# 跨市场时间对齐研究

纽约原油昨晚涨了，研究上海原油时，能不能把这条信息用在今天的决策里？得先知道那份数据什么时候发布、什么时候能拿到。事后下载的历史表能看到完整价格，但如果当时还没公布，就不能把它算进那次决策。

这个工具把外盘信息、汇率和国内每次决策的时间对起来，比较只看国内行情和加上外盘信息的差别。每次决策用到了哪条信息，报告里都可以回查。它用来做历史研究，不连交易账户，也不下单。

## 第一次用，先跑一个示例

在 GitHub 点 **Code → Download ZIP**，解压后在能看到 `start.py` 的文件夹里打开 PowerShell。准备好 Python 3.11 或更新版本，然后依次运行下面三行。安装依赖通常需要联网，跑示例不用账号或密钥。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python start.py --demo --output outputs/first-demo
```

用 macOS 或 Linux 时，把 `.venv\Scripts\python` 换成 `.venv/bin/python`。也可以运行 `.venv\Scripts\python start.py` 打开中文菜单，选 `2` 跑示例。重复运行时换一个新的输出目录。

## 晚一次决策才收到消息，还有用吗？

1. 打开 `outputs/first-demo/使用结果.md`。在 `1 倍` 费用下，「时间对齐模型」加入了外盘信息，累计收益是 1.07%；「国内滞后基线（同覆盖）」只用国内数据、看的是同一批决策时段，累计收益是 0.27%。
2. 再看同费用的「额外滞后一窗口」。它假设消息晚一次决策才到，累计收益变成 0.27%，示例里的差距消失了。这些是构造数据，用来练习判断结果有多依赖消息到达的时间。
3. 想追查某次决策，打开 `signals.csv`。用 `decision_at` 找到那次决策，`used_event_id` 告诉你用的是哪条消息，`used_external_available_at` 记录那条消息何时可用。时间如果只是估算的，先写明假设，再尝试加长延迟，看结论会不会变。

## 换成自己的行情

1. 复制 [`templates/input/`](templates/input/) 到 `我的输入`，保留表头，填入国内行情、外盘、汇率和研究设置。[填表说明](references/prepare-inputs.md)逐列解释怎么填、数据到哪里找；也可以打开[填好的演示](examples/synthetic/)对照。
2. 先检查缺项。少了哪份文件、哪一列，或哪行必填值为空，终端会直接指出。这一步不需要装第三方依赖，也不会生成报告。

```powershell
python start.py --check-input --input-dir "我的输入"
```

3. 补齐后，用中文菜单选 `3`，或运行下面这行。缺少历史发布时间时，只能在写明延迟假设后做研究，不把行情日期当成可用时间。

```powershell
.venv\Scripts\python start.py --run --input-dir "我的输入" --output "outputs/我的结果"
```

## 文件放在哪

日常从 [`start.py`](start.py) 进。手头已有平台导出数据时，先看[怎么转换](references/platform-inputs.md)。需要核对时间和费用的精确口径时，翻[数据协议](references/data-contract.md)。

[详细入门](references/getting-started.md) · [实际研究记录](references/validation-20261104.md) · [来源核对](references/evidence-status-20261105.md) · [软件复验记录](references/validation-20261105.md)
