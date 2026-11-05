"""Standard-library-only guide; analysis runs in isolated child processes."""
import argparse
from importlib import metadata
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
KIND = "cross"
FILES = {"prompt": ("suite.json", "manifest.json", "versions.json", "runs.jsonl"),
         "cta": ("outcomes.csv", "features.csv", "config.json"),
         "cross": ("domestic.csv", "events.csv", "fx.csv", "config.json")}[KIND]

class GuideError(Exception):
    pass


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "参数不正确：请运行 python start.py --help 查看用法。\n")

def check():
    ready = sys.version_info >= (3, 11)
    print("Python " + ".".join(map(str, sys.version_info[:3])) + ("：可用" if ready else "：需要 3.11 或以上"))
    for line in (ROOT/"requirements.txt").read_text(encoding="utf-8").splitlines():
        if "==" not in line or line.lstrip().startswith("#"):
            continue
        name, required = line.strip().split("==", 1)
        try:
            actual = metadata.version(name)
        except metadata.PackageNotFoundError:
            actual = None
        # Match the documented clean environment; no automatic changes.
        match = actual == required
        ready = ready and match
        print("依赖 " + name + "：" + (actual or "未安装") + "；已验证版本 " + required + (" ✓" if match else "（需准备或单独验证）"))
    print("检查不安装软件、不联网、不登录。准备方法见 references/getting-started.md。")
    return 0 if ready else 1

def invoke(script, args):
    result = subprocess.run([sys.executable, "-X", "utf8", str(ROOT/"scripts"/script), *map(str,args)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise GuideError("分析未完成：请按 references/data-contract.md 检查输入内容和依赖；不会显示输入内容或账户信息。若已有部分产物，请保留排查并换新目录重试。")

def analyze(source, out):
    args = ["evaluate"] if KIND == "prompt" else []
    for filename in FILES:
        args += ["--" + filename.split(".")[0], source/filename]
    if KIND == "prompt":
        args += ["--split", "holdout"]
    invoke("regression.py" if KIND == "prompt" else "analyze.py", args + ["--output", out])

def summary(out, report_path, demo):
    report = json.loads(report_path.read_text(encoding="utf-8"))
    mode = "合成演示：不是市场行情或真实模型实验。" if demo else "用户提供的数据：程序没有自动证明它是真实数据、获得授权或具备历史时点证据；也可能包含合成数据。"
    lines = ["# 使用结果", "", mode, "", "## 本次结果", ""]
    if KIND == "prompt":
        lines += ["候选是否通过放行条件：" + str(report["accepted"]).lower(),
                  "建议版本：" + str(report["recommended_version"]),
                  "评价任务数：" + str(report["n_tasks"]),
                  "候选相对基线的通过率差：" + str(report["paired_task_pass_rate_delta"]),
                  "保留集评价不会修改用户的本地或平台版本指针。"]
        if demo:
            lines += ["本演示沿用旧示例，只在本次新输出目录内演示本地指针恢复，不修改平台。"]
    else:
        lines += ["报告状态：" + report["status"], "比较结果数量：" + str(len(report["metrics"])),
                  "", "以下数值来自本次报告，未选择最佳结果冒充整体结论。", "",
                  "```json", json.dumps(report["metrics"], ensure_ascii=False, indent=2), "```"]
        if KIND == "cross":
            lines += ["检验窗口匹配率：" + str(report["alignment_coverage"]["test"]),
                      "所有方案均不构成已认证的可交易信号；工具不下单。"]
    lines += ["", "## 限制", "", "结果只适用于本次输入、冻结规则和样本；合成演示不证明真实效果。已经查看的保留样本不能继续冒充未见数据。",
              "统计比较不替代平台证据、历史发布时间核验或成交验证；不保证未来表现。",
              "", "## 原始结果", "", "[原始报告](" + report_path.relative_to(out).as_posix() + ")"]
    for path in sorted(out.rglob("*")):
        if path.is_file() and path != report_path and path.name != "使用结果.md":
            rel = path.relative_to(out).as_posix()
            lines.append("- [" + rel + "](" + rel.replace(" ", "%20") + ")")
    (out/"使用结果.md").write_text("\n".join(lines)+"\n", encoding="utf-8")

def execute(demo, input_dir, output):
    if not output:
        raise GuideError("请用 --output 指定一个新的输出目录。")
    out = Path(output).expanduser().resolve()
    if out.exists():
        raise GuideError("输出目录已存在：为保留旧结果，请换一个新目录。")
    source = ROOT/"examples"/"synthetic" if demo and KIND == "cross" else Path(input_dir).expanduser().resolve() if input_dir else None
    if not demo and source is None:
        raise GuideError("请用 --input-dir 指定输入文件夹。")
    if source is not None:
        missing = [name for name in FILES if not (source/name).is_file()]
        if missing:
            raise GuideError("缺少必要输入文件：" + "、".join(missing) + "。请参照 references/getting-started.md。")
    if check():
        raise GuideError("依赖尚未就绪：请先按入门说明准备环境，再运行 --check。")
    if demo and KIND != "cross":
        invoke("demo.py", ["--output", out])
        report = out/("evaluation" if KIND == "prompt" else "analysis")/"report.json"
    else:
        analyze(source, out)
        report = out/"report.json"
        if demo:
            invoke("summarize_study.py", ["--report", report, "--signals", out/"signals.csv",
                "--plan", source/"reporting-plan.json", "--output", out/"summary"])
    summary(out, report, demo)
    print("已完成。请打开：" + str(out/"使用结果.md"))
    return 0

def menu():
    while True:
        print("\n1 环境检查\n2 运行合成示例\n3 使用自己的数据文件夹\n0 退出")
        try:
            choice = input("请选择：").strip()
            if choice in ("0", "q", "exit"):
                return 0
            if choice == "1":
                check()
            elif choice in ("2", "3"):
                source = input("输入文件夹：").strip().strip('"') if choice == "3" else None
                output = input("新的输出文件夹：").strip().strip('"')
                execute(choice == "2", source, output)
            else:
                print("请输入 0、1、2 或 3。")
        except EOFError:
            print("\n已退出。")
            return 0

def main(argv=None):
    parser = SafeParser(description="环境检查、离线示例、用户数据分析；不联网、不登录、不安装。")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="检查环境")
    group.add_argument("--demo", action="store_true", help="运行合成示例")
    group.add_argument("--run", action="store_true", help="分析用户提供的文件")
    parser.add_argument("--input-dir")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        if args.check:
            return check()
        if args.demo or args.run:
            return execute(args.demo, args.input_dir, args.output)
        if args.input_dir or args.output:
            raise GuideError("请同时选择 --demo 或 --run。")
        return menu()
    except GuideError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130
    except Exception:
        print("分析未完成：请检查文件权限、输入格式和环境。为保护数据，不显示原始异常内容。", file=sys.stderr)
        return 1

