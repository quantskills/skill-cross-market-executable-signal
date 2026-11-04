import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from analyze import analyze


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts/summarize_study.py"


def helper():
    assert SCRIPT.is_file(), "study summary implementation is missing"
    spec = importlib.util.spec_from_file_location("study_summary", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def example():
    folder = ROOT / "examples/synthetic"
    d = pd.read_csv(folder / "domestic.csv", dtype={"window_id": "string"}, keep_default_na=False)
    e = pd.read_csv(folder / "events.csv", dtype={"event_id": "string"}, keep_default_na=False)
    f = pd.read_csv(folder / "fx.csv", dtype={"fx_id": "string"}, keep_default_na=False)
    cfg = json.loads((folder / "config.json").read_text(encoding="utf-8"))
    report, signals = analyze(d, e, f, cfg)
    plan = {"evaluation_years": [2024, 2025], "report_timezone": "Asia/Shanghai",
            "bootstrap_block_windows": [10, 20, 40], "bootstrap_repeats": 100, "seed": 42}
    return report, signals, plan


def test_yearly_counts_costs_and_missing_year_are_not_fabricated():
    report, signals, plan = example()
    result = helper().summarize(report, signals, plan)
    assert result["status"] == "synthetic_fixture_only"
    assert result["calendar_independently_verified"] is False
    assert result["time_source_independently_verified"] is False
    assert result["executable"] is False
    assert len(result["yearly"]) == 30
    first = next(r for r in result["yearly"] if r["year"] == 2024 and r["scheme"] == "aligned" and r["cost_multiplier"] == 2)
    rows = signals[signals.scheme == "aligned"]
    net = rows.gross_return.to_numpy() - 2 * (rows.fee_return.to_numpy() + rows.slippage_return.to_numpy())
    assert first["windows"] == 34
    assert first["mean_return_per_window"] == pytest.approx(net.mean(), abs=1e-14)
    assert first["total_return"] == pytest.approx(np.prod(1 + net) - 1, abs=1e-14)
    for row in result["yearly"]:
        if row["year"] == 2025:
            assert row["windows"] == 0 and row["coverage"] is None
            assert row["total_return"] is None and row["sharpe_per_252_windows"] is None
    json.dumps(result, allow_nan=False)


def test_bootstrap_is_paired_reproducible_and_does_not_shorten_large_blocks():
    module = helper()
    delta = np.array([-.01, .02, .03, -.02, .01, .04])
    actual = module.moving_block_interval(delta, 2, 100, 42)
    rng = np.random.default_rng(42)
    means = []
    for _ in range(100):
        starts = rng.integers(0, len(delta) - 2 + 1, size=3)
        sample = np.concatenate([delta[s:s + 2] for s in starts])[:len(delta)]
        means.append(float(sample.mean()))
    assert actual["mean_difference"] == pytest.approx(delta.mean())
    assert [actual["ci_low"], actual["ci_high"]] == pytest.approx(np.quantile(means, [.025, .975]))
    assert actual == module.moving_block_interval(delta, 2, 100, 42)
    constant = module.moving_block_interval(np.full(34, .001), 10, 100, 42)
    assert constant["ci_low"] == pytest.approx(.001)
    assert constant["ci_high"] == pytest.approx(.001)
    too_long = module.moving_block_interval(delta, 10, 100, 42)
    assert too_long["status"] == "insufficient_windows"
    assert too_long["ci_low"] is None and too_long["block_windows"] == 10


def test_paired_comparisons_include_all_zero_windows_and_two_baselines():
    report, signals, plan = example()
    result = helper().summarize(report, signals, plan)
    assert len(result["paired_intervals"]) == 18
    for row in result["paired_intervals"]:
        assert row["windows"] == 34
        assert row["candidate"] == "aligned"
        assert row["baseline"] in {"domestic_lag_baseline", "domestic_lag_matched_baseline"}
        left = signals[signals.scheme == "aligned"]
        right = signals[signals.scheme == row["baseline"]]
        cost = row["cost_multiplier"]
        delta = (left.gross_return.to_numpy() - cost * (left.fee_return.to_numpy() + left.slippage_return.to_numpy())
                 - right.gross_return.to_numpy() + cost * (right.fee_return.to_numpy() + right.slippage_return.to_numpy()))
        assert row["mean_difference"] == pytest.approx(delta.mean(), abs=1e-14)


def test_source_reuse_counts_only_matched_windows_and_preserves_text_ids():
    report, signals, plan = example()
    result = helper().summarize(report, signals, plan)
    rows = result["source_reuse"]
    assert not any(r["scheme"].startswith("domestic_lag_") for r in rows)
    first = next(r for r in rows if r["scheme"] == "aligned" and r["source_kind"] == "external")
    assert first["source_id"] == "9007199254741085"
    fx = next(r for r in rows if r["scheme"] == "aligned" and r["source_kind"] == "fx")
    assert fx["source_id"] == "000046"
    placebo = [r for r in rows if r["scheme"] == "future_misalignment_placebo" and r["source_kind"] == "external"]
    assert sum(r["used_windows"] for r in placebo) == 33
    assert all(r["age_hours_min"] < 0 for r in placebo)


@pytest.mark.parametrize("mutation", ["drop", "duplicate", "label", "gross", "net", "fee", "matched", "position", "decision", "id", "nan"])
def test_invalid_details_are_rejected_instead_of_silently_repaired(mutation):
    report, signals, plan = example()
    signals = signals.copy()
    if mutation == "drop":
        signals = signals.drop(index=0)
    elif mutation == "duplicate":
        signals = pd.concat([signals, signals.iloc[[0]]], ignore_index=True)
    elif mutation == "label":
        signals.loc[0, "asset_return"] += .01
    elif mutation in {"gross", "net", "fee"}:
        signals.loc[0, {"gross": "gross_return", "net": "net_return", "fee": "fee_return"}[mutation]] += .01
    elif mutation == "matched":
        signals["matched"] = "False"
    elif mutation == "position":
        signals.loc[0, "position"] = 2
    elif mutation == "decision":
        signals.loc[0, "decision_at"] = signals.loc[0, "exit_at"]
    elif mutation == "id":
        signals.loc[0, "window_id"] = ""
    else:
        signals.loc[0, "net_return"] = np.inf
    with pytest.raises(ValueError):
        helper().summarize(report, signals, plan)


@pytest.mark.parametrize("mutation", ["metric", "coverage", "missing_metric", "duplicate_metric", "source_status", "source_verified", "executable"])
def test_corrupt_report_is_rejected(mutation):
    report, signals, plan = example()
    report = copy.deepcopy(report)
    if mutation == "metric":
        report["metrics"][0]["total_return"] += .01
    elif mutation == "coverage":
        report["metrics"][0]["coverage"] = .2
    elif mutation == "missing_metric":
        report["metrics"].pop()
    elif mutation == "duplicate_metric":
        report["metrics"].append(report["metrics"][0])
    elif mutation == "source_status":
        report["status"] = "real_market_verified"
    elif mutation == "source_verified":
        report["time_source_independently_verified"] = True
    else:
        report["metrics"][0]["executable"] = True
    with pytest.raises(ValueError):
        helper().summarize(report, signals, plan)


@pytest.mark.parametrize("field,value", [
    ("evaluation_years", [2025]), ("evaluation_years", [2025, 2024]),
    ("evaluation_years", [2024, 2024]), ("report_timezone", "not/a/timezone"),
    ("bootstrap_block_windows", [0]), ("bootstrap_block_windows", [10.5]),
    ("bootstrap_repeats", True), ("seed", -1),
])
def test_bad_or_selective_plan_is_rejected(field, value):
    report, signals, plan = example()
    plan[field] = value
    with pytest.raises(ValueError):
        helper().summarize(report, signals, plan)


def test_cli_writes_hashed_outputs_and_refuses_overwrite(tmp_path):
    assert SCRIPT.is_file(), "study summary implementation is missing"
    report, signals, plan = example()
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    signals.to_csv(tmp_path / "signals.csv", index=False)
    (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    output = tmp_path / "summary"
    cmd = [sys.executable, "-X", "utf8", str(SCRIPT), "--report", str(tmp_path / "report.json"),
           "--signals", str(tmp_path / "signals.csv"), "--plan", str(tmp_path / "plan.json"), "--output", str(output)]
    process = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert process.returncode == 0, process.stderr
    result = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert set(result["input_hashes"]) == {"report", "signals", "plan"}
    markdown = (output / "REPORT.md").read_text(encoding="utf-8")
    for fingerprint in result["input_hashes"].values():
        assert fingerprint in markdown
    assert json.dumps(plan, ensure_ascii=False, indent=2) in markdown
    years = pd.read_csv(output / "yearly.csv")
    assert len(years) == 30
    assert years.loc[years.year == 2025, "total_return"].isna().all()
    intervals = pd.read_csv(output / "paired_intervals.csv")
    assert len(intervals) == 18
    oversized = intervals[intervals.block_windows == 40]
    assert len(oversized) == 6
    assert oversized[["ci_low", "ci_high"]].isna().all().all()
    assert set(oversized.status) == {"insufficient_windows"}
    assert {p.name for p in output.iterdir()} == {"summary.json", "yearly.csv", "source_reuse.csv", "paired_intervals.csv", "REPORT.md"}
    reused = pd.read_csv(output / "source_reuse.csv", dtype={"source_id": "string"}, keep_default_na=False)
    assert "000046" in reused.source_id.to_list()
    before = (output / "summary.json").read_bytes()
    repeated = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert repeated.returncode != 0
    assert (output / "summary.json").read_bytes() == before


def test_plan_cannot_hide_empty_tail_years_in_declared_evaluation_scope():
    report, signals, plan = example()
    report["config"]["test_end"] = "2025-12-31T23:59:59+08:00"
    plan["evaluation_years"] = [2024]
    with pytest.raises(ValueError):
        helper().summarize(report, signals, plan)


def test_source_id_timestamp_mapping_is_consistent_across_schemes():
    report, signals, plan = example()
    aligned = signals[signals.scheme == "aligned"].iloc[0]
    future_index = signals.index[signals.scheme == "future_misalignment_placebo"][0]
    signals.loc[future_index, "used_event_id"] = aligned.used_event_id
    with pytest.raises(ValueError):
        helper().summarize(report, signals, plan)


def test_training_window_count_cannot_change_between_cost_multipliers():
    report, signals, plan = example()
    report["metrics"][1]["training_windows"] += 1
    with pytest.raises(ValueError):
        helper().summarize(report, signals, plan)


def test_year_grouping_uses_frozen_reporting_timezone_not_utc():
    report, signals, plan = example()
    target = pd.Timestamp("2023-12-31T16:01:00Z")
    offset = target - signals.entry_at.iloc[0]
    time_cols = ["decision_at", "entry_at", "exit_at", "local_lag_available_at",
                 "external_available_at", "fx_available_at", "used_external_available_at", "used_fx_available_at"]
    for col in time_cols:
        signals[col] = pd.to_datetime(signals[col], utc=True) + offset
    report["config"]["train_end"] = (pd.Timestamp(report["config"]["train_end"]) + offset).isoformat()
    plan["evaluation_years"] = [2023, 2024]
    result = helper().summarize(report, signals, plan)
    assert {r["windows"] for r in result["yearly"] if r["year"] == 2023} == {0}
    assert {r["windows"] for r in result["yearly"] if r["year"] == 2024} == {34}


def test_plan_cannot_hide_empty_head_years_in_declared_evaluation_scope():
    report, signals, plan = example()
    report["config"]["train_end"] = "2022-12-31T23:59:59+08:00"
    report["config"]["test_end"] = "2025-12-31T23:59:59+08:00"
    with pytest.raises(ValueError):
        helper().summarize(report, signals, plan)


def test_same_source_identifier_cannot_have_different_feature_values():
    report, signals, plan = example()
    index = signals.index[signals.scheme == "aligned"][0]
    signals.loc[index, "used_external_return"] += .01
    with pytest.raises(ValueError):
        helper().summarize(report, signals, plan)


def test_boolean_is_not_a_valid_cost_multiplier():
    report, signals, plan = example()
    report["metrics"][0]["cost_multiplier"] = True
    with pytest.raises(ValueError):
        helper().summarize(report, signals, plan)


def test_source_feature_values_cannot_be_booleans():
    report, signals, plan = example()
    for col in ("used_external_return", "used_fx_return"):
        signals[col] = signals[col].astype(object)
        mask = signals.matched & ~signals.scheme.str.startswith("domestic_lag_")
        signals.loc[mask, col] = True
    with pytest.raises(ValueError):
        helper().summarize(report, signals, plan)


def test_domestic_baseline_cannot_claim_source_feature_values():
    report, signals, plan = example()
    signals.loc[0, "used_external_return"] = .01
    with pytest.raises(ValueError):
        helper().summarize(report, signals, plan)


def test_different_ids_cannot_represent_one_source_timestamp():
    report, signals, plan = example()
    index = signals.index[signals.scheme == "aligned"][0]
    signals.loc[index, "used_event_id"] = "alias-for-existing-event"
    with pytest.raises(ValueError):
        helper().summarize(report, signals, plan)


def test_cli_hashes_and_parses_same_snapshot_if_input_changes_after_read(tmp_path, monkeypatch):
    module = helper()
    report, signals, plan = example()
    report_path, signals_path, plan_path = [tmp_path / n for n in ("report.json", "signals.csv", "plan.json")]
    report_path.write_text(json.dumps(report), encoding="utf-8")
    signals.to_csv(signals_path, index=False)
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    original_read = Path.read_bytes
    expected_hash = hashlib.sha256(original_read(signals_path)).hexdigest()

    def change_after_read(path):
        data = original_read(path)
        if path == signals_path:
            signals_path.write_text("replaced,after,first,read\n", encoding="utf-8")
        return data

    monkeypatch.setattr(Path, "read_bytes", change_after_read)
    output = tmp_path / "snapshot-output"
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--report", str(report_path), "--signals", str(signals_path),
                                     "--plan", str(plan_path), "--output", str(output)])
    module.main()
    result = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert result["input_hashes"]["signals"] == expected_hash
    assert all(r["windows"] == 34 for r in result["paired_intervals"])


def test_source_reuse_is_counted_without_treating_windows_as_releases():
    folder = ROOT / "examples/synthetic"
    d = pd.read_csv(folder / "domestic.csv", dtype={"window_id": "string"}, keep_default_na=False)
    e = pd.read_csv(folder / "events.csv", dtype={"event_id": "string"}, keep_default_na=False).iloc[::4]
    f = pd.read_csv(folder / "fx.csv", dtype={"fx_id": "string"}, keep_default_na=False)
    cfg = json.loads((folder / "config.json").read_text(encoding="utf-8"))
    report, signals = analyze(d, e, f, cfg)
    _, _, plan = example()
    result = helper().summarize(report, signals, plan)
    row = next(r for r in result["source_counts"] if r["scheme"] == "aligned" and r["source_kind"] == "external")
    assert row["matched_uses"] == 34
    assert row["distinct_source_ids"] == 9
    assert row["reused_uses"] == 25 and row["max_windows_per_source"] == 4


def test_existing_analyzer_equal_decision_entry_contract_is_supported():
    folder = ROOT / "examples/synthetic"
    d = pd.read_csv(folder / "domestic.csv", dtype={"window_id": "string"}, keep_default_na=False)
    e = pd.read_csv(folder / "events.csv", dtype={"event_id": "string"}, keep_default_na=False)
    f = pd.read_csv(folder / "fx.csv", dtype={"fx_id": "string"}, keep_default_na=False)
    d["decision_at"] = d["entry_at"]
    cfg = json.loads((folder / "config.json").read_text(encoding="utf-8"))
    report, signals = analyze(d, e, f, cfg)
    _, _, plan = example()
    result = helper().summarize(report, signals, plan)
    assert all(r["windows"] == 34 for r in result["paired_intervals"])
    assert result["executable"] is False


@pytest.mark.parametrize("mutation", ["missing", "infinite"])
def test_missing_or_nonfinite_actual_source_values_are_rejected(mutation):
    report, signals, plan = example()
    if mutation == "missing":
        signals = signals.drop(columns=["used_external_return", "used_fx_return"])
    else:
        signals.loc[signals.scheme == "aligned", "used_external_return"] = np.inf
    with pytest.raises(ValueError):
        helper().summarize(report, signals, plan)


def test_zero_foreign_test_coverage_is_not_dropped_or_filled_with_prices():
    folder = ROOT / "examples/synthetic"
    d = pd.read_csv(folder / "domestic.csv", dtype={"window_id": "string"}, keep_default_na=False)
    e = pd.read_csv(folder / "events.csv", dtype={"event_id": "string"}, keep_default_na=False).iloc[:30]
    f = pd.read_csv(folder / "fx.csv", dtype={"fx_id": "string"}, keep_default_na=False)
    cfg = json.loads((folder / "config.json").read_text(encoding="utf-8"))
    report, signals = analyze(d, e, f, cfg)
    _, _, plan = example()
    result = helper().summarize(report, signals, plan)
    aligned = [r for r in result["yearly"] if r["scheme"] == "aligned" and r["year"] == 2024]
    assert all(r["windows"] == 34 and r["coverage"] == 0 and r["zero_position_windows"] == 34 for r in aligned)
    assert result["source_reuse"] == []
    assert all(r["distinct_source_ids"] == 0 for r in result["source_counts"])


def test_decision_order_must_agree_with_nonoverlapping_execution_order():
    report, signals, plan = example()
    decision = signals.decision_at.iloc[0] - pd.Timedelta(minutes=1)
    source_cols = ["used_event_id", "used_fx_id", "used_external_available_at", "used_fx_available_at",
                   "used_external_return", "used_fx_return"]
    for scheme in signals.scheme.unique():
        rows = signals[signals.scheme == scheme]
        index = rows.index[1]
        signals.loc[index, "decision_at"] = decision
        signals.loc[index, "local_lag_available_at"] = decision - pd.Timedelta(minutes=1)
        if not scheme.startswith("domestic_lag_"):
            for col in source_cols:
                signals.loc[index, col] = rows.iloc[0][col]
            for col, source in (("external_age_hours", "used_external_available_at"), ("fx_age_hours", "used_fx_available_at")):
                signals.loc[index, col] = (decision - pd.Timestamp(signals.loc[index, source])).total_seconds() / 3600
    with pytest.raises(ValueError):
        helper().summarize(report, signals, plan)


def test_extra_lag_cannot_use_current_window_sources():
    report, signals, plan = example()
    aligned = signals[signals.scheme == "aligned"].reset_index(drop=True)
    lag_indices = signals.index[signals.scheme == "extra_session_lag"]
    columns = ["used_event_id", "used_fx_id", "used_external_available_at",
               "used_fx_available_at", "used_external_return", "used_fx_return",
               "external_age_hours", "fx_age_hours"]
    differing = [i for i in range(1, len(aligned))
                 if signals.loc[lag_indices[i], "matched"]
                 and aligned.loc[i, "used_event_id"] != aligned.loc[i - 1, "used_event_id"]]
    assert differing, "fixture must contain a matched source-change boundary"
    position = differing[0]
    index = lag_indices[position]
    for column in columns:
        signals.loc[index, column] = aligned.loc[position, column]
    with pytest.raises(ValueError, match="滞后来源") as error:
        helper().summarize(report, signals, plan)
    assert aligned.loc[position, "window_id"] in str(error.value)


def test_external_csv_row_order_does_not_change_summary():
    report, signals, plan = example()
    module = helper()
    expected = module.summarize(report, signals, plan)
    shuffled = signals.sample(frac=1, random_state=17).reset_index(drop=True)
    assert module.summarize(report, shuffled, plan) == expected
