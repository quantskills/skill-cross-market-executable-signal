# 从这里开始

本入口在本地运行，不联网、不登录、不自动安装软件，也不下单。正式评分、模型审查和平台复验不是普通使用的前提。

## 1. 检查环境

需要 Python 3.11 或以上。在本仓库目录打开终端，先运行：

```powershell
python start.py --check
```

检查会列出缺失或不同于已验证版本的依赖；不修改现有环境。未就绪时，手动建立独立环境并安装固定依赖（安装通常需要联网）：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python start.py --check
```

macOS / Linux 将下文的 `.venv\Scripts\python` 换成 `.venv/bin/python`。不要替换平台全局依赖；本入口按仓库固定版本检查，平台历史版本的验证结论仍单独保留。

## 2. 跑合成示例

```powershell
.venv\Scripts\python start.py --demo --output "outputs/我的示例 01"
```

打开输出目录里的 `使用结果.md`，先看本次结果和限制，再通过链接打开原始报告。合成数据只检查流程，不证明真实市场或模型效果。同时生成逐年与稳健性汇总；所有可交易标记仍为 false。

## 3. 用自己的数据

先复制[空白模板](../templates/input/)，按[填表说明](prepare-inputs.md)找资料、填列。正式分析前可用 `python start.py --check-input --input-dir "我的输入"` 检查缺项，不需要先装依赖，也不生成报告。

把以下文件放进一个文件夹，字段和内容按[数据协议](data-contract.md)准备；不能只是改文件名。

| 文件 | 内容 |
|---|---|
| `domestic.csv` | 国内窗口及信息可用时刻 |
| `events.csv` | 外盘数据及时间来源 |
| `fx.csv` | 汇率及时间来源 |
| `config.json` | 冻结训练边界、费用与仓位配置 |

```powershell
.venv\Scripts\python start.py --run --input-dir "我的输入" --output "outputs/我的结果 01"
```

此入口生成通用分析报告；需要逐年汇总时，再按[研究报告说明](study-report.md)使用事先冻结的报告计划。

程序只知道文件由使用者提供，不会自动认证数据真实、授权充分、历史发布时间正确或可成交。已看过的保留样本不能再次冒充未见数据。每次必须指定新输出目录，已有目录即停止，不覆盖旧结果。缺文件时不会建立输出目录；内容错误时会停止，可能保留部分诊断产物，修正后换新目录重试。

不带参数运行 `python start.py` 可使用中文菜单；输入 0 或结束输入即可退出。路径有空格时请加双引号。错误提示不会转印输入正文或凭据。

需要实际平台取数或复跑时读[平台运行](platform-run.md)；仅在需要正式评分、模型审查或负责人确认时读[可选评估](assessment.md)。

