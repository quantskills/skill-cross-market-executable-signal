# 原油日频归档资料

本页保留早期 WTI—SC 归档方案的工具和边界，不再是本轮正式取数入口。[EIA 官方期货页](https://www.eia.gov/dnav/pet/pet_pri_fut_s1_d.htm)说明 2024-04-05 之后的期货价格不可得；已保存的近期页面也曾链接旧报价，不能按页面日期冒充新数据。

用户已选择改用另一授权来源，旧采集批次和冻结文件保留，不再扩大旧 EIA 采集。新来源及许可核验后须重新冻结；下面的两天规则不自动适用于新供应商。其他标的或数据源按主数据协议处理。

## 原始来源

- [EIA 周报归档目录](https://www.eia.gov/petroleum/supply/weekly/archive/)：每份报告的表 13 保留 NYMEX 具体交割月的日价格。应从报告页面中“NYMEX Futures Prices”所在行取得 CSV 链接，不凭表号猜下载地址。
- [美联储 H10 归档目录](https://www.federalreserve.gov/releases/h10/)：保存逐次发布的汇率表，人民币方向为 CNY/USD。页面加载的 releaseDates.json 是其实际使用的发布日期索引。

这些资料有公布延迟，并非实时行情。某个观测日的数值要等其所在报告发布后才可使用。EIA 文件的 Last-Modified 还可能早于公开发布，不能直接当作可用时间。

美联储的索引日期也未必等于正文发布日期：已实际遇到目录和网址为 2018-09-03、正文为 2018-09-04 的假日记录。索引日期用于定位和核对缓存，正文日期单独保存并用于等待期计算；不按网址擅自赋予公布时刻，也不把日期不同一律当成缺数据。

## 收集器的使用范围

`scripts/collect_archive_snapshots.ps1` 从正常终端运行，需 PowerShell 7.5 或更新版本。它是公开源文件导出步骤，不要求登录，不放入账户密码或模型密钥。QuantStudio 随后的计算读取保存好的文件；终端导出与平台计算的证据分别记录。

研究目录需有 `config.json`、`PREREGISTRATION.md`、`scope-freeze.json` 和从官方目录取得的 `archive-jobs.json`。配置和事前说明的 SHA256 必须与冻结清单一致。`archive-jobs.json` 分别保存 eia、fed 数组，每条有 source、link_date、url。不能把一份任意网址列表称为已核实的官方目录。

```powershell
pwsh -NoProfile -File scripts/collect_archive_snapshots.ps1 -StudyRoot "C:\research\wti-sc" -Source eia -Offset 0 -Limit 5
pwsh -NoProfile -File scripts/collect_archive_snapshots.ps1 -StudyRoot "C:\research\wti-sc" -Source fed -Offset 0 -Limit 5
```

每次最多处理 50 份，Offset 对应已保存清单中的位置。默认每份真实请求至少间隔 1.25 秒，不并发运行同一来源。已保存文件会先核对再复用，不重复请求；一次运行没有输出，不能据此推断全部已完成，需对照清单和完成回执数量。

## 出错时

- 网站拒绝访问、跳转、超时或页面格式变化时停止，保留失败回执。不换身份、代理或客户端继续试，也不关闭证书校验。
- 缓存或完成回执缺失、指纹不一致时停止，不自行补下载覆盖旧材料。先核实原文件，确需新版本时另建研究目录。
- 2MB 是下载后的接收检查，不是传输中的硬截断。它适用于已限定的官方小文档；超限的 `.part` 会留存供检查，不能称为完整、可解析的数据。
- 当前 Windows QuantStudio 的受限 PowerShell 曾遇到系统 HTTPS 凭证错误；这是工具运行环境问题，不等于 PandaData 账号无权限。正常 HTTPS 和默认校验应保留，可使用已授权导出文件作为输入，不扩大系统权限来跑通。

## 日期上界与验收

早期归档方案的冻结规则取报告发布日期、当前文件修改 UTC 日期中的较晚日期，再等两天，于 00:00 UTC 视为可使用。`archive_sources.conservative_available_at` 只计算这个界限：缺修改时间、缺时区或元信息晚于获取时间会报错，不猜测补齐。

这一界限不是精确历史接收时刻。源文件、归档日期、版本、报价方向和国内首个可交易窗口仍需核实。收集回执只证明保存了哪份文件，不证明已有交易优势或已完成市场回测。

主分析器目前没有把这个归档日期界限认证为精确存档时刻；也不能为绕过字段检查就填 vendor_archive。继续使用此类界限前，须先确认新的研究范围、相应时间来源类型和代码支持，不能把旧归档工具能解析文件解释为正式研究已接通。

原始资料默认留在研究环境，不自动放入公开压缩包。EIA [再利用说明](https://www.eia.gov/about/copyrights_reuse.php)还区分政府内容与第三方受保护材料；发布时应保留出处，并另行核对价格资料的再分发条件。
