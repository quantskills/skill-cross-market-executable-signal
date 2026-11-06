# 时间与数据协议

第一次准备输入可从[空白模板和填表说明](prepare-inputs.md)开始。

外盘 `events.csv` 包含 `event_id,available_at,external_return`。`available_at` 是平台实际能使用该观测的时间，包括收盘、发布和供应商延迟，必须带时区。汇率 `fx.csv` 同样包含 `fx_id,available_at,fx_return`。

event_id、fx_id 和 window_id 必须非空且各表内唯一，按文本读取和保留。长整数、前导零和文字“NA”都是标识，不作数字运算。直接调用 Python 函数时也应从原始标识构建文本或整数列，不能先转浮点数后再转回；已经丢掉的精度无法恢复。用表格软件查看信号文件时，把编号列设为文本。

三张表都必须包含 `available_at_basis`：供应商历史存档填 `vendor_archive`，交易所存档填 `exchange_archive`，人为假设填 `assumed`，测试数据填 `synthetic`。只填带时区的日期不算来源证明；存档路径、版本和散列另附。程序没有独立认证这些声明的能力，输出中的 `time_source_independently_verified` 始终为 false，须凭源文件完成验收。

任一输入含 `assumed` 时，结果强制标记 `hypothesis_only`；否则，含合成数据时标记 `synthetic_fixture_only`。任何本地分析的 `executable` 都为 false。全部声明为历史存档时，非安慰剂方案可有 `time_causal_candidate=true`，仅表示按所声明字段通过候选时点检查，不证明许可、时间来源或成交。`time_source_independently_verified` 始终为 false，实际核验另留来源与平台证据。

国内 `domestic.csv` 包含下列字段。

| 字段 | 含义 |
|---|---|
| window_id | 可交易窗口唯一编号 |
| decision_at | 作出决策的时刻 |
| entry_at | 首个真实可执行进场时刻 |
| exit_at | 冻结退出时刻 |
| asset_return | 从进场到退出的未扣费收益 |
| local_lag_return | 决策之前已经可见的国内滞后收益 |
| local_lag_available_at | 国内滞后收益实际可用时刻，带时区且不得晚于 decision_at |

需逐条核验 `local_lag_return` 的来源截止时刻，当前程序不能从一个收益数字自动证明其来源。所有时间必须带时区；程序统一换算 UTC。国内窗口不能重叠，默认逐窗口建仓并平仓，费用按双边计算。跨夜持仓或连续净仓位需另一个经确认的执行适配器，不能套用本口径。

窗口来自实际交易日历或可交易行情，不使用普通工作日推导。外盘和汇率分别向后匹配至 `decision_at`，默认最长陈旧时限 96 小时，必须为正有限值，研究前应根据节假日和频率冻结。相同信息时刻有多条记录时，先按可审计规则聚合，程序不任意挑一条。

配置如下。

```json
{"train_start":"2019-01-01T00:00:00+08:00","train_end":"2022-12-31T23:59:59+08:00","test_end":"2025-12-31T23:59:59+08:00","max_age_hours":96,"ridge":1.0,"notional_scale":0.25,"fee_rate":0.0001,"slippage_rate":0.0002}
```

这是配置示例，不是新来源已经核验或正式冻结的证明。train_end、fee_rate、slippage_rate 必填；train_start、test_end 可选，提供时须为带时区时刻，满足 train_start < train_end < test_end。训练窗口要求决策不早于起点、退出不晚于截止；测试窗口要求决策晚于截止、退出不晚于终点。跨界窗口剔除，完整范围外窗口另计。全表基础数据先验检查仍会拒绝无效国内标签，不根据这些标签的好坏筛日期。

ridge 为有限非负值，默认 1。notional_scale 为 (0,1] 内有限数，省略时为兼容旧输入使用 1；研究应明确填写，不根据保留结果更改。仓位 = notional_scale × clip(预测收益/训练标签波动, −1, 1)，波动尺度下限为 1e-6。未匹配窗口仓位为零。费用是每单位实际方向仓位的单边收益率，基础成本分别为 2×abs(position)×fee_rate 和 2×abs(position)×slippage_rate；不把资产收益和费用重复缩放。

## 五组对照

| scheme | 特征和覆盖 |
|---|---|
| domestic_lag_baseline | 只用国内滞后收益，全部合格国内窗口 |
| domestic_lag_matched_baseline | 仍只用国内滞后收益，但训练和测试资格都与 aligned 相同，用于分离日期覆盖与外盘特征效果 |
| aligned | 国内滞后收益、分别向后匹配的外盘和 FX |
| extra_session_lag | 外盘/FX精确滞后一个国内窗口，不跳过缺口；相对当前决策重新检查年龄，节后过期则禁用 |
| future_misalignment_placebo | 严格向前匹配外盘，最长仍为 max_age_hours，排除恰好相同时刻；FX仍向后匹配，永远不可执行 |

安慰剂的训练特征可用时刻不得晚于训练截止。各方案至少需 20 个有效训练窗口，国内测试分母至少 10 个窗口；不足时明确报错，不把未训练模型当成零收益策略。所有方案保留相同的合格国内测试分母，覆盖不足时零仓位，不单报匹配成功日期的收益。

## 输出与追溯

report.json 包含训练数量、覆盖率、跨界/范围外数量以及 1/1.5/2 倍费用对照。signals.csv 每行对应一个方案、一个测试窗口，包含仓位、预测、asset_return、gross_return、fee_return、slippage_return、net_return。明细为一倍费用：net_return = position×asset_return − fee_return − slippage_return。

aligned_event_id、aligned_fx_id、external_available_at、fx_available_at 只是当前窗口的共同向后匹配参照，不能拿来解释所有模型的输入。方案选取来源看 used_event_id、used_external_available_at、used_fx_id、used_fx_available_at、used_external_return、used_fx_return；两个国内基准的这些字段为空。

matched 表示该方案是否具备特征使用资格。失配行可保留选取的候选来源，以解释超龄或缺口，但这些来源没有用于该行预测/交易。external_age_hours、fx_age_hours 由方案选取时刻相对当前决策计算；未来安慰剂的外盘年龄为负，两个国内基准无外盘年龄。这些编号是分析选取记录，不是成交回执。

输出必须写入尚不存在的目录，不能覆盖旧研究。未来错位安慰剂仅暴露错配风险，禁止交付为可执行信号。
