# 跨市场时间对齐研究

外盘先动，国内开盘时这条信息究竟已经可用，还是回看数据才知道？这个 Skill 按数据声明的可用时间，把外盘事件、汇率和国内决策窗口匹配起来。你可以在相同覆盖和费用下，比较加入外盘信息前后的研究结果，并追查每个窗口实际用了哪条信息。

## 快速上手

在代码目录打开 PowerShell。需要 Python 3.11 或更新版本。首次安装依赖通常需要联网，示例运行不需要账号或密钥。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python start.py --demo --output outputs/first-demo
```

macOS 或 Linux 用 `.venv/bin/python` 替换命令中的 Python 路径。再次运行时换一个新的输出目录。

## 例子 · 外盘信息晚一个窗口还有用吗？

打开 `outputs/first-demo/使用结果.md`。在同一费用档，先确认「时间对齐模型」与「国内滞后基线（同覆盖）」的样本范围，再比扣费收益。合成示例在一倍费用下分别是 1.07% 和 0.27%。再看「额外滞后一窗口」一行，就能检查这个差距是否依赖信息到得够早。这些数字只用于练习读报告。

如果某个窗口贡献异常，去 `signals.csv` 对照决策时间 `decision_at`、实际使用的外盘信息编号 `used_event_id` 和它的可用时间 `used_external_available_at`。先查明时间来源再解释差异。报告用于研究，不生成委托。

## 换成自己的数据

准备国内行情 `domestic.csv`、外盘事件 `events.csv`、汇率 `fx.csv` 和研究配置 `config.json`。按[三步入门](references/getting-started.md)准备可用时间、费用和仓位字段。

```powershell
.venv\Scripts\python start.py --run --input-dir "我的输入" --output "outputs/我的结果"
```

## 参考资料

[平台数据转换](references/platform-inputs.md) · [实际研究记录](references/validation-20261104.md) · [来源核对](references/evidence-status-20261105.md) · [软件复验记录](references/validation-20261105.md)
