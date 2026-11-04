import numpy as np
import pandas as pd
import pytest
import json
import subprocess
import sys
from pathlib import Path

from analyze import align, analyze, fit_predict, performance


def fixture():
    days = pd.date_range("2024-01-01", periods=80, tz="UTC")
    signal = np.sin(np.arange(80))*.01
    d = pd.DataFrame({"window_id": [f"w{i}" for i in range(80)], "decision_at":days+pd.Timedelta(hours=1),
                      "entry_at":days+pd.Timedelta(hours=1,minutes=1), "exit_at":days+pd.Timedelta(hours=7),
                      "asset_return":signal*.3, "local_lag_return":np.r_[0,signal[:-1]*.3]})
    e = pd.DataFrame({"event_id": [f"e{i}" for i in range(80)], "available_at":days, "external_return":signal})
    f = pd.DataFrame({"fx_id": [f"f{i}" for i in range(80)], "available_at":days+pd.Timedelta(minutes=30), "fx_return":signal*.01})
    for table in (d,e,f):
        table["available_at_basis"] = "synthetic"
    d["local_lag_available_at"] = d.decision_at-pd.Timedelta(minutes=1)
    cfg = {"train_end":"2024-02-15T23:59:00Z", "fee_rate":.0001, "slippage_rate":.0001}
    return d, e, f, cfg


def test_information_join_and_cost_stress():
    d,e,f,cfg = fixture(); joined=align(d,e,f,96)
    assert (joined.external_available_at <= joined.decision_at).all()
    report, details=analyze(d,e,f,cfg)
    selected=[r for r in report["metrics"] if r["scheme"]=="aligned"]
    assert selected[0]["total_return"] > selected[-1]["total_return"]
    assert not [r for r in report["metrics"] if r["scheme"].endswith("placebo")][0]["executable"]


def test_future_events_are_unavailable_and_missing_coverage_is_kept():
    d,e,f,cfg = fixture(); e.loc[0,"available_at"] = e.loc[0,"available_at"]+pd.Timedelta(hours=2)
    joined=align(d,e,f,1)
    assert pd.isna(joined.loc[0,"event_id"])
    assert len(joined)==len(d)


def test_naive_time_and_overlapping_windows_rejected():
    d,e,f,cfg = fixture(); d["decision_at"]=d.decision_at.dt.tz_localize(None)
    with pytest.raises(ValueError,match="时区"):
        align(d,e,f,96)
    d,e,f,cfg = fixture(); d.loc[1,"entry_at"]=d.loc[0,"entry_at"]
    with pytest.raises(ValueError):
        align(d,e,f,96)


def test_post_decision_changes_do_not_rewrite_past_alignment():
    d,e,f,cfg=fixture(); before=align(d,e,f,96)
    e.loc[e.available_at>d.decision_at.iloc[40],"external_return"]=99
    after=align(d,e,f,96)
    assert before.external_return.iloc[:41].equals(after.external_return.iloc[:41])


def test_timestamp_basis_is_required_and_assumptions_are_not_executable():
    d,e,f,cfg=fixture()
    with pytest.raises(ValueError,match="时间来源"):
        analyze(d,e.drop(columns="available_at_basis"),f,cfg)
    e["available_at_basis"]="assumed"
    report,_=analyze(d,e,f,cfg)
    assert report["status"]=="hypothesis_only"
    assert not any(row["executable"] for row in report["metrics"])


def test_synthetic_fixture_is_never_platform_market_evidence():
    d,e,f,cfg=fixture()
    report,_=analyze(d,e,f,cfg)
    assert report["status"]=="synthetic_fixture_only"
    assert not any(row["executable"] for row in report["metrics"])


def test_domestic_lag_available_after_decision_is_rejected():
    d,e,f,cfg=fixture()
    d.loc[10,"local_lag_available_at"]=d.loc[10,"decision_at"]+pd.Timedelta(seconds=1)
    with pytest.raises(ValueError,match="国内滞后"):
        analyze(d,e,f,cfg)


def test_actual_cli_writes_timestamp_audit(tmp_path):
    d,e,f,cfg=fixture()
    for name,frame in (("domestic",d),("events",e),("fx",f)):
        frame.to_csv(tmp_path/(name+".csv"),index=False)
    (tmp_path/"config.json").write_text(json.dumps(cfg))
    args=[sys.executable,"-X","utf8",str(Path(__file__).parents[1]/"scripts"/"analyze.py")]
    for name in ("domestic","events","fx","config"):
        args.extend(["--"+name,str(tmp_path/(name+(".json" if name=="config" else ".csv")))])
    args.extend(["--output",str(tmp_path/"result")])
    proc=subprocess.run(args,capture_output=True,text=True,encoding="utf-8")
    assert proc.returncode==0,proc.stderr
    signals=pd.read_csv(tmp_path/"result"/"signals.csv")
    assert {"aligned_event_id","external_available_at","entry_at"}<=set(signals)
    expected = {"domestic_lag_baseline", "domestic_lag_matched_baseline", "aligned",
                "extra_session_lag", "future_misalignment_placebo"}
    assert set(signals.scheme) == expected
    report = json.loads((tmp_path/"result"/"report.json").read_text(encoding="utf-8"))
    assert len(report["metrics"]) == 15
    assert not report["time_source_independently_verified"]
    assert not any(m["executable"] or m["time_causal_candidate"] for m in report["metrics"])
    assert_cost_identities(signals, cfg)
    for scheme, event_id, fx_id in (("aligned", "e46", "f46"),
                                    ("extra_session_lag", "e45", "f45"),
                                    ("future_misalignment_placebo", "e47", "f46")):
        row = signals[(signals.scheme == scheme) & (signals.window_id == "w46")].iloc[0]
        assert row.used_event_id == event_id
        assert row.used_fx_id == fx_id
    before = {name: (tmp_path/"result"/name).read_bytes() for name in ("report.json", "signals.csv")}
    again = subprocess.run(args,capture_output=True,text=True,encoding="utf-8")
    assert again.returncode != 0
    assert "拒绝覆盖" in again.stderr
    assert all((tmp_path/"result"/name).read_bytes() == content for name, content in before.items())


def scheme_rows(details, name):
    return details[details.scheme == name].set_index("window_id")


def assert_cost_identities(details, cfg):
    np.testing.assert_allclose(details.gross_return, details.position * details.asset_return)
    np.testing.assert_allclose(details.fee_return, 2 * details.position.abs() * cfg["fee_rate"])
    np.testing.assert_allclose(details.slippage_return, 2 * details.position.abs() * cfg["slippage_rate"])
    np.testing.assert_allclose(details.net_return,
                               details.gross_return - details.fee_return - details.slippage_return)


def test_notional_scale_changes_positions_and_each_window_cost_not_predictions():
    d,e,f,cfg = fixture()
    default_report, default = analyze(d,e,f,cfg)
    full_report, full = analyze(d,e,f,{**cfg, "notional_scale": 1})
    quarter_report, quarter = analyze(d,e,f,{**cfg, "notional_scale": .25})
    pd.testing.assert_frame_equal(default, full)
    np.testing.assert_allclose(quarter.prediction, full.prediction)
    for col in ("position", "gross_return", "fee_return", "slippage_return", "net_return"):
        np.testing.assert_allclose(quarter[col], full[col] * .25)
    assert_cost_identities(full,cfg)
    assert_cost_identities(quarter,cfg)
    for metric, small_metric in zip(full_report["metrics"], quarter_report["metrics"]):
        assert metric["scheme"] == small_metric["scheme"]
        assert small_metric["mean_return_per_window"] == pytest.approx(metric["mean_return_per_window"] * .25)
        rows = scheme_rows(quarter, metric["scheme"])
        net = rows.gross_return - metric["cost_multiplier"] * (rows.fee_return + rows.slippage_return)
        assert small_metric["total_return"] == pytest.approx(np.prod(1 + net) - 1)
    assert default_report["metrics"] == full_report["metrics"]


def test_matched_domestic_baseline_has_aligned_eligibility_and_full_denominator():
    d,e,f,cfg = fixture()
    e = e.drop(index=list(range(10,18)) + list(range(50,58)))
    f = f.drop(index=list(range(12,19)) + list(range(52,59)))
    joined = align(d,e,f,96)
    report, details = analyze(d,e,f,cfg)
    cutoff = pd.Timestamp(cfg["train_end"])
    train = (joined.exit_at <= cutoff) & joined.matched
    test = joined.decision_at > cutoff
    baseline = scheme_rows(details,"domestic_lag_baseline")
    matched = scheme_rows(details,"domestic_lag_matched_baseline")
    aligned = scheme_rows(details,"aligned")
    assert baseline.matched.all() and not matched.matched.all()
    pd.testing.assert_series_equal(matched.matched, aligned.matched)
    np.testing.assert_allclose(matched.loc[~matched.matched,"position"],0)
    expected = fit_predict(joined.loc[train,["local_lag_return"]].to_numpy(),
                           joined.loc[train,"asset_return"].to_numpy(),
                           joined.loc[test & joined.matched,["local_lag_return"]].to_numpy())
    np.testing.assert_allclose(matched.loc[matched.matched,"prediction"], expected)
    sigma = max(joined.loc[train,"asset_return"].std(ddof=0),1e-6)
    np.testing.assert_allclose(matched.loc[matched.matched,"position"],np.clip(expected/sigma,-1,1))
    for m in report["metrics"]:
        assert m["windows"] == int(test.sum())
        if m["scheme"] in {"aligned","domestic_lag_matched_baseline"}:
            assert m["training_windows"] == int(train.sum())
    assert len(baseline) == len(matched) == len(aligned) == int(test.sum())


def test_placebo_is_strictly_future_bounded_and_cannot_train_past_cutoff():
    d,e,f,cfg = fixture()
    cfg["train_end"] = "2024-02-15T12:00:00Z"
    e.loc[50,"available_at"] = d.loc[50,"decision_at"]
    report, details = analyze(d,e,f,cfg)
    placebo = scheme_rows(details,"future_misalignment_placebo")
    assert placebo.loc["w50","used_event_id"] == "e51"
    assert placebo.loc["w50","external_age_hours"] < 0
    assert not placebo.loc["w79","matched"]
    far = e.iloc[[-1]].copy()
    far["event_id"] = "far_future"
    far["available_at"] += pd.Timedelta(days=365)
    _, appended = analyze(d,pd.concat([e,far],ignore_index=True),f,cfg)
    pd.testing.assert_frame_equal(placebo,scheme_rows(appended,"future_misalignment_placebo"))
    e.loc[46,"external_return"] = 123.0  # first external event after cutoff
    changed_report, changed = analyze(d,e,f,cfg)
    np.testing.assert_allclose(placebo.prediction,scheme_rows(changed,"future_misalignment_placebo").prediction)
    metric = next(m for m in report["metrics"] if m["scheme"] == "future_misalignment_placebo")
    assert metric["training_windows"] == 45
    assert changed_report["metrics"][0]["training_windows"] == 46


def test_far_future_event_does_not_create_placebo_trade():
    d,e,f,cfg = fixture()
    _, before = analyze(d,e,f,cfg)
    far = e.iloc[[-1]].copy()
    far["event_id"] = "far_future"
    far["available_at"] += pd.Timedelta(days=365)
    _, after = analyze(d,pd.concat([e,far],ignore_index=True),f,cfg)
    assert not scheme_rows(after,"future_misalignment_placebo").loc["w79","matched"]
    np.testing.assert_allclose(scheme_rows(before,"future_misalignment_placebo").position,
                               scheme_rows(after,"future_misalignment_placebo").position)


def test_post_cutoff_placebo_feature_cannot_rewrite_training():
    d,e,f,cfg = fixture()
    _, before = analyze(d,e,f,cfg)
    e.loc[46,"external_return"] = 123.0
    _, after = analyze(d,e,f,cfg)
    np.testing.assert_allclose(scheme_rows(before,"future_misalignment_placebo").prediction,
                               scheme_rows(after,"future_misalignment_placebo").prediction)


def test_extra_lag_shifts_one_domestic_row_and_rechecks_current_age():
    d,e,f,cfg = fixture()
    e = e.drop(index=range(48,55))
    _, details = analyze(d,e,f,cfg)
    lag = scheme_rows(details,"extra_session_lag")
    assert not lag.loc["w55","matched"]
    assert pd.isna(lag.loc["w55","used_event_id"])
    assert lag.loc["w56","used_event_id"] == "e55"
    d,e,f,cfg = fixture()
    for col in ("decision_at","entry_at","exit_at","local_lag_available_at"):
        d.loc[60:,col] += pd.Timedelta(days=7)
    for source in (e,f):
        source.loc[60:,"available_at"] += pd.Timedelta(days=7)
    _, details = analyze(d,e,f,cfg)
    lag = scheme_rows(details,"extra_session_lag")
    holiday = lag.loc["w60"]
    assert holiday.used_event_id == "e59" and holiday.used_fx_id == "f59"
    assert holiday.external_age_hours > 96 and holiday.fx_age_hours > 96
    assert not holiday.matched and holiday.position == 0
    assert lag.loc["w61","matched"] and lag.loc["w61","used_event_id"] == "e60"


def test_extra_lag_stale_after_holiday_cannot_trade():
    d,e,f,cfg = fixture()
    for col in ("decision_at","entry_at","exit_at","local_lag_available_at"):
        d.loc[60:,col] += pd.Timedelta(days=7)
    for source in (e,f):
        source.loc[60:,"available_at"] += pd.Timedelta(days=7)
    _, details = analyze(d,e,f,cfg)
    row = scheme_rows(details,"extra_session_lag").loc["w60"]
    assert not row.matched and row.position == 0


def test_cli_refuses_existing_directory_without_changing_bytes(tmp_path):
    d,e,f,cfg = fixture()
    args = [sys.executable,"-X","utf8",str(Path(__file__).parents[1]/"scripts"/"analyze.py")]
    for name, frame in (("domestic",d),("events",e),("fx",f)):
        path = tmp_path/(name+".csv")
        frame.to_csv(path,index=False)
        args.extend(["--"+name,str(path)])
    config = tmp_path/"config.json"
    config.write_text(json.dumps(cfg))
    out = tmp_path/"existing"
    out.mkdir()
    originals = {"report.json":b"prior report", "signals.csv":b"prior signals"}
    for name, content in originals.items():
        (out/name).write_bytes(content)
    proc = subprocess.run(args+["--config",str(config),"--output",str(out)],
                          capture_output=True,text=True,encoding="utf-8")
    assert proc.returncode != 0
    assert "拒绝覆盖" in proc.stderr
    assert all((out/name).read_bytes() == content for name,content in originals.items())


def test_optional_scope_bounds_exclude_labels_and_count_crossings_separately():
    d,e,f,cfg = fixture()
    cfg.update(train_start="2024-01-10T03:00:00Z",train_end="2024-02-15T03:00:00Z",
               test_end="2024-03-10T03:00:00Z")
    report, details = analyze(d,e,f,cfg)
    assert report["purged_cross_boundary_windows"] == 3
    assert report["out_of_scope_windows"] == 19
    assert set(details.window_id) == {f"w{i}" for i in range(46,69)}
    assert all(m["windows"] == 23 for m in report["metrics"])
    altered = d.copy()
    outside = (altered.exit_at <= pd.Timestamp(cfg["train_start"])) | (altered.decision_at > pd.Timestamp(cfg["test_end"]))
    altered.loc[outside,"asset_return"] = np.arange(outside.sum()) + 123.0
    _, changed = analyze(altered,e,f,cfg)
    np.testing.assert_allclose(details.prediction,changed.prediction)
    for m in report["metrics"]:
        if m["scheme"] in {"aligned","domestic_lag_matched_baseline","domestic_lag_baseline"}:
            assert m["training_windows"] == 35


@pytest.mark.parametrize("bounds", [
    {"train_start":"2024-02-15T23:59:00Z"},
    {"train_start":"2024-02-16T00:00:00Z"},
    {"test_end":"2024-02-15T23:59:00Z"},
    {"test_end":"2024-02-14T00:00:00Z"},
    {"train_start":"2024-01-10"}, {"test_end":"2024-03-10"},
])
def test_invalid_scope_bounds_rejected(bounds):
    d,e,f,cfg = fixture()
    with pytest.raises(ValueError):
        analyze(d,e,f,{**cfg,**bounds})


def test_used_source_identities_returns_and_ages_are_scheme_specific():
    d,e,f,cfg = fixture()
    _, details = analyze(d,e,f,cfg)
    used = ["used_event_id","used_external_available_at","used_fx_id","used_fx_available_at",
            "used_external_return","used_fx_return"]
    for name in ("domestic_lag_baseline","domestic_lag_matched_baseline"):
        rows = scheme_rows(details,name)
        assert rows[used].isna().all().all()
        assert rows.loc["w46","aligned_event_id"] == "e46"
        assert rows[["external_age_hours","fx_age_hours"]].isna().all().all()
    for name, event_idx, fx_idx in (("aligned",46,46),("extra_session_lag",45,45),
                                    ("future_misalignment_placebo",47,46)):
        row = scheme_rows(details,name).loc["w46"]
        assert row.used_event_id == f"e{event_idx}" and row.used_fx_id == f"f{fx_idx}"
        assert row.used_external_available_at == e.loc[event_idx,"available_at"]
        assert row.used_fx_available_at == f.loc[fx_idx,"available_at"]
        assert row.used_external_return == e.loc[event_idx,"external_return"]
        assert row.used_fx_return == f.loc[fx_idx,"fx_return"]
        assert row.external_age_hours == pytest.approx((row.decision_at - row.used_external_available_at).total_seconds()/3600)
        assert row.fx_age_hours == pytest.approx((row.decision_at - row.used_fx_available_at).total_seconds()/3600)
        assert row.aligned_event_id == "e46" and row.aligned_fx_id == "f46"


@pytest.mark.parametrize("key,value", [("notional_scale",0),("notional_scale",1.01),
    ("notional_scale",float("nan")),("notional_scale",float("inf")),
    ("ridge",-1),("ridge",float("nan")),("ridge",float("inf")),
    ("max_age_hours",0),("max_age_hours",-1),("max_age_hours",float("nan")),("max_age_hours",float("inf"))])
def test_invalid_model_settings_are_rejected(key,value):
    d,e,f,cfg = fixture()
    with pytest.raises(ValueError):
        analyze(d,e,f,{**cfg,key:value})


@pytest.mark.parametrize("max_age",[0,-1,float("nan"),float("inf")])
def test_align_rejects_invalid_age(max_age):
    d,e,f,_ = fixture()
    with pytest.raises(ValueError):
        align(d,e,f,max_age)


@pytest.mark.parametrize("which,col",[(0,"window_id"),(1,"event_id"),(2,"fx_id")])
@pytest.mark.parametrize("bad",[None,"","  ","duplicate"])
def test_identifiers_must_be_nonempty_and_unique(which,col,bad):
    d,e,f,cfg = fixture()
    table = (d,e,f)[which]
    table.loc[1,col] = table.loc[0,col] if bad == "duplicate" else bad
    with pytest.raises(ValueError):
        analyze(d,e,f,cfg)


@pytest.mark.parametrize("bad",[np.nan,np.inf,-np.inf])
def test_domestic_nonfinite_labels_fail_closed_even_outside_scope(bad):
    d,e,f,cfg = fixture()
    cfg["train_start"] = "2024-01-10T00:00:00Z"
    d.loc[0,"asset_return"] = bad
    with pytest.raises(ValueError):
        analyze(d,e,f,cfg)


@pytest.mark.parametrize("returns",[[np.nan],[np.inf],[-np.inf],[1e308,1e308]])
def test_performance_rejects_nonfinite_inputs_and_calculated_metrics(returns):
    with pytest.raises(ValueError):
        performance(np.array(returns))


def test_finite_cost_inputs_that_overflow_fail_explicitly_without_runtime_warnings():
    d,e,f,cfg = fixture()
    cfg.update(fee_rate=1e308,slippage_rate=1e308)
    with np.errstate(over="raise",invalid="raise"):
        with pytest.raises(ValueError,match="非有限"):
            analyze(d,e,f,cfg)


def test_insufficient_valid_training_cannot_be_reported_as_zero_model():
    d,e,f,cfg = fixture()
    with pytest.raises(ValueError,match="20"):
        analyze(d,e.iloc[40:],f,cfg)


def test_archive_declaration_is_candidate_only_never_execution_certification():
    d,e,f,cfg = fixture()
    for table in (d,e,f):
        table["available_at_basis"] = "vendor_archive"
    report,_ = analyze(d,e,f,cfg)
    assert report["time_source_independently_verified"] is False
    for metric in report["metrics"]:
        assert metric["executable"] is False
        assert metric["time_causal_candidate"] is (metric["scheme"] != "future_misalignment_placebo")


@pytest.mark.parametrize("initial_gap",[False,True])
def test_large_identifiers_remain_exact_text_through_alignment(initial_gap):
    d,e,f,_ = fixture()
    ids = [2**53 + 1 + 2*i for i in range(len(d))]
    for table, col in ((d,"window_id"),(e,"event_id"),(f,"fx_id")):
        table[col] = ids
    if initial_gap:
        e.loc[0,"available_at"] += pd.Timedelta(hours=2)
        f.loc[0,"available_at"] += pd.Timedelta(hours=2)
    joined = align(d,e,f,96)
    for col in ("event_id","fx_id","window_id"):
        assert joined.loc[46,col] == str(ids[46])
    if initial_gap:
        assert pd.isna(joined.loc[0,"event_id"]) and pd.isna(joined.loc[0,"fx_id"])
    # Normalizing copied inputs must not mutate a caller's integer identifiers.
    assert e.loc[46,"event_id"] == ids[46]


def test_large_identifiers_remain_exact_text_through_extra_lag_and_placebo():
    d,e,f,cfg = fixture()
    ids = [2**53 + 1 + 2*i for i in range(len(d))]
    e["event_id"],f["fx_id"] = ids,ids
    _,details = analyze(d,e,f,cfg)
    lag = scheme_rows(details,"extra_session_lag").loc["w46"]
    assert lag.used_event_id == str(ids[45])
    assert lag.used_fx_id == str(ids[45])
    placebo = scheme_rows(details,"future_misalignment_placebo").loc["w46"]
    assert placebo.used_event_id == str(ids[47])
    assert placebo.used_fx_id == str(ids[46])
    for name in ("domestic_lag_baseline","domestic_lag_matched_baseline"):
        assert scheme_rows(details,name)[["used_event_id","used_fx_id"]].isna().all().all()


def invoke_fixture_cli(tmp_path,d,e,f,cfg):
    args = [sys.executable,"-X","utf8",str(Path(__file__).parents[1]/"scripts"/"analyze.py")]
    for name,frame in (("domestic",d),("events",e),("fx",f)):
        path = tmp_path/(name+".csv")
        frame.to_csv(path,index=False)
        args.extend(["--"+name,str(path)])
    config = tmp_path/"config.json"
    config.write_text(json.dumps(cfg),encoding="utf-8")
    out = tmp_path/"result"
    proc = subprocess.run(args+["--config",str(config),"--output",str(out)],
                          capture_output=True,text=True,encoding="utf-8")
    return proc,out


@pytest.mark.parametrize("identifier_kind",["leading_zero","literal_NA","large_integer"])
def test_cli_preserves_identifier_text_without_numeric_or_missing_coercion(tmp_path,identifier_kind):
    d,e,f,cfg = fixture()
    for table,col in ((d,"window_id"),(e,"event_id"),(f,"fx_id")):
        if identifier_kind == "leading_zero":
            table[col] = [f"{i:06d}" for i in range(len(table))]
        elif identifier_kind == "literal_NA":
            table.loc[46,col] = "NA"
        else:
            table[col] = [2**53 + 1 + 2*i for i in range(len(table))]
    proc,out = invoke_fixture_cli(tmp_path,d,e,f,cfg)
    assert proc.returncode == 0,proc.stderr
    id_columns = ["window_id","aligned_event_id","aligned_fx_id","used_event_id","used_fx_id"]
    signals = pd.read_csv(out/"signals.csv",dtype={col:"string" for col in id_columns},keep_default_na=False)
    window = str(d.loc[46,"window_id"])
    for scheme,event_idx,fx_idx in (("aligned",46,46),("extra_session_lag",45,45),
                                    ("future_misalignment_placebo",47,46)):
        row = scheme_rows(signals,scheme).loc[window]
        assert row.aligned_event_id == str(e.loc[46,"event_id"])
        assert row.aligned_fx_id == str(f.loc[46,"fx_id"])
        assert row.used_event_id == str(e.loc[event_idx,"event_id"])
        assert row.used_fx_id == str(f.loc[fx_idx,"fx_id"])
    for scheme in ("domestic_lag_baseline","domestic_lag_matched_baseline"):
        assert scheme_rows(signals,scheme)[["used_event_id","used_fx_id"]].eq("").all().all()


@pytest.mark.parametrize("which,col",[(0,"asset_return"),(0,"local_lag_return"),
                                     (1,"external_return"),(2,"fx_return")])
def test_cli_missing_returns_still_fail_closed(tmp_path,which,col):
    d,e,f,cfg = fixture()
    table = (d,e,f)[which]
    table[col] = table[col].astype(object)
    table.loc[10,col] = ""
    proc,out = invoke_fixture_cli(tmp_path,d,e,f,cfg)
    assert proc.returncode != 0
    assert not out.exists()


def test_prediction_to_sigma_overflow_is_rejected_before_position_clipping():
    d,e,f,cfg = fixture()
    d["asset_return"] = 0.0
    d.loc[d.exit_at <= pd.Timestamp(cfg["train_end"]),"asset_return"] = float(2**1006)
    with pytest.raises(ValueError,match="仓位.*非有限"):
        analyze(d,e,f,cfg)
