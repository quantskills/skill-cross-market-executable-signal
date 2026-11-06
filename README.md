# 跨市场时间对齐研究

想知道外盘信息加入国内模型后有没有用，先要对齐信息可用时间和国内交易窗口。这个 Skill 把外盘事件、汇率和国内行情按时间匹配，再把加入外盘信息的方案与国内基线放在一起比较。

## 可以看什么

- 每个国内决策窗口用了哪条外盘信息、哪条汇率，以及它们距决策时刻有多久。
- 在相同覆盖范围和费用口径下，加入外盘信息前后有什么变化。
- 延迟或费用变化时，年度表现、覆盖缺口和逐窗口结果如何变化。

## 快速开始

需要 Python 3.11 或更新版本。下载代码后，在项目目录运行合成示例，无需行情权限。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python start.py --demo --output outputs/first-demo
```

打开 `outputs/first-demo/使用结果.md` 查看中文说明和明细入口。macOS 和 Linux 请用 `.venv/bin/python` 替换示例中的 Python 路径。

有自己的国内行情、外盘事件和汇率时，按[三步入门](references/getting-started.md)准备文件，再执行下面的命令。

```powershell
.venv\Scripts\python start.py --run --input-dir "我的输入" --output "outputs/我的结果"
```

每次运行请选新的输出目录。原始平台数据可先按[平台数据转换](references/platform-inputs.md)整理。时间戳和费用字段见[数据协议](references/data-contract.md)。

## 数据与运行验证

研究使用在 EVO 实际取得的 PandaData WTI 指标、美元兑人民币汇率和上海原油合约历史。冻结的日频研究在 QuantStudio 实际复算，并与本地结果核对。相关样本覆盖、成本口径和结果见[实际研究记录](references/validation-20261104.md)，本地 318 项测试见[本地复验记录](references/validation-20261105.md)。

这是一个日频研究工具。报告会保留时间来源和等待假设，方便读者按同一条件复查或替换数据做自己的比较。数据口径见[来源核对](references/evidence-status-20261105.md)。
