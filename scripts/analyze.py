"""Timestamp-first cross-market study using exported domestic tradable windows."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


def utc(series: pd.Series) -> pd.Series:
    for value in series:
        stamp = pd.Timestamp(value)
        if stamp.tzinfo is None:
            raise ValueError("时间必须带时区，不能猜测交易所时区")
    return pd.to_datetime(series, utc=True, errors="raise")


def maximum_age(hours: float) -> pd.Timedelta:
    hours = float(hours)
    if not np.isfinite(hours) or hours <= 0:
        raise ValueError("信息最大年龄必须为正有限值")
    try:
        return pd.Timedelta(hours=hours)
    except (OverflowError, ValueError) as exc:
        raise ValueError("信息最大年龄超出可表示范围") from exc


def require_identifiers(table: pd.DataFrame, column: str) -> None:
    values = table[column].astype("string")
    stripped = values.str.strip()
    if stripped.isna().any() or stripped.eq("").any() or stripped.duplicated().any():
        raise ValueError("来源和交易窗口标识必须非空且唯一")
    # Convert before a merge or shift can introduce missing values and coerce
    # integer identifiers to lossy floating point. Preserve the original text.
    table[column] = values


def scope_timestamp(value: str, label: str) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError(label + "必须带时区且非空")
    return stamp.tz_convert("UTC")


def align(domestic: pd.DataFrame, events: pd.DataFrame, fx: pd.DataFrame, max_age_hours: float) -> pd.DataFrame:
    age = maximum_age(max_age_hours)
    d, e, f = domestic.copy(), events.copy(), fx.copy()
    for table in (d,e,f):
        if "available_at_basis" not in table or not table.available_at_basis.isin(
                {"vendor_archive", "exchange_archive", "assumed", "synthetic"}).all():
            raise ValueError("时间来源必须明确为供应商/交易所存档、假设或合成数据")
    required = {"window_id", "decision_at", "entry_at", "exit_at", "asset_return", "local_lag_return", "local_lag_available_at"}
    if not required <= set(d) or not {"event_id", "available_at", "external_return"} <= set(e) or not {"fx_id", "available_at", "fx_return"} <= set(f):
        raise ValueError("输入字段不完整，请按数据协议导出")
    for table, column in ((d,"window_id"),(e,"event_id"),(f,"fx_id")):
        require_identifiers(table, column)
    for col in ("decision_at", "entry_at", "exit_at", "local_lag_available_at"):
        d[col] = utc(d[col])
    if (d.local_lag_available_at > d.decision_at).any():
        raise ValueError("国内滞后收益在决策时尚不可见")
    e["external_available_at"], f["fx_available_at"] = utc(e.available_at), utc(f.available_at)
    if e.external_available_at.duplicated().any() or f.fx_available_at.duplicated().any():
        raise ValueError("同一时刻存在多条信息，需先按可审计规则聚合")
    d = d.sort_values("decision_at").reset_index(drop=True)
    if d.window_id.duplicated().any() or (d.entry_at < d.decision_at).any() or (d.exit_at <= d.entry_at).any():
        raise ValueError("交易窗口重复或时间顺序有误")
    if len(d) > 1 and (d.entry_at.iloc[1:].to_numpy() < d.exit_at.iloc[:-1].to_numpy()).any():
        raise ValueError("交易窗口重叠，当前逐窗口平仓口径不支持")
    if not np.isfinite(d[["asset_return", "local_lag_return"]].to_numpy(float)).all():
        raise ValueError("国内收益包含非有限值")
    for table, col in ((e, "external_return"), (f, "fx_return")):
        if not np.isfinite(table[col].to_numpy(float)).all():
            raise ValueError("外盘或汇率收益包含非有限值")
    d = pd.merge_asof(d, e[["event_id", "external_available_at", "external_return"]].sort_values("external_available_at"),
                      left_on="decision_at", right_on="external_available_at", direction="backward", tolerance=age)
    d = pd.merge_asof(d, f[["fx_id", "fx_available_at", "fx_return"]].sort_values("fx_available_at"),
                      left_on="decision_at", right_on="fx_available_at", direction="backward", tolerance=age)
    d["matched"] = d.external_available_at.notna() & d.fx_available_at.notna()
    if (d.loc[d.matched, "external_available_at"] > d.loc[d.matched, "decision_at"]).any():
        raise AssertionError("出现未来信息")
    return d


def fit_predict(train_x: np.ndarray, train_y: np.ndarray, test_x: np.ndarray, ridge: float = 1.0) -> np.ndarray:
    if not np.isfinite(ridge) or ridge < 0:
        raise ValueError("岭惩罚必须为非负有限值")
    if len(train_y) < 20:
        raise ValueError("训练样本少于 20 个交易窗口")
    if not all(np.isfinite(a).all() for a in (train_x,train_y,test_x)):
        raise ValueError("模型输入必须为有限值")
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            mu, sd = train_x.mean(0), train_x.std(0)
            sd = np.where(sd > 1e-12, sd, 1)
            x = np.column_stack([np.ones(len(train_x)), (train_x-mu)/sd])
            penalty = np.eye(x.shape[1])*ridge; penalty[0, 0] = 0
            beta = np.linalg.solve(x.T@x+penalty, x.T@train_y)
            prediction = np.column_stack([np.ones(len(test_x)), (test_x-mu)/sd])@beta
    except FloatingPointError as exc:
        raise ValueError("模型计算产生非有限值") from exc
    if not np.isfinite(prediction).all():
        raise ValueError("模型预测包含非有限值")
    return prediction


def performance(returns: np.ndarray) -> dict:
    r = np.asarray(returns, float)
    if not len(r) or not np.isfinite(r).all() or (r <= -1).any():
        raise ValueError("测试收益为空、非有限值或发生资金耗尽")
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            sd = r.std(ddof=1) if len(r) > 1 else 0
            nav = np.r_[1, np.cumprod(1+r)]
            metrics = {"windows": len(r), "mean_return_per_window": float(r.mean()),
                       "sharpe_per_252_windows": float(np.sqrt(252)*r.mean()/sd) if sd > 0 else None,
                       "total_return": float(nav[-1]-1), "max_drawdown": float(np.min(nav/np.maximum.accumulate(nav)-1))}
    except FloatingPointError as exc:
        raise ValueError("收益指标计算产生非有限值") from exc
    if not all(value is None or np.isfinite(value) for value in metrics.values()):
        raise ValueError("收益指标包含非有限值")
    return metrics


def analyze(domestic: pd.DataFrame, events: pd.DataFrame, fx: pd.DataFrame, cfg: dict) -> tuple[dict, pd.DataFrame]:
    cutoff = scope_timestamp(cfg["train_end"], "训练截止时间")
    start = scope_timestamp(cfg["train_start"], "训练开始时间") if "train_start" in cfg else None
    end = scope_timestamp(cfg["test_end"], "测试结束时间") if "test_end" in cfg else None
    if (start is not None and start >= cutoff) or (end is not None and cutoff >= end):
        raise ValueError("范围必须满足训练开始 < 训练截止 < 测试结束")
    fee, slippage = float(cfg["fee_rate"]), float(cfg["slippage_rate"])
    ridge, scale = float(cfg.get("ridge",1)), float(cfg.get("notional_scale",1))
    if not np.isfinite([fee,slippage]).all() or min(fee,slippage) < 0:
        raise ValueError("费用必须为非负有限值")
    if not np.isfinite(ridge) or ridge < 0:
        raise ValueError("岭惩罚必须为非负有限值")
    if not np.isfinite(scale) or not 0 < scale <= 1:
        raise ValueError("名义仓位比例必须为 (0,1] 内的有限值")
    max_age_hours = float(cfg.get("max_age_hours",96))
    age = maximum_age(max_age_hours)
    d = align(domestic, events, fx, max_age_hours)
    bases = set().union(*(set(t.available_at_basis) for t in (domestic,events,fx)))
    assumptions = "assumed" in bases
    synthetic = "synthetic" in bases
    status = "hypothesis_only" if assumptions else "synthetic_fixture_only" if synthetic else "local_analysis_only_quantstudio_run_required"
    train_mask, test_mask = d.exit_at <= cutoff, d.decision_at > cutoff
    outside = pd.Series(False,index=d.index)
    crossing = (d.decision_at <= cutoff) & (d.exit_at > cutoff)
    if start is not None:
        train_mask &= d.decision_at >= start
        outside |= d.exit_at <= start
        crossing |= (d.decision_at < start) & (d.exit_at > start)
    if end is not None:
        test_mask &= d.exit_at <= end
        outside |= d.decision_at > end
        crossing |= (d.decision_at <= end) & (d.exit_at > end)
    # Wholly out-of-scope rows are not boundary-crossing windows.
    crossing &= ~outside
    if test_mask.sum() < 10:
        raise ValueError("保留测试窗口不足 10 个")
    source_cols = ["event_id","external_available_at","fx_id","fx_available_at","external_return","fx_return"]
    aligned_sources = d[source_cols].copy()
    lagged = aligned_sources.shift(1)  # Exactly one domestic row, including rows with missing sources.
    lag_external_age = (d.decision_at-lagged.external_available_at).dt.total_seconds()/3600
    lag_fx_age = (d.decision_at-lagged.fx_available_at).dt.total_seconds()/3600
    lag_eligible = lag_external_age.between(0,max_age_hours) & lag_fx_age.between(0,max_age_hours)
    future = events[["event_id","available_at","external_return"]].copy()
    future["event_id"] = future.event_id.astype("string")
    future["external_available_at"] = utc(future.pop("available_at"))
    shifted = pd.merge_asof(d[["decision_at"]], future.sort_values("external_available_at"),
                            left_on="decision_at", right_on="external_available_at", direction="forward",
                            tolerance=age, allow_exact_matches=False)
    placebo_sources = shifted[["event_id","external_available_at","external_return"]].join(d[["fx_id","fx_available_at","fx_return"]])
    no_sources = aligned_sources.copy()
    for col in source_cols:
        if col in {"event_id","fx_id"}:
            no_sources[col] = pd.Series(pd.NA,index=d.index,dtype="string")
        else:
            no_sources[col] = pd.NaT if col.endswith("available_at") else np.nan
    all_eligible = pd.Series(True,index=d.index)
    designs = {
        "domestic_lag_baseline": (d[["local_lag_return"]],no_sources,all_eligible),
        "domestic_lag_matched_baseline": (d[["local_lag_return"]],no_sources,d.matched),
        "aligned": (d[["local_lag_return","external_return","fx_return"]],aligned_sources,d.matched),
        "extra_session_lag": (pd.concat([d[["local_lag_return"]],lagged[["external_return","fx_return"]]],axis=1),lagged,lag_eligible),
        "future_misalignment_placebo": (pd.concat([d[["local_lag_return"]],placebo_sources[["external_return","fx_return"]]],axis=1),placebo_sources,all_eligible),
    }
    summaries, details = [], []
    y = d.asset_return.to_numpy(float)
    for name, (frame,sources,eligible) in designs.items():
        valid = (frame.notna().all(1) & eligible).to_numpy()
        train = train_mask.to_numpy() & valid
        if name == "future_misalignment_placebo":
            train &= (sources.external_available_at <= cutoff).to_numpy()
        test = test_mask.to_numpy()
        prediction = np.zeros(len(d))
        target = test & valid
        prediction[target] = fit_predict(frame.loc[train].to_numpy(float), y[train], frame.loc[target].to_numpy(float), ridge)
        with np.errstate(over="raise",invalid="raise"):
            try:
                sigma = max(float(y[train].std()),1e-6)
            except FloatingPointError as exc:
                raise ValueError("训练收益波动计算产生非有限值") from exc
        if not np.isfinite(sigma):
            raise ValueError("训练收益波动包含非有限值")
        try:
            with np.errstate(over="raise",invalid="raise",divide="raise"):
                normalized_prediction = prediction/sigma
        except FloatingPointError as exc:
            raise ValueError("仓位计算产生非有限值") from exc
        if not np.isfinite(normalized_prediction).all():
            raise ValueError("仓位计算包含非有限值")
        position = scale*np.clip(normalized_prediction, -1, 1)
        position[~target] = 0
        try:
            with np.errstate(over="raise",invalid="raise"):
                gross = position*y
                fee_return, slippage_return = 2*np.abs(position)*fee, 2*np.abs(position)*slippage
                one_times_net = gross-fee_return-slippage_return
        except FloatingPointError as exc:
            raise ValueError("逐窗口收益或费用计算产生非有限值") from exc
        for cost_mult in (1, 1.5, 2):
            try:
                with np.errstate(over="raise",invalid="raise"):
                    net = gross-(fee_return+slippage_return)*cost_mult
            except FloatingPointError as exc:
                raise ValueError("费用压力计算产生非有限值") from exc
            summaries.append({"scheme": name, "executable": False,
                              "time_causal_candidate": name != "future_misalignment_placebo" and not (assumptions or synthetic),
                              "cost_multiplier": cost_mult, "coverage": float(valid[test].mean()),
                              "training_windows": int(train.sum()),
                              "nonzero_trades": int(np.count_nonzero(position[test])), **performance(net[test])})
        rows = pd.DataFrame({"window_id": d.loc[test, "window_id"], "scheme": name,
                                     "position": position[test], "prediction": prediction[test],
                                     "asset_return": y[test], "gross_return": gross[test],
                                     "fee_return": fee_return[test], "slippage_return": slippage_return[test],
                                     "net_return": one_times_net[test],
                                     "matched": valid[test], "decision_at": d.loc[test, "decision_at"],
                                     "entry_at":d.loc[test,"entry_at"], "exit_at":d.loc[test,"exit_at"],
                                     "local_lag_available_at":d.loc[test,"local_lag_available_at"],
                                     "aligned_event_id":d.loc[test,"event_id"], "aligned_fx_id":d.loc[test,"fx_id"],
                                     "external_available_at":d.loc[test,"external_available_at"],
                                     "fx_available_at":d.loc[test,"fx_available_at"]})
        # Provenance includes selected candidates on unmatched rows for diagnosis;
        # matched=False candidates are never used for prediction or trading.
        for col in source_cols:
            rows["used_"+col] = sources.loc[test,col]
        if name.startswith("domestic_lag_"):
            rows["external_age_hours"],rows["fx_age_hours"] = np.nan,np.nan
        else:
            rows["external_age_hours"] = (rows.decision_at-rows.used_external_available_at).dt.total_seconds()/3600
            rows["fx_age_hours"] = (rows.decision_at-rows.used_fx_available_at).dt.total_seconds()/3600
        details.append(rows)
    result = {"status": status, "config": cfg,
              "declared_time_bases": sorted(bases), "time_source_independently_verified": False,
              "alignment_coverage": {"train": float(d.loc[train_mask, "matched"].mean()),
                                     "test": float(d.loc[test_mask, "matched"].mean())},
              "purged_cross_boundary_windows": int(crossing.sum()),
              "out_of_scope_windows": int(outside.sum()), "metrics": summaries}
    return result, pd.concat(details, ignore_index=True)


def main() -> None:
    p = argparse.ArgumentParser(description="跨市场信息时间对齐与可执行信号检验")
    for name in ("domestic", "events", "fx", "config", "output"):
        p.add_argument("--"+name, required=True)
    a = p.parse_args()
    out = Path(a.output)
    if out.exists():
        raise ValueError("输出位置已存在，拒绝覆盖已有结果")
    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    if min(float(cfg["fee_rate"]), float(cfg["slippage_rate"])) < 0:
        raise ValueError("费用不能为负")
    # Identifiers are text at the input boundary; e.g. "000046" and "NA" are
    # legitimate IDs, while blank/malformed return fields still fail validation.
    domestic = pd.read_csv(a.domestic,dtype={"window_id":"string"},keep_default_na=False)
    events = pd.read_csv(a.events,dtype={"event_id":"string"},keep_default_na=False)
    fx = pd.read_csv(a.fx,dtype={"fx_id":"string"},keep_default_na=False)
    result, details = analyze(domestic,events,fx,cfg)
    result["input_hashes"] = {n: hashlib.sha256(Path(getattr(a, n)).read_bytes()).hexdigest() for n in ("domestic", "events", "fx", "config")}
    report_json = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    out.mkdir(parents=True, exist_ok=False)
    (out/"report.json").write_text(report_json, encoding="utf-8")
    details.to_csv(out/"signals.csv", index=False)
    print(json.dumps({"output": str(out), "platform_verified": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
