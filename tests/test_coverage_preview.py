import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "synthetic"


def run(source, output, mode="--preview-coverage"):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "start.py"), mode,
         "--input-dir", str(source), "--output", str(output)],
        text=True, encoding="utf-8", capture_output=True, timeout=120,
    )


def read_gaps(output):
    with (output / "覆盖缺口.csv").open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def test_complete_preview_counts_both_periods_without_running_a_study(tmp_path):
    output = tmp_path / "预览结果"
    result = run(EXAMPLE, output)
    assert result.returncode == 0, result.stderr
    summary = (output / "覆盖预览.md").read_text(encoding="utf-8")
    assert "| 训练期 | 46 | 46 | 0 | 100.00% |" in summary
    assert "| 检验期 | 34 | 34 | 0 | 100.00% |" in summary
    assert read_gaps(output) == []
    assert {p.name for p in output.iterdir()} == {"覆盖预览.md", "覆盖缺口.csv"}
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    assert run(EXAMPLE, output).returncode != 0
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before


def test_preview_distinguishes_no_prior_record_stale_and_both(tmp_path):
    source = tmp_path / "in"
    shutil.copytree(EXAMPLE, source)
    config = source / "config.json"
    cfg = json.loads(config.read_text(encoding="utf-8"))
    cfg["max_age_hours"] = 25
    config.write_text(json.dumps(cfg), encoding="utf-8")
    for name, dates in (
        ("events.csv", {"2024-01-01", "2024-02-16", "2024-02-17", "2024-02-18"}),
        ("fx.csv", {"2024-02-17", "2024-02-18", "2024-02-19"}),
    ):
        path = source / name
        header, *rows = path.read_text(encoding="utf-8").splitlines()
        kept = [row for row in rows if row.split(",")[1][:10] not in dates]
        path.write_text("\n".join([header, *kept]) + "\n", encoding="utf-8")
    output = tmp_path / "out"
    result = run(source, output)
    assert result.returncode == 0, result.stderr
    summary = (output / "覆盖预览.md").read_text(encoding="utf-8")
    assert "| 训练期 | 46 | 45 | 1 | 97.83% |" in summary
    assert "| 检验期 | 34 | 31 | 3 | 91.18% |" in summary
    gaps = read_gaps(output)
    assert [(g["决策日期"], g["外盘状态"], g["汇率状态"]) for g in gaps] == [
        ("2024-01-01", "截至决策时无记录", "可用"),
        ("2024-02-17", "超过时限", "可用"),
        ("2024-02-18", "超过时限", "超过时限"),
        ("2024-02-19", "可用", "超过时限"),
    ]
    assert gaps[0]["最近外盘可用时刻（UTC）"] == ""
    assert gaps[1]["最近外盘可用时刻（UTC）"] == "2024-02-15T00:00:00+00:00"
    research = tmp_path / "research"
    result = run(source, research, mode="--run")
    assert result.returncode == 0, result.stderr
    report = json.loads((research / "report.json").read_text(encoding="utf-8"))
    assert report["alignment_coverage"] == {"train": 45 / 46, "test": 31 / 34}
    with (research / "signals.csv").open(encoding="utf-8", newline="") as stream:
        unmatched = [row["decision_at"][:10] for row in csv.DictReader(stream)
                     if row["scheme"] == "aligned" and row["matched"] == "False"]
    assert unmatched == [g["决策日期"] for g in gaps if g["区间"] == "检验期"]


def test_preview_reports_small_scope_without_training(tmp_path):
    source = tmp_path / "in"
    shutil.copytree(EXAMPLE, source)
    config = source / "config.json"
    cfg = json.loads(config.read_text(encoding="utf-8"))
    cfg.update(train_start="2024-02-14T04:00:00Z", test_end="2024-02-17T04:00:00Z")
    config.write_text(json.dumps(cfg), encoding="utf-8")
    output = tmp_path / "out"
    result = run(source, output)
    assert result.returncode == 0, result.stderr
    summary = (output / "覆盖预览.md").read_text(encoding="utf-8")
    assert "| 训练期 | 1 | 1 | 0 | 100.00% |" in summary
    assert "| 检验期 | 1 | 1 | 0 | 100.00% |" in summary
    assert "跨区间边界 2" in summary
    assert "范围外 76" in summary
    assert "不足 20" in summary and "不足 10" in summary


def test_preview_missing_fx_file_does_not_create_output(tmp_path):
    source = tmp_path / "in"
    shutil.copytree(EXAMPLE, source)
    (source / "fx.csv").unlink()
    output = tmp_path / "out"
    result = run(source, output)
    assert result.returncode == 1
    assert "fx.csv 缺少文件" in result.stderr
    assert not output.exists()
