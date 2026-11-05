---
name: cross-market-executable-signal
description: Test one authorized foreign-to-domestic commodity relationship in QuantStudio using availability timestamps, domestic tradable windows, FX alignment, lag baselines, misalignment placebos, out-of-sample fees and coverage.
metadata:
  platform: QuantStudio
  development_status: conditional-replay-verified-not-trading-certified
  organization: https://github.com/quantskills
---

# 跨市场时间对齐与可执行信号

检查一组外盘与国内商品关系，在声明的信息时点和成本约束下能否支持研究决策。本包按研究工具交付，不提供下单功能；正式活动评分与负责人确认不作为运行前提。时间证据不足时允许输出明确标注假设的研究结果，不得称为可执行信号。

## 流程

普通使用先读[入门说明](references/getting-started.md)，使用 `start.py --check`、`--demo --output 新目录` 或 `--run --input-dir 输入目录 --output 新目录`。无参数显示中文菜单。用户已有符合协议的数据时不要求先登录平台；以下平台流程仅在需要采集或复验证据时使用。新入口不联网、不安装、不登录、不修改平台状态。正式评分与模型审查只在需要时读[可选评估](references/assessment.md)。

1. 从 QuantStudio 实际入口建立研究会话，保存任务输入输出、模型、工具调用、原始数据和产物。参考 [平台运行](references/platform-run.md)。
2. 先核验授权、时区、发布时间、供应商延迟、节假日和国内首个可交易时点。按 [源数据检查](references/source-readiness.md) 区分取数权限与时点证据；未经核验不能把日线日期当作信息可用时刻，也不能随意补时区后宣称已对齐。
3. 冻结一组关系、研究频率、训练截止时间、保留样本、模型和费用。分钟外盘不可得时明确日频范围和信息等待假设；若用于活动申报，再单独处理难度系数，不阻止普通研究使用。
4. 按 [数据协议](references/data-contract.md) 导出外盘、汇率与国内可交易窗口。
   三张表必须声明时间来源，国内滞后收益还需提供实际可用时刻。假设时点与合成数据强制降级为不可执行，不能仅凭一个带时区的字符串当作历史证据。
   优先核查 PandaAI 已授权期货及能源宏观目录；没有满足条件的 WTI 来源时，用户已授权使用 EIA WTI 现货归档方案。现货不等于 CL 合约，不延用旧期货研究结论。按[原油归档工具](references/public-archives.md)检查来源，新方案必须重新冻结配置并明确时间假设。
5. 运行分析，比较完整国内滞后基线、同外盘/FX覆盖的国内基线、按可用时刻对齐的模型、额外滞后一交易窗口的模型，以及有界的未来错位安慰剂。名义仓位比例在配置中明确写定，不用测试效果反推比例。
6. 安慰剂永远不可执行。匹配失败的国内窗口保留为零仓位并计入覆盖率分母；缺国内标签、任何方案不足 20 个训练窗口或测试不足 10 窗口时停止，不能填零伪造模型。核对各方案选取来源字段，不把共同的当前对齐编号误作滞后/未来方案的输入。
7. 按 [研究报告说明](references/study-report.md) 核对 report.json 与 signals.csv，生成逐年、来源复用与配对区间报告。正式研究须事先冻结报告年份、时区和重抽样设置，不根据保留结果删年或换区块长度。来源重复使用不等于多个独立发布，汇总工具不能认证缺失的交易日历或原始数据。
8. 回看具体信号是否在国内首个可交易时点之前已可得。计入双边费用和滑点，再说明相对基线改变的决策和适用范围。

```powershell
python scripts/analyze.py --domestic domestic.csv --events events.csv --fx fx.csv --config config.json --output outputs/study
```

Granger 或相关性统计可作为补充诊断，不能代替样本外扣费交易检验。2026-11-04 已完成 PandaData 真实数据条件性回放及 QuantStudio 独立复算，106 个输出文件与本地一致；冻结方案未发现稳健的扣费增量优势。完整平台测试仍有临时目录权限限制，详细证据与限制见[本轮验证](references/validation-20261104.md)。程序输出不自证执行，`executable` 始终为 false；`time_causal_candidate` 仅描述依声明通过时点检查的候选，不等于已核验可交易。
