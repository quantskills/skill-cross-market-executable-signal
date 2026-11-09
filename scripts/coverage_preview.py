"""Preview the aligned model's input coverage without fitting or backtesting."""
import argparse
import csv
import json
from pathlib import Path

import pandas as pd

from analyze import align, read_market_tables, study_bounds, utc, window_masks


def latest_prior(gaps, source):
    times = pd.DataFrame({"prior": utc(source.available_at)}).sort_values("prior")
    return pd.merge_asof(
        gaps[["decision_at"]], times, left_on="decision_at", right_on="prior",
        direction="backward",
    ).prior.array


def source_status(usable, prior):
    if pd.notna(usable):
        return "可用"
    return "截至决策时无记录" if pd.isna(prior) else "超过时限"


def timestamp(value):
    return "" if pd.isna(value) else value.isoformat()


def write_preview(source: Path, output: Path):
    cfg = json.loads((source / "config.json").read_text(encoding="utf-8"))
    bounds = study_bounds(cfg)
    domestic, events, fx = read_market_tables(*(source / name for name in ("domestic.csv", "events.csv", "fx.csv")))
    age = float(cfg.get("max_age_hours", 96))
    aligned = align(domestic, events, fx, age)
    train, test, crossing, outside = window_masks(aligned, *bounds)
    summary = [
        "# 覆盖预览", "",
        "这里只检查按声明时刻能否匹配，不算收益，也不核实数据出处或成交条件。",
        f"外盘和汇率各取截至决策时最近的一条记录，最多允许早于决策 {age:g} 小时。",
        "计数单位为决策窗口，同一天有多个窗口时分别计数。", "",
        "| 区间 | 决策窗口 | 两项都可用 | 匹配不上 | 覆盖率 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for label, mask in (("训练期", train), ("检验期", test), ("合计", train | test)):
        count, matched = int(mask.sum()), int((mask & aligned.matched).sum())
        rate = f"{matched / count:.2%}" if count else "无窗口"
        summary.append(f"| {label} | {count} | {matched} | {count - matched} | {rate} |")
    summary.extend(["", f"未计入覆盖率：跨区间边界 {crossing.sum()} 个，范围外 {outside.sum()} 个。"])
    if (train & aligned.matched).sum() < 20:
        summary.append("- 训练期两项都可用的窗口不足 20 个，正式分析无法训练对齐模型。")
    if test.sum() < 10:
        summary.append("- 检验期国内窗口不足 10 个，正式分析会停止。")
    summary.extend([
        "", "即使预览覆盖充足，正式分析仍需检查其他对照方案的样本和配置。",
        "[查看逐窗口缺口](覆盖缺口.csv)。决策日期按北京时间显示，精确时刻用 UTC。",
        "「截至决策时无记录」表示输入里没有当时或更早的记录；「超过时限」表示有旧记录，但超出设置的时限。",
    ])
    gaps = aligned.loc[(train | test) & ~aligned.matched, [
        "decision_at", "entry_at", "external_available_at", "fx_available_at",
    ]].copy()
    gaps["period"] = train.loc[gaps.index].map({True: "训练期", False: "检验期"})
    gaps["external_prior"] = latest_prior(gaps, events)
    gaps["fx_prior"] = latest_prior(gaps, fx)
    output.mkdir(parents=True, exist_ok=False)
    with (output / "覆盖缺口.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow([
            "区间", "决策日期", "决策时刻（UTC）", "进场时刻（UTC）", "外盘状态", "汇率状态",
            "最近外盘可用时刻（UTC）", "最近汇率可用时刻（UTC）",
        ])
        for row in gaps.itertuples(index=False):
            writer.writerow([
                row.period, row.decision_at.tz_convert("Asia/Shanghai").date().isoformat(),
                timestamp(row.decision_at), timestamp(row.entry_at),
                source_status(row.external_available_at, row.external_prior),
                source_status(row.fx_available_at, row.fx_prior),
                timestamp(row.external_prior), timestamp(row.fx_prior),
            ])
    (output / "覆盖预览.md").write_text("\n".join(summary) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write_preview(args.input_dir, args.output)
