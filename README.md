# 跨市场时间对齐研究工具

首次使用先看[三步入门](references/getting-started.md)：运行 `python start.py --check` 检查环境，`python start.py` 打开中文菜单。准备好依赖后，运行 `python start.py --demo --output outputs/first-demo`，打开结果目录里的 `使用结果.md`。每次使用新目录；示例是合成数据，不证明真实效果。

自己的数据统一放进一个文件夹，再运行 `python start.py --run --input-dir "我的输入" --output "outputs/我的结果"`。所需文件与准备步骤见入门说明。以下保留原始命令和历史研究记录，供进一步使用；[正式评分与模型审查](references/assessment.md)均为可选，不阻止普通使用。

外盘价格变化之后，国内市场还有没有可以利用的信息，需要把发布时间、交易时段和成本一起算进去。这套工具按输入声明的可用时间逐条匹配信息，保留无覆盖窗口，再比较加入外盘信息前后的扣费结果。

当前按研究工具交付。已用 PandaData 的 WTI 指标、汇率和上海原油合约完成日频回放，并在 QuantStudio 实际复算，无需注册 Databento。本次固定方案没有发现稳健的收益提升。平台记录没有历史发布时间，研究采用明确披露的等待假设，不能称为已验证的实时交易信号。代码不连接交易账户，也不生成委托。

需要 Python 3.11 或以上。

## 先跑示例

在本目录打开终端，建立独立环境后运行示例，不需要账号或数据授权。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -X utf8 scripts/analyze.py --domestic examples/synthetic/domestic.csv --events examples/synthetic/events.csv --fx examples/synthetic/fx.csv --config examples/synthetic/config.json --output outputs/example-first-run
```

成功后会生成 `outputs/example-first-run/report.json` 和 `signals.csv`，分别有 15 行汇总和 170 行明细。示例含 80 个合成日窗口，日期包含周末，只用来检查流程，不代表市场行情或真实交易日历。报告会标记 `synthetic_fixture_only`，所有方案的 `executable` 均为 false。

macOS和Linux将 `.venv\Scripts\python` 换成 `.venv/bin/python`。下文的python命令也请使用这个独立环境。输出目录已存在时程序会停止，再次运行请换一个新目录名。示例无需联网，首次安装依赖需要联网。规则检查可运行 `python -m pytest -q`；完整套件还需要PowerShell 7.5或以上，供归档采集测试调用。

再生成逐年与稳健性报告。

```powershell
python scripts/summarize_study.py --report outputs/example-first-run/report.json --signals outputs/example-first-run/signals.csv --plan examples/synthetic/reporting-plan.json --output outputs/example-summary
```

汇总工具会重新核对费用和窗口，列出年度表现、来源复用次数及配对区间。示例特意保留没有窗口的 2025 年，显示为空缺，不填成零收益。输出和统计边界见[研究报告说明](references/study-report.md)。

## 换成自己的数据

准备好符合协议的三份标准数据和配置后，使用同一入口。

```powershell
python scripts/analyze.py --domestic domestic.csv --events events.csv --fx fx.csv --config config.json --output outputs/study
```

输入和费用口径见[数据协议](references/data-contract.md)，实际工作台操作见[平台运行](references/platform-run.md)。报告比较国内滞后基线、同覆盖国内基线、时间对齐模型、额外滞后模型和故意错位的诊断模型。输出目录必须是新目录，已有结果不会覆盖。

### 使用 PandaData 导出的原始数据

已授权平台可用 `get_macro_en` 取得 WTI 价格指标、`get_currency` 取得 USD_CNY，国内合约使用 `get_future_daily`。账号和密码不写进本工具。把同一来源的分年文件合并后，可用下面的命令转换。

```powershell
python scripts/platform_inputs.py --bars sc-bars.csv --macro wti.csv --currency currency.csv --symbol EN0002967 --delay-days 2 --output outputs/platform-inputs
```

原始字段、转换规则和局限见[平台数据转换](references/platform-inputs.md)。转换保留缺失原因，拒绝重复记录，不补值。负油价保留为价格变化特征，不把它算成可投资收益。这里只准备研究输入，不会替你取得数据权限或下单。

`notional_scale` 控制名义仓位比例，本次示例明确设为 0.25；省略时为兼容旧输入使用 1。逐窗口明细保留各方案选取的数据编号、时刻、仓位和双边费用。编号按文本保存，不把长编号或前导零当作数字计算。

准备真实数据前先看[源数据检查](references/source-readiness.md)。本轮通过 EVO 已授权环境采集数据，再由 QuantStudio 0.1.36 实际执行冻结研究，两者职责分开；没有使用模拟交易账户，也没有下单。

保留的历史归档工具需 PowerShell 7.5 或以上，范围和失败处理见[原油归档资料](references/public-archives.md)。它不提供当前完整 WTI 数据，不会登录账户、下单或更改安全设置。

输入须声明时间戳来自历史存档、假设还是测试数据。包含假设或合成数据时报告强制降级；国内滞后信息晚于决策时刻时直接停止。任何本地报告的 `executable` 都为 false，单凭声明不能认证数据授权、历史可用时刻或实际成交。完整字段和候选状态的含义见数据协议。

## 这次实际验证得到了什么

2019至2022年训练，2023至2025年检验，固定使用上海原油日盘窗口。检验期有722个有效窗口，原始目标合约行情日为727个，不能把有效样本内的匹配率当作全市场覆盖率。

| 固定方案 | 检验期扣费累计净值变化 |
|---|---|
| 国内滞后信息基线 | -0.086% |
| 加入 WTI 结算价与汇率 | -0.437% |
| 改用 EIA 现货与汇率 | -0.304% |

费用和滑点每边分别为0.01%和0.02%，双边扣除，名义仓位缩放为0.25。上表是这一冻结规则下的研究净值，不是满仓资产收益，也没有计算保证金杠杆收益。

两个来源各检查五组预先固定的参数与延迟设置，再分别使用三档费用，共30个对齐模型结果均为负。没有发现稳健的收益提升。因此本轮没有采用这套外盘特征作为交易规则。这个结果可以帮助研究者判断是否值得增加模型复杂度，结论只适用于本轮的特征、模型和时段。

本地完整测试310项通过。QuantStudio实际复算生成的106个结果文件与本地逐字节一致，平台原环境另有139项测试正常结束并通过。平台完整测试受临时文件夹权限限制，没有记为全量通过。详情见[验证记录](references/validation-20261104.md)。模型审查和正式评分不作为本工具的运行条件。

## 使用限制与历史复现

平台WTI结算价指标缺逐日具体CL合约身份和换月规则，EIA现货也不能当作CL合约收益。平台汇率与美联储H.10的同日数值及缺值情况不同，不能借用H.10发布时间认证平台数据。观测日期后等待两天仍属假设，详见[来源核查](references/timestamp-evidence.md)。

国内开盘价不保证实际成交。涨跌停排队、部分成交、整数手数和保证金要求尚未完整模拟，交易日历也没有独立认证。故意错位的对照没有获利，不能证明其他输入完全没有未来信息。

后续补证需要三类材料：与当前数值逐条对应的历史发布时间及修订记录、WTI 指标的具体合约身份与换月规则、国内交易日历和成交条件的独立核验。平台能做模拟交易，不等于这些历史证明已经齐备。在补齐之前，继续按研究工具使用，不提高可交易性等级，也不因为增加测试数量而上调效果评分。

公开仓库只含代码、说明和合成示例，不含授权行情、私人平台会话或凭据。持有本轮私有数据包时，可以从Skill目录复现冻结研究。

```powershell
python -X utf8 -W error scripts/run_platform_study.py --study ../study --raw ../study/raw --output outputs/replay-first-run
```

这条入口只复现本轮固定的2019至2025年研究。其他日期和模型请使用通用分析入口，另建研究配置。数据包需使用者自行获得合法授权，不随公开代码再分发。已经查看过的2023至2025年结果不能继续当作未使用的保留样本。
