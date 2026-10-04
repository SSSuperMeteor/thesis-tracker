# Decision Mode 命令行 Agent 循环

## 1. 范围与模型（已定，2026-10-04）

`uv run analyze TICKER [--as-of YYYY-MM-DD]` 只分析一个标的；默认日期为运行当天。
模型仅为 DeepSeek，API 请求名固定 `deepseek-flash`。DeepSeek 的
[模型与价格页](https://api-docs.deepseek.com/quick_start/pricing/)确认该名字指向
DeepSeek-V4.1-Flash，旧名 `deepseek-v4-flash` 虽仍可请求，但模型已退役并路由到 V4.1。
同页确认 JSON 输出、工具调用和 thinking/non-thinking 均支持。
本实现使用 Chat Completions 的 thinking 模式、JSON 输出与工具调用。
API base URL 只能是 `https://api.deepseek.com`；API key 首选 shell 环境变量，
`.env` 仅沿用项目 settings 的备选读取，不写入日志、提示词或 SQLite。

## 2. 工具与日期注入（已定）

模型只看见 `get_price_history`、`get_indicators`、`get_fundamental_metrics`。
英文工具描述列清本地数据、用途、分页字段。模型可传目标 ticker 或 SPY，
可传 `limit`、`full_history`、`end_date`，不能传其它字段。工具 schema 不含 `as_of`；
若模型仍传入，Python 返回 `as_of_forbidden` 结构化错误且不调用工具。
每次实际调用由 Python 注入本次 `as_of`，原样 envelope 同时发给模型并放进
`EvidenceSnapshot`。未知工具和错误参数返回结构化错误，不导致进程崩溃。
不暴露文本检索。依据：现有三个本地工具的契约和
`tests/test_decision_agent.py` 的注入、错误参数、断网测试。

## 3. 对话、修正与限额（已定）

系统提示词版本 `decision-agent-v1-2026-10-04`。模型须给明确倾向和动作，
适用的买点、止损、目标位，至少一条可机器检查的收盘价失效条件；
事实数字只通过 `fact_id` 和 `{fact:<id>}` 占位符表达。
模型最终输出 JSON 草稿；Python 生成事实表、数据缺口、免责声明，再调用阶段一
validator。JSON 解析或校验失败时，把全部 `rule`、`location`、中文说明反馈模型。
最多修正 2 次；第三次失败即拒绝，不出卡。通过后才渲染中文卡并追加存档。

每次分析最多 12 次工具调用、16 次模型请求、累计 60,000 tokens；超限拒绝。
SDK 自动 HTTP 重试关闭，以免一次记录的模型请求实际重复发送和计费。
模型响应报告的输入、输出、缓存命中分别累计。发送前另以完整 JSON 请求的 UTF-8
字节数加 2,048 留量做保守预算闸门，并压低 `max_tokens`；这样历史页把上下文放大时
下一次请求不会先发出再发现超额。若 API 报告的用量仍越界，收到响应后拒绝。
字节闸门是安全上界估算，不是 API 官方 token 计数。

## 4. 协议依据（已定）

DeepSeek [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/) 明确要求：
请求带 `tools` 时，后续每轮都回传前轮完整 `reasoning_content`，否则 API 可返回 400。
因此循环保留每个 assistant 的 `content`、`reasoning_content`、`tool_calls` 和对应工具
响应。`tool_choice=auto`；官方 [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)
指出 thinking 模式不支持 `required` 或强制指定某个工具。
同一 API 文档定义 `model`、`system_fingerprint`、`prompt_tokens`、
`completion_tokens`、`prompt_cache_hit_tokens`、`prompt_cache_miss_tokens`；
每轮将请求名、返回名、指纹与用量追加到 `decision_model_calls`。
官方 [JSON Output](https://api-docs.deepseek.com/guides/json_mode/) 要求
`response_format={"type":"json_object"}` 且提示词包含 JSON 指令，也提醒可能返回空内容；
空内容按解析失败进入修正。模型实际返回名仍以每次 API 响应为准。

## 5. 存档与审计（已定）

通过的卡继续存入 `data/decisions/cards.db` 的 `decision_cards`，新增累计输入、输出、
缓存命中 token 字段。`decision_attempts` 逐次追加原始模型最终输出、全部违规项、
是否通过；`decision_model_calls` 逐轮追加请求/返回模型名、指纹和用量；
`decision_tool_calls` 记录实际工具名、程序注入后的参数和 envelope 状态。
四个表均用触发器禁止 `UPDATE`、`DELETE`，无 ORM 或独立迁移工具。
失败分析不存卡，但保留尝试和模型调用审计。数据库位于 `.gitignore` 的
`data/decisions/`，与可重建的 `data/cache/` 分开。

## 6. 费用口径（已定，价格须调用时重查）

按 DeepSeek [官方价格页](https://api-docs.deepseek.com/quick_start/pricing/) 的
2026-10-04 美元价估算 Flash：每百万输入缓存命中 $0.003/$0.006、
缓存未命中 $0.15/$0.30、输出 $0.60/$1.20（斜杠前为低峰，后为高峰）。
费用 = `(输入-缓存命中)×未命中单价 + 缓存命中×命中单价 + 输出×输出单价`，
各项除以一百万。官方说 UTC 周一至周五 01:00–04:00、06:00–10:00 为高峰；
其它为低峰。价格只作为报告估算，不硬编码进运行逻辑。

## 7. 本轮真实验证（待定补齐）

2026-10-04 的 AAPL 首次真实分析被拒绝：第一轮输入 1,237、输出 352，第二轮
输入 676,043、输出 3,235；累计输入 677,280、输出 3,587、缓存命中 1,536。
模型请求和实际返回均为 `deepseek-flash`，5 次工具调用、2 轮、0 次修正、0 张卡。
按上节低峰价估算此次用量约 **$0.103518408**；这是公式估算，不是账单金额。
第二轮输入过大是在响应后才发现，已经越过本轮真实验证 500k token 总上限。
新增发送前预算闸门有离线回归测试，但因总上限已用尽，本轮不会再次真实调用。
首次运行尚未持久化失败分析的工具调用明细，因此五次调用的具体工具名未留证；
现已增加 `decision_tool_calls`，但不能倒推此次明细。
NVDA、TSLA 的真实结果，以及修复后的真实 API 行为均为**未验证**。

## 8. 后续待定（待定）

待另轮授权并重置真实验证预算后，按上限重新验证 AAPL、NVDA、TSLA；
期间不放松 validator。到期评估与文本检索不在本轮实现范围。
