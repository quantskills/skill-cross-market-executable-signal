"""Audit analyzer outputs and describe yearly performance and paired uncertainty.

This does not certify input origin, calendar completeness, publication times,
platform execution, or fills. No model is fitted or selected here.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd


SCHEMES = ("domestic_lag_baseline", "domestic_lag_matched_baseline", "aligned",
           "extra_session_lag", "future_misalignment_placebo")
COSTS = (1, 1.5, 2)
NUMBERS = ("position", "prediction", "asset_return", "gross_return", "fee_return",
           "slippage_return", "net_return")
TIMES = ("decision_at", "entry_at", "exit_at", "local_lag_available_at")
REUSE_FIELDS = ("scheme", "source_kind", "source_id", "used_windows", "available_at",
                "first_decision_at", "last_decision_at", "age_hours_min", "age_hours_max")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, name, low=0, high=10000):
    require(isinstance(value, int) and not isinstance(value, bool) and low <= value <= high,
            f"{name} 必须为范围内的整数")
    return value


def number(value, name):
    require(not isinstance(value, (bool, np.bool_)), f"{name} 不能是布尔值")
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} 必须为有限数值") from exc
    require(np.isfinite(result), f"{name} 必须为有限数值")
    return result


def timestamp(value):
    try:
        result = pd.Timestamp(value)
    except (ValueError, TypeError) as exc:
        raise ValueError("时刻格式无效") from exc
    require(not pd.isna(result) and result.tzinfo is not None, "时刻必须非空且带时区")
    return result.tz_convert("UTC")


def blank(value):
    return value is None or value is pd.NA or (isinstance(value, str) and value == "") or bool(pd.isna(value))


def close(actual, expected, name):
    if actual is None or expected is None:
        require(actual is None and expected is None, f"{name} 不一致")
    else:
        require(np.isclose(number(actual, name), number(expected, name), rtol=1e-10, atol=1e-12), f"{name} 不一致")


def performance(returns):
    r = np.asarray(returns, dtype=float)
    if not len(r):
        return {"windows": 0, "mean_return_per_window": None, "sharpe_per_252_windows": None,
                "total_return": None, "max_drawdown": None}
    require(np.isfinite(r).all() and (r > -1).all(), "收益无效或资金耗尽")
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            mean = float(np.mean(r))
            sd = float(np.std(r, ddof=1)) if len(r) > 1 else 0
            nav = np.r_[1., np.cumprod(1 + r)]
            result = {"windows": len(r), "mean_return_per_window": mean,
                      "sharpe_per_252_windows": float(np.sqrt(252) * mean / sd) if sd > 0 else None,
                      "total_return": float(nav[-1] - 1),
                      "max_drawdown": float(np.min(nav / np.maximum.accumulate(nav) - 1))}
    except FloatingPointError as exc:
        raise ValueError("收益指标溢出") from exc
    require(all(v is None or np.isfinite(v) for v in result.values()), "收益指标非有限")
    return result


def moving_block_interval(differences, block_windows, repeats, seed):
    block = integer(block_windows, "block_windows", 1)
    repeats = integer(repeats, "bootstrap_repeats", 100)
    seed = integer(seed, "seed", 0, 2**32 - 1)
    delta = np.asarray(differences, dtype=float)
    require(delta.ndim == 1 and len(delta) > 0 and np.isfinite(delta).all(), "配对差值无效")
    result = {"windows": len(delta), "block_windows": block, "repeats": repeats, "seed": seed,
              "mean_difference": float(delta.mean()), "full_blocks_floor": len(delta) // block,
              "ci_low": None, "ci_high": None, "status": "insufficient_windows"}
    if block > len(delta):
        return result
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(repeats):
        starts = rng.integers(0, len(delta) - block + 1, size=(len(delta) + block - 1) // block)
        sample = np.concatenate([delta[start:start + block] for start in starts])[:len(delta)]
        means.append(float(sample.mean()))
    require(np.isfinite(means).all(), "重抽样均值非有限")
    low, high = np.quantile(means, [.025, .975])
    result.update(ci_low=float(low), ci_high=float(high),
                  status="few_blocks_descriptive_only" if len(delta) // block < 5 else "descriptive_only")
    return result


def validate_plan(plan):
    required = {"evaluation_years", "report_timezone", "bootstrap_block_windows", "bootstrap_repeats", "seed"}
    require(isinstance(plan, dict) and set(plan) == required, "汇总计划字段不完整或含未知设置")
    years = plan["evaluation_years"]
    require(isinstance(years, list) and 0 < len(years) <= 100, "评价年份无效")
    for value in years:
        integer(value, "evaluation_years", 1900, 2200)
    require(years == list(range(years[0], years[-1] + 1)), "评价年份必须升序连续且无重复")
    try:
        zone = ZoneInfo(plan["report_timezone"])
    except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
        raise ValueError("报告时区无效") from exc
    blocks = plan["bootstrap_block_windows"]
    require(isinstance(blocks, list) and 0 < len(blocks) <= 10, "区块列表无效")
    for value in blocks:
        integer(value, "bootstrap_block_windows", 1)
    require(blocks == sorted(set(blocks)), "区块长度须升序且不重复")
    integer(plan["bootstrap_repeats"], "bootstrap_repeats", 100)
    integer(plan["seed"], "seed", 0, 2**32 - 1)
    return zone


def audited_frames(report, signals):
    require(isinstance(report, dict) and isinstance(signals, pd.DataFrame), "输入类型无效")
    bases = report.get("declared_time_bases")
    require(isinstance(bases, list) and len(bases) > 0 and set(bases) <= {"synthetic", "assumed", "vendor_archive", "exchange_archive"}, "时间来源声明无效")
    status = "hypothesis_only" if "assumed" in bases else "synthetic_fixture_only" if "synthetic" in bases else "local_analysis_only_quantstudio_run_required"
    require(report.get("status") == status and report.get("time_source_independently_verified") is False, "来源状态不能升级")
    cfg = report.get("config", {})
    fee, slip = number(cfg.get("fee_rate"), "fee_rate"), number(cfg.get("slippage_rate"), "slippage_rate")
    scale = number(cfg.get("notional_scale", 1), "notional_scale")
    require(fee >= 0 and slip >= 0 and 0 < scale <= 1, "费用或仓位范围无效")
    age_limit = number(cfg.get("max_age_hours", 96), "max_age_hours")
    require(age_limit > 0, "陈旧时限无效")
    cutoff = timestamp(cfg.get("train_end"))
    end = timestamp(cfg["test_end"]) if "test_end" in cfg else None
    required = {"window_id", "scheme", "matched", *NUMBERS, *TIMES,
                "used_event_id", "used_fx_id", "used_external_available_at", "used_fx_available_at",
                "used_external_return", "used_fx_return", "external_age_hours", "fx_age_hours"}
    require(required <= set(signals), "明细字段不足")
    d = signals.copy()
    require(set(d.scheme) == set(SCHEMES) and not d.duplicated(["scheme", "window_id"]).any(), "方案集合不全或窗口重复")
    require(d.window_id.map(lambda v: isinstance(v, str) and bool(v.strip())).all(), "窗口编号必须是非空文本")
    require(d.matched.map(lambda v: isinstance(v, (bool, np.bool_))).all(), "matched 必须是布尔值")
    for col in NUMBERS:
        require(not d[col].map(lambda v: isinstance(v, (bool, np.bool_))).any(), "数值字段含布尔值")
        try:
            d[col] = pd.to_numeric(d[col], errors="raise").astype(float)
        except (TypeError, ValueError) as exc:
            raise ValueError("明细数值字段无效") from exc
        require(np.isfinite(d[col]).all(), "明细数值须有限")
    for col in TIMES:
        d[col] = pd.to_datetime(d[col].map(timestamp), utc=True)
    require(((d.local_lag_available_at <= d.decision_at) & (d.decision_at <= d.entry_at) & (d.entry_at < d.exit_at)).all(), "窗口时间次序无效")
    require((end is None or end > cutoff) and (d.decision_at > cutoff).all()
            and (end is None or (d.exit_at <= end).all()), "包含评价范围外窗口")
    require((d.position.abs() <= scale + 1e-12).all(), "仓位超过冻结比例")
    require((d.loc[~d.matched, list(NUMBERS[:2]) + ["gross_return", "fee_return", "slippage_return", "net_return"]] == 0).all().all(), "无匹配窗口必须保留为零仓位")
    for name, expected in (("gross_return", d.position * d.asset_return),
                           ("fee_return", 2 * d.position.abs() * fee),
                           ("slippage_return", 2 * d.position.abs() * slip),
                           ("net_return", d.gross_return - d.fee_return - d.slippage_return)):
        require(np.allclose(d[name], expected, rtol=1e-10, atol=1e-12), f"{name} 公式不一致")
    frames = {s: d[d.scheme == s].sort_values("entry_at").reset_index(drop=True) for s in SCHEMES}
    base = frames[SCHEMES[0]]
    require(len(base) >= 10 and (base.entry_at.iloc[1:].to_numpy() >= base.exit_at.iloc[:-1].to_numpy()).all(), "窗口太少或重叠")
    require(base.decision_at.is_monotonic_increasing, "决策顺序与执行窗口顺序不一致")
    common = ["window_id", "asset_return", *TIMES]
    source_identities = {}
    source_times = {}
    for scheme, frame in frames.items():
        require(frame[common].equals(base[common]), "方案的评价窗口或国内标签不一致")
        if scheme.startswith("domestic_lag_"):
            require(frame[["used_event_id", "used_fx_id", "used_external_available_at", "used_fx_available_at",
                           "used_external_return", "used_fx_return"]].map(blank).all().all(), "国内基线不能声称使用外盘来源")
            continue
        for kind, id_col, time_col, age_col, return_col in (
            ("external", "used_event_id", "used_external_available_at", "external_age_hours", "used_external_return"),
            ("fx", "used_fx_id", "used_fx_available_at", "fx_age_hours", "used_fx_return"),
        ):
            valid = frame[frame.matched]
            if valid.empty:
                continue
            require(valid[id_col].map(lambda v: isinstance(v, str) and bool(v.strip())).all(), "匹配来源编号必须为非空文本")
            require(not valid[return_col].map(lambda v: isinstance(v, (bool, np.bool_))).any(), "来源值不能是布尔值")
            values = pd.to_numeric(valid[return_col], errors="raise")
            require(np.isfinite(values).all(), "匹配来源值须有限")
            available = pd.to_datetime(valid[time_col].map(timestamp), utc=True)
            ages = (valid.decision_at - available).dt.total_seconds() / 3600
            supplied = pd.to_numeric(valid[age_col], errors="raise")
            require(np.allclose(ages, supplied, rtol=1e-10, atol=1e-12), "信息年龄不一致")
            future = kind == "external" and scheme == "future_misalignment_placebo"
            require(((ages < 0) & (ages >= -age_limit)).all() if future else ((ages >= 0) & (ages <= age_limit)).all(), "匹配来源时点无效")
            for source_id, at, value in zip(valid[id_col], available, values):
                key = (kind, source_id)
                identity = (at, float(value))
                require(key not in source_identities or source_identities[key] == identity,
                        "同一来源编号跨方案的时刻或数值不一致")
                source_identities[key] = identity
                time_key = (kind, at)
                require(time_key not in source_times or source_times[time_key] == source_id,
                        "同一来源时刻不能对应多个编号")
                source_times[time_key] = source_id
    # The first held-out row's predecessor is not exported (it is in training).
    # Later matched lag rows must use exactly the preceding domestic row's
    # aligned candidates, even when those candidates did not qualify then.
    aligned, lagged = frames["aligned"], frames["extra_session_lag"]
    for i in range(1, len(lagged)):
        if not lagged.loc[i, "matched"]:
            continue
        window = lagged.loc[i, "window_id"]
        for col in ("used_event_id", "used_fx_id"):
            require(lagged.loc[i, col] == aligned.loc[i - 1, col], f"窗口 {window} 滞后来源编号不是上一国内窗口的来源")
        for col in ("used_external_available_at", "used_fx_available_at"):
            require(timestamp(lagged.loc[i, col]) == timestamp(aligned.loc[i - 1, col]),
                    f"窗口 {window} 滞后来源时刻不是上一国内窗口的来源")
        for col in ("used_external_return", "used_fx_return"):
            require(number(lagged.loc[i, col], col) == number(aligned.loc[i - 1, col], col),
                    f"窗口 {window} 滞后来源数值不是上一国内窗口的来源")
    require(frames[SCHEMES[0]].matched.all(), "完整国内基线不能缺覆盖")
    require(frames[SCHEMES[1]].matched.equals(frames["aligned"].matched), "同覆盖基线资格不一致")
    metrics = report.get("metrics", [])
    require(isinstance(metrics, list) and len(metrics) == 15, "汇总指标必须为五方案三档费用")
    indexed = {}
    for row in metrics:
        require(isinstance(row, dict), "指标行必须为对象")
        number(row.get("cost_multiplier"), "cost_multiplier")
        key = (row.get("scheme"), row.get("cost_multiplier"))
        require(key not in indexed and key[0] in SCHEMES and key[1] in COSTS, "汇总方案重复或无效")
        indexed[key] = row
    require(set(indexed) == {(s, c) for s in SCHEMES for c in COSTS}, "汇总方案不完整")
    training_counts = {}
    for (scheme, cost), row in indexed.items():
        frame = frames[scheme]
        require(row.get("executable") is False, "汇总不可标记可执行")
        expected_candidate = scheme != SCHEMES[-1] and status == "local_analysis_only_quantstudio_run_required"
        require(row.get("time_causal_candidate") is expected_candidate, "候选状态不一致")
        train = row.get("training_windows")
        integer(train, "training_windows", 20, 10**9)
        require(scheme not in training_counts or training_counts[scheme] == train,
                "同一方案的训练窗口数不能随费用档变化")
        training_counts[scheme] = train
        close(row.get("coverage"), float(frame.matched.mean()), "coverage")
        close(row.get("nonzero_trades"), int((frame.position != 0).sum()), "nonzero_trades")
        for key, value in performance(frame.gross_return.to_numpy() - cost * (frame.fee_return.to_numpy() + frame.slippage_return.to_numpy())).items():
            close(row.get(key), value, key)
    close(report.get("alignment_coverage", {}).get("test"), float(frames["aligned"].matched.mean()), "alignment_coverage")
    require(training_counts[SCHEMES[1]] == training_counts["aligned"], "同覆盖训练资格数量不一致")
    return frames


def summarize(report, signals, plan):
    zone = validate_plan(plan)
    frames = audited_frames(report, signals)
    observed = set(frames[SCHEMES[0]].entry_at.dt.tz_convert(zone).dt.year)
    require(observed <= set(plan["evaluation_years"]), "报告年份不能排除已有评价窗口")
    cutoff = timestamp(report["config"]["train_end"]).tz_convert(zone)
    first_year = cutoff.year
    # A year-end cutoff's empty final minute need not become an extra test year;
    # any actual test windows in it are still required by the observed-year check.
    if (cutoff.month, cutoff.day, cutoff.hour, cutoff.minute) == (12, 31, 23, 59) and first_year not in observed:
        first_year += 1
    last_year = timestamp(report["config"]["test_end"]).tz_convert(zone).year if "test_end" in report["config"] else max(observed)
    require(set(range(first_year, last_year + 1)) <= set(plan["evaluation_years"]),
            "报告年份不得省略声明范围内的无窗口年份")
    yearly, reuse, paired, source_counts = [], [], [], []
    for scheme, frame in frames.items():
        years = frame.entry_at.dt.tz_convert(zone).dt.year
        for year in plan["evaluation_years"]:
            part = frame[years == year]
            for cost in COSTS:
                net = part.gross_return.to_numpy() - cost * (part.fee_return.to_numpy() + part.slippage_return.to_numpy())
                yearly.append({"scheme": scheme, "year": year, "cost_multiplier": cost,
                               "matched_windows": int(part.matched.sum()),
                               "coverage": float(part.matched.mean()) if len(part) else None,
                               "zero_position_windows": int((part.position == 0).sum()),
                               "nonzero_trades": int((part.position != 0).sum()), **performance(net)})
        if scheme.startswith("domestic_lag_"):
            continue
        for kind, id_col, time_col, age_col in (
            ("external", "used_event_id", "used_external_available_at", "external_age_hours"),
            ("fx", "used_fx_id", "used_fx_available_at", "fx_age_hours"),
        ):
            used = frame[frame.matched]
            for source_id, group in used.groupby(id_col, sort=False):
                times = group[time_col].map(timestamp)
                require(times.nunique() == 1, "同一来源编号对应不同可用时刻")
                ages = pd.to_numeric(group[age_col], errors="raise")
                reuse.append({"scheme": scheme, "source_kind": kind, "source_id": source_id,
                              "used_windows": len(group), "available_at": times.iloc[0].isoformat(),
                              "first_decision_at": group.decision_at.min().isoformat(),
                              "last_decision_at": group.decision_at.max().isoformat(),
                              "age_hours_min": float(ages.min()), "age_hours_max": float(ages.max())})
            counts = used.groupby(id_col).size()
            source_counts.append({"scheme": scheme, "source_kind": kind, "matched_uses": len(used),
                                  "distinct_source_ids": len(counts), "reused_uses": len(used) - len(counts),
                                  "max_windows_per_source": int(counts.max()) if len(counts) else 0})
    candidate = frames["aligned"]
    for baseline in SCHEMES[:2]:
        base = frames[baseline]
        for cost in COSTS:
            delta = (candidate.gross_return.to_numpy() - cost * (candidate.fee_return.to_numpy() + candidate.slippage_return.to_numpy())
                     - base.gross_return.to_numpy() + cost * (base.fee_return.to_numpy() + base.slippage_return.to_numpy()))
            for block in plan["bootstrap_block_windows"]:
                paired.append({"candidate": "aligned", "baseline": baseline, "cost_multiplier": cost,
                               **moving_block_interval(delta, block, plan["bootstrap_repeats"], plan["seed"])})
    return {"status": report["status"], "scope": "supplied_output_consistency_and_descriptive_statistics_only",
            "executable": False, "calendar_independently_verified": False,
            "time_source_independently_verified": False, "platform_execution_independently_verified": False,
            "reporting_plan": plan, "yearly": yearly, "source_reuse": reuse, "source_counts": source_counts,
            "paired_intervals": paired, "statistic": "mean_window_net_return_difference",
            "limitations": ["Coverage denominator is supplied domestic windows, not an independently verified exchange calendar.",
                            "Intervals are descriptive, not causal evidence, multiple-testing correction or an execution recommendation.",
                            "Window blocks do not certify independence of reused source releases or remove historical selection.",
                            "Sensitivity runs require separate frozen inputs and model execution; this helper does not refit ridge or shift source availability.",
                            "Source authorization, timestamps, actual market study, platform execution and owner acceptance remain separate evidence."]}


def markdown(result):
    lines = ["# 跨市场研究输出核对", "", f"输入状态 `{result['status']}`。本报告只核对所给输出并作描述统计，不能证明真实数据、完整日历或实际成交。", "",
             "## 本次输入与报告设置", ""]
    for name, fingerprint in result.get("input_hashes", {}).items():
        lines.append(f"- {name} SHA-256 `{fingerprint}`")
    if not result.get("input_hashes"):
        lines.append("未提供文件指纹。请通过命令行读取文件，才能记录输入字节指纹。")
    lines += ["", "```json", json.dumps(result["reporting_plan"], ensure_ascii=False, indent=2), "```", "",
              "## 逐年与费用", "", "| 年份 | 方案 | 费用倍数 | 窗口数 | 覆盖率 | 累计净收益 |", "|---|---|---|---|---|---|"]
    for row in result["yearly"]:
        coverage = "无窗口" if row["coverage"] is None else f"{row['coverage']:.1%}"
        net = "未计算" if row["total_return"] is None else f"{row['total_return']:.2%}"
        lines.append(f"| {row['year']} | {row['scheme']} | {row['cost_multiplier']} | {row['windows']} | {coverage} | {net} |")
    lines += ["", "## 统计边界", "", "配对区块重抽样的数值见 paired_intervals.csv，单位为每窗口净收益率差，不是累计收益差或显著性检验。区块长于样本时不计算区间，不缩短区块。", "",
              "来源重复使用明细见 source_reuse.csv。缺覆盖窗口保留为零仓位，来源编号按文本保存。覆盖率只针对输入中的窗口，未认证交易日历。", "",
              "本工具不调整模型、选择参数或认证发布时刻。真实来源许可、市场检验、平台运行及负责人确认仍需另附证据。", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="核对跨市场输出并生成逐年、来源复用和配对区间报告")
    for name in ("report", "signals", "plan", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    output = Path(args.output)
    require(not output.exists(), "输出位置已存在，拒绝覆盖")
    raw = {name: Path(getattr(args, name)).read_bytes() for name in ("report", "signals", "plan")}
    report = json.loads(raw["report"].decode("utf-8-sig"))
    plan = json.loads(raw["plan"].decode("utf-8-sig"))
    signals = pd.read_csv(io.BytesIO(raw["signals"]), dtype={"window_id": "string", "used_event_id": "string", "used_fx_id": "string"}, keep_default_na=False)
    result = summarize(report, signals, plan)
    result["input_hashes"] = {name: hashlib.sha256(body).hexdigest() for name, body in raw.items()}
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    output.mkdir(parents=True, exist_ok=False)
    (output / "summary.json").write_text(encoded, encoding="utf-8")
    pd.DataFrame(result["yearly"]).to_csv(output / "yearly.csv", index=False)
    pd.DataFrame(result["source_reuse"], columns=REUSE_FIELDS).to_csv(output / "source_reuse.csv", index=False)
    pd.DataFrame(result["paired_intervals"]).to_csv(output / "paired_intervals.csv", index=False)
    (output / "REPORT.md").write_text(markdown(result), encoding="utf-8")
    print(json.dumps({"output": str(output), "platform_verified": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
