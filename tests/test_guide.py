"""End-to-end tests for the beginner entrypoint."""
import json
import os
from pathlib import Path
import shutil
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
KIND = ROOT.name

def run(*args, stdin=""):
    return subprocess.run([sys.executable, "-X", "utf8", str(ROOT/"start.py"), *map(str,args)],
        input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=120,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE":"1"})

def test_check_and_eof():
    result = run("--check")
    assert result.returncode == 0, result.stderr
    assert "Python" in result.stdout
    assert run().returncode == 0

def test_check_without_site_packages():
    result = subprocess.run([sys.executable, "-S", "-X", "utf8", str(ROOT/"start.py"), "--check"],
        capture_output=True, text=True, encoding="utf-8")
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "依赖" in result.stdout + result.stderr

def test_missing_inputs_do_not_create_output(tmp_path):
    out = tmp_path/"不应 创建"
    result = run("--run", "--input-dir", tmp_path, "--output", out)
    assert result.returncode != 0
    assert not out.exists()
    assert "缺少" in result.stderr
    assert "Traceback" not in result.stderr

def test_demo_real_route_and_collision(tmp_path):
    demo = tmp_path/"中文 示例"
    result = run("--demo", "--output", demo)
    assert result.returncode == 0, result.stderr
    note = (demo/"使用结果.md").read_text(encoding="utf-8")
    assert "合成" in note
    assert "report.json" in note
    before = {str(p.relative_to(demo)):p.read_bytes() for p in demo.rglob("*") if p.is_file()}
    assert run("--demo", "--output", demo).returncode != 0
    assert before == {str(p.relative_to(demo)):p.read_bytes() for p in demo.rglob("*") if p.is_file()}
    source = ROOT/"examples"/"synthetic" if KIND.startswith("cross-") else demo
    inputs = tmp_path/"用户 输入"
    inputs.mkdir()
    names = ("suite.json","manifest.json","versions.json","runs.jsonl") if KIND.startswith("prompt-") else ("outcomes.csv","features.csv","config.json") if KIND.startswith("cta-") else ("domestic.csv","events.csv","fx.csv","config.json")
    for name in names:
        shutil.copyfile(source/name, inputs/name)
    (inputs/"state.json").write_text('{"active_version":"unchanged"}',encoding="utf-8")
    output = tmp_path/"用户 输出"
    result = run("--run", "--input-dir", inputs, "--output", output)
    assert result.returncode == 0, result.stderr
    note = (output/"使用结果.md").read_text(encoding="utf-8")
    assert "用户提供" in note and "不" in note
    assert json.loads((inputs/"state.json").read_text())["active_version"] == "unchanged"
    report = json.loads((output/"report.json").read_text(encoding="utf-8"))
    if KIND.startswith("prompt-"):
        assert report["split"] == "holdout"
        assert ("候选是否通过放行条件：通过" if report["accepted"] else "候选是否通过放行条件：未通过") in note
        assert f'{report["paired_task_pass_rate_delta"] * 100:+.2f} 个百分点' in note
    else:
        assert "累计收益" in note and "最大回撤" in note and "夏普" in note
        assert "```json" not in note
        rows = [line for line in note.splitlines() if line.startswith("| ") and line.split("|")[1].strip().isdigit()]
        assert len(rows) == len(report["metrics"])
        for metric in report["metrics"]:
            assert f'{metric["total_return"]:.2%}' in note
            assert f'{metric["max_drawdown"]:.2%}' in note
    if KIND.startswith("cross-"):
        assert (demo/"summary"/"summary.json").is_file()

def test_invalid_data_hides_payload(tmp_path):
    inputs = tmp_path/"input"
    inputs.mkdir()
    names = ("suite.json","manifest.json","versions.json","runs.jsonl") if KIND.startswith("prompt-") else ("outcomes.csv","features.csv","config.json") if KIND.startswith("cta-") else ("domestic.csv","events.csv","fx.csv","config.json")
    for name in names:
        (inputs/name).write_text("SECRET-DO-NOT-ECHO",encoding="utf-8")
    result = run("--run","--input-dir",inputs,"--output",tmp_path/"out")
    assert result.returncode != 0
    assert "SECRET-DO-NOT-ECHO" not in result.stdout + result.stderr
    assert "分析未完成" in result.stderr
    assert "Traceback" not in result.stderr


def test_invalid_arguments_hide_payload():
    result = run("--unknown-secret=SECRET-DO-NOT-ECHO")
    assert result.returncode != 0
    assert "SECRET-DO-NOT-ECHO" not in result.stdout + result.stderr
    assert "参数" in result.stderr


def test_menu_can_check_then_exit():
    result = run(stdin="1\n0\n")
    assert result.returncode == 0
    assert "Python" in result.stdout


def test_summary_handles_unavailable_values_in_chinese(tmp_path):
    report = {"accepted": False, "recommended_version": None, "n_tasks": 2,
              "paired_task_pass_rate_delta": -0.125,
              "status": "synthetic_fixture_only", "alignment_coverage": {"test": 0.5},
              "metrics": [{"scope": "known_future", "scheme": "unified", "cost_multiplier": 2,
                           "days": 10, "windows": 10, "coverage_min": 1, "coverage": 0.5,
                           "total_return": -0.0125, "max_drawdown": -0.025,
                           "sharpe": None, "sharpe_per_252_windows": None}]}
    path = tmp_path/"report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    runpy.run_path(str(ROOT/"scripts"/"guide.py"))["summary"](tmp_path, path, False)
    note = (tmp_path/"使用结果.md").read_text(encoding="utf-8")
    assert "None" not in note and "null" not in note and "false" not in note
    if KIND.startswith("prompt-"):
        assert "未通过" in note and "暂无可放行版本" in note
        assert "-12.50 个百分点" in note
    else:
        assert "-1.25%" in note and "-2.50%" in note
        assert "无法计算" in note

