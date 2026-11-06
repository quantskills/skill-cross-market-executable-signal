"""Read-only checks for common omissions in the fill-in templates."""

import csv
import json


CSV_FIELDS = {
    "domestic.csv": (
        "window_id", "decision_at", "entry_at", "exit_at", "asset_return",
        "local_lag_return", "local_lag_available_at", "available_at_basis",
    ),
    "events.csv": ("event_id", "available_at", "external_return", "available_at_basis"),
    "fx.csv": ("fx_id", "available_at", "fx_return", "available_at_basis"),
}
CONFIG_FIELDS = ("train_end", "fee_rate", "slippage_rate")
INPUT_FILES = (*CSV_FIELDS, "config.json")


def csv_issue(path, fields):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.reader(stream, strict=True)
        header = next(rows, [])
        if len(set(header)) != len(header):
            return "表头有重复列"
        missing = [field for field in fields if field not in header]
        if missing:
            return "缺少必填列 " + "、".join(missing)
        columns = [(header.index(field), field) for field in fields]
        has_data = False
        while True:
            line = rows.line_num + 1
            row = next(rows, None)
            if row is None:
                break
            if not row or (len(row) == 1 and not row[0].strip()):
                continue
            has_data = True
            if len(row) != len(header):
                return f"第 {line} 行的列数与表头不同"
            empty = [field for index, field in columns if not row[index].strip()]
            if empty:
                return f"第 {line} 行缺少必填值 " + "、".join(empty)
        if not has_data:
            return "只有表头，没有数据行"


def config_issue(path):
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        return "配置应为 JSON 对象"
    missing = [
        key for key in CONFIG_FIELDS
        if cfg.get(key) is None or (isinstance(cfg[key], str) and not cfg[key].strip())
    ]
    if missing:
        return "缺少必填设置 " + "、".join(missing)


def inspect_inputs(source):
    issues = []
    for name in INPUT_FILES:
        path = source / name
        if not path.is_file():
            issues.append(f"{name} 缺少文件")
            continue
        try:
            issue = csv_issue(path, CSV_FIELDS[name]) if name in CSV_FIELDS else config_issue(path)
        except (UnicodeError, csv.Error, ValueError):
            issue = "格式无效，请保存为 UTF-8 并核对文件格式"
        except OSError:
            issue = "无法读取，请检查文件权限"
        if issue:
            issues.append(f"{name} {issue}")
    return issues
