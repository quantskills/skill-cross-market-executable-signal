import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def start(*args):
    # Site packages are intentionally disabled. The fill-in check is offline
    # and must work before dependencies are installed.
    return subprocess.run(
        [sys.executable, "-S", "-X", "utf8", str(ROOT / "start.py"), *map(str, args)],
        cwd=ROOT, text=True, encoding="utf-8", capture_output=True, timeout=30,
    )


def test_complete_example_passes_check_without_writing(tmp_path):
    source = tmp_path / "示例 输入"
    shutil.copytree(ROOT / "examples" / "synthetic", source)
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    result = start("--check-input", "--input-dir", source)
    assert result.returncode == 0, result.stderr
    assert "基础检查通过" in result.stdout
    assert "不代表" in result.stdout
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before


def test_blank_template_names_each_table_and_unfilled_settings():
    result = start("--check-input", "--input-dir", ROOT / "templates" / "input")
    assert result.returncode == 1
    for name in ("domestic.csv", "events.csv", "fx.csv", "config.json"):
        assert name in result.stderr
    assert "只有表头" in result.stderr
    for key in ("train_end", "fee_rate", "slippage_rate"):
        assert key in result.stderr


@pytest.mark.parametrize("mode", ["--check-input", "--run"])
def test_missing_file_column_and_value_are_reported_together(tmp_path, mode):
    source = tmp_path / "in"
    shutil.copytree(ROOT / "examples" / "synthetic", source)
    (source / "fx.csv").unlink()
    (source / "events.csv").write_text(
        "event_id,external_return\nSECRET-DO-NOT-ECHO,0.01\n", encoding="utf-8"
    )
    domestic = source / "domestic.csv"
    header, row, *_ = domestic.read_text(encoding="utf-8").splitlines()
    cells = row.split(",")
    cells[header.split(",").index("local_lag_available_at")] = ""
    domestic.write_text(header + "\n" + ",".join(cells) + "\n", encoding="utf-8")
    output = tmp_path / "no-results"
    args = ["--output", output] if mode == "--run" else []
    result = start(mode, "--input-dir", source, *args)
    assert result.returncode == 1
    assert "fx.csv" in result.stderr and "缺少" in result.stderr
    assert "events.csv" in result.stderr and "available_at" in result.stderr
    assert "domestic.csv" in result.stderr and "第 2 行" in result.stderr
    assert "local_lag_available_at" in result.stderr
    assert "SECRET-DO-NOT-ECHO" not in result.stdout + result.stderr
    assert not output.exists()


def test_check_rejects_output_argument(tmp_path):
    result = start("--check-input", "--input-dir", ROOT / "examples" / "synthetic", "--output", tmp_path / "out")
    assert result.returncode != 0
    assert "不需要 --output" in result.stderr
    assert not (tmp_path / "out").exists()
