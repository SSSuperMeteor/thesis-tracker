# Decision Mode 命令行 Agent 循环

## 1. 范围与模型（已定，2026-10-04）

`uv run analyze TICKER [--as-of YYYY-MM-DD] [--horizon short|mid|long]` 只分析一个标的；
默认日期为运行当天，默认周期 `mid`（2026-10-04 新增 `--horizon`）。
模型仅为 DeepSeek，API 请求名固定 `deepseek-flash`。DeepSeek 的
[模型与价格页](https://api-docs.deepseek.com/quick_start/pricing/)确认该名字指向
DeepSeek-V4.1-Flash，旧名 `deepseek-v4-flash` 虽仍可请求，但模型已退役并路由到 V4.1。
同页确认 JSON 输出、工具调用和 thinking/non-thinking 均支持。
本实现使用 Chat Completions 的 thinking 模式、JSON 输出与工具调用。
API base URL 只能是 `https://api.deepseek.com`；API key 首选 shell 环境变量，
`.env` 仅沿用项目 settings 的备选读取，不写入日志、提示词或 SQLite。

## 2. 工具、基础包与目录（已定，2026-10-04）

模型只看见 `get_price_history`、`get_indicators`、`get_fundamental_metrics`。
英文工具描述列清本地数据、用途和分档历史。目标 ticker 可用于默认页和分档历史；
**SPY 只可用于默认最新页**，分档历史只支持目标 ticker，schema 与系统提示词都写明这一点。
历史须显式传 `resolution` 和 `fields`；`fields` 枚举按工具分开：`get_price_history`
只接受 `close`/`adjusted_close`，`get_indicators` 只接受 `rsi_14`/`macd_histogram`/
`volume_ratio_20`。schema、目录文字和 `history_view` 共用 `evidence.TOOL_HISTORY_FIELDS`
一个来源，所以宣传的枚举不会再和分发层漂移（见第 8 节）。旧 `full_history` 原始页不在
模型工具 schema 中；若仍请求，返回可修正的 `resolution_required` 错误，消息里列出可选
resolution 与该工具支持的 fields。工具 schema 不含 `as_of`；若模型仍传入，Python 返回
`as_of_forbidden` 结构化错误且不调用工具。所有工具参数错误都返回结构化错误交回模型继续，
消息必须给出可操作提示（哪个参数、可选值是什么），不得终止整次分析。
每次实际调用由 Python 注入本次 `as_of`。程序循环前预取三个工具的默认页，
再按需取五年价格原始行和财务同比来源行；预取次数单独统计，不占模型的 12 次。
（2026-10-04 核实：基础包本身不随周期变化，只有下面这个档位随周期变化。）
除默认页外还按 `--horizon` 预取一个价格历史档位：`short`→`weekly_3m`、
`mid`→`monthly_2y`、`long`→`quarterly_5y`，结果放进基础包的 `horizon_history`，
并在 `snapshot.calls` 记一条 `get_price_history_tier`、在 `evidence_windows` 记一条同分辨率窗口；
它计入预取次数，字节上限仍是 32 KiB。模型仍可请求其它档位。
默认页、价格来源页及指标所选历史行的精确值与确定性派生事实进入 `EvidenceSnapshot`；模型只收到有编号的
`base_pack` 和不含观测值的 `catalog`，之后收到至多 32 KiB 的精简工具视图。
目录列所有指标、财务指标、定义、可用分辨率和约数 KB；基础包列最新价格、
各指标最新值、财务最新一期及可得的去年同期、1/3/6/12 月回报、52 周价格位置、
52 周/3 年/5 年高低价及日期、SMA200。缺数据保留原因，不猜值。
未知工具和错误参数返回结构化错误，不导致进程崩溃。
不暴露文本检索。依据：现有三个本地工具的契约和
`tests/test_decision_agent.py` 的注入、错误参数、断网测试。

## 3. 对话、修正与限额（已定）

系统提示词版本 `decision-agent-v5-entry-stop-rules-2026-10-04`。v4 → v5 只改四点，各有证据：
把本次请求的周期写进提示词并要求 `horizon` 等于它（对应 AAPL 第 2 稿 D13）；
加一组 D02 正例/反例并点明“200 日线”“63 日”这类窗口天数也算裸数字
（对应 NVDA 6 条、更早 TSLA 6 条 D02）；写明买入/分批现价必须在买点区间内、止损必须等于
第一条 `close_below` 阈值、失效条件文字只是对机器条件的说明（对应新规则 D14/D15）；
补一句 JSON 必须完整闭合、`fact_ids` 不要堆成长列表（对应 AAPL 第 1 稿 D00：JSON 在
`fact_ids` 数组中间被截断）。未放宽任何校验规则。模型须给明确倾向和动作，
适用的买点、止损、目标位，至少一条可机器检查的收盘价失效条件；
事实数字只通过 `fact_id` 和 `{fact:<id>}` 占位符表达。
买入、分批、持有还须给出 `stop_rationale` 与 `target_rationale`（各含一个事实占位符）；
中性倾向可用动作 `观望` 且三个价位留空。
模型最终输出 JSON 草稿；Python 生成事实表、数据缺口、免责声明，再调用阶段一
validator。JSON 解析或校验失败时，把全部 `rule`、`location`、中文说明反馈模型。
最多修正 2 次；第三次失败即拒绝，不出卡。通过后才渲染中文卡并追加存档。

每次分析最多 12 次模型工具调用、16 次模型请求、2 次修正。累计 token
硬上限 1,500,000；超过 1,000,000 后不再执行新工具调用，只能收尾或修正；
若响应仍越过硬上限，则拒绝。单次请求预估输入上限 200,000 tokens。
SDK 自动 HTTP 重试关闭，以免一次记录的模型请求实际重复发送和计费。
模型响应报告的输入、输出、缓存命中分别累计。发送前把完整 `messages` 和
`tools` 序列化成 UTF-8 JSON，按 **1 字节至多估 1 token，再加 2,048 token**
作聊天结构留量；若超过单次 200,000 或累计剩余额度，不发送请求，记录闸门原因。
每次 API 响应后用返回的 usage 更新累计值并复查。字节闸门是保守估算，
不是 API 官方 tokenizer；输出由 `max_tokens` 和剩余额度约束。

历史请求按 `daily_10`、`weekly_3m`、`monthly_2y`、`quarterly_5y` 分档，
每次不超过 30 行，只返回请求字段。短期默认建议近约三个月，中期近两年，
长期近五年；模型可在字节和 token 预算内另取档位。分档改变时间分辨率，
不赋予历史事实不同可信度。原始完整历史页不能直接送模型；即使单条超过
32 KiB，也返回 `truncated: true` 和人话原因，不静默裁切。

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
是否通过，以及本次命令行请求的周期 `requested_horizon`（2026-10-04 新增列，
旧库用 `ALTER TABLE` 补列，旧行该列为 NULL）；`decision_model_calls` 逐轮追加请求/返回模型名、指纹和用量；
`decision_tool_calls` 记录每次模型工具请求名、参数和 envelope 状态，包括参数错误。
卡的 `evidence_windows` 由 Python 记录基础包与实际请求的窗口、分辨率、字段和展示行数。
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

## 7. 根因证据与本轮真实验证（已定记录，2026-10-04）

2026-10-04 的 AAPL 首次真实分析被拒绝：第一轮输入 1,237、输出 352，第二轮
输入 676,043、输出 3,235；累计输入 677,280、输出 3,587、缓存命中 1,536。
模型请求和实际返回均为 `deepseek-flash`，5 次工具调用、2 轮、0 次修正、0 张卡。
按上节低峰价估算此次用量约 **$0.103518408**；这是公式估算，不是账单金额。
旧审计库只有两轮 usage，没有保存那五次工具调用的参数，故**不能证明**旧运行
具体哪一次请求了什么。离线同日 AAPL 测得默认价格/指标/财务页分别为
18,751/7,657/6,692 字节；100 行指标历史页为 713,471 字节，100 行价格页
93,073 字节。代码旧路径 `decision/agent.py` 的 `_dispatch` 原样返回 envelope，
`run_analysis` 将整个 envelope 序列化进 tool 消息并保留在后续消息中；
五次调用的离线示例（其中一次指标历史和一次价格历史）第二轮请求 JSON 为
936,360 字节。这验证了造成巨大上下文的机制，但不是旧请求参数的复原。
改动前 `ee5f510` 的 `decision/agent.py:251` 返回原 envelope，`:325` 选择原 envelope，
`:328–329` 将其写入累积消息；`indicator_tool.py:43` 历史模式默认 100 行，
`:74–85` 选择历史计算日，`:100–104` 将逐日全指标记录放入返回。

同一 `as_of` 的离线 AAPL 对照，按实际模型 tool 消息的 UTF-8 JSON 字节计：

| 工具/形态 | 改动前 | 精简视图 |
|---|---:|---:|
| 默认价格 | 18,751 | 271 |
| 默认指标 | 7,657 | 5,285 |
| 默认财务 | 6,692 | 1,566 |
| 价格历史：逐日/周/月/季 | 原 100 行 93,073 | 3,738 / 5,175 / 9,125 / 7,691 |
| 指标历史：逐日/周/月/季 | 原 100 行 713,471 | 5,412 / 7,510 / 13,280 / 11,187 |

价格历史含原始/复权收盘价；指标历史含 RSI、MACD 柱、量比。四档分别为
10/14/25/21 行，均未截断。最终基础包 10,083 字节，目录 3,652 字节，
还保留全部缺口状态及截断标记；
真实调用时的基础包尺寸以当次审计为准。

本轮首次真实 AAPL（`as_of=2026-10-04`）输入 6,712、输出 505、缓存命中 0，
首轮模型请求/返回名均为 `deepseek-flash`；模型发起 4 次工具请求，前 3 次本地
错误 envelope 各 249 字节（两次价格、一次指标），第 4 次触发旧 `full_history`
拒绝逻辑，未发送第二轮；0 次修正、0 张卡。基础包 9,852 字节、目录 3,485
字节。根因是当时工具 schema 仍宣传 `full_history`，分发层却把它当作预算失败
立即终止；现移除该 schema 字段并将旧请求改为可修正的 `resolution_required`。
遵照“因工具返回大小失败即停止”规则，本轮不再调用 NVDA/TSLA；该修正后的
真实 API 行为仍未验证。按第 6 节官方低峰价估算，这次约 $0.0013098。

## 8. 第二轮离线复核与协议修复（已定记录，2026-10-04）

### 8.1 真实 API 本节未运行（未验证）

本轮 shell 环境没有 `DEEPSEEK_API_KEY`（`printenv` 为空）。按任务规定，真实 API
验证整节跳过并标 **未验证**：AAPL → NVDA → TSLA 一次都没有真实调用，因此
"模型改用分档请求后能否正常完成分析"仍然未知。没有产生任何真实 token 消耗。

### 8.2 对上一轮二手信息的核实

审计库 `data/decisions/cards.db`：

- `decision_model_calls` 3 行。其中 `5d4a3ebd-…` 第 1 轮 = 输入 6,712 / 输出 505 /
  缓存命中 0，请求名与返回名都是 `deepseek-flash`，与上一轮报告的"1 轮、6,712/505"一致。
- `decision_tool_calls` **0 行**，`decision_attempts` **0 行**。该表由 `dfd5fff`
  引入，而那次运行早于它。因此**旧运行没有留下工具参数记录**，"哪一次到底传了什么"
  无法从审计复原。上一轮报告里"前 3 次 249 字节错误"的说法本身有一处不准确：它把
  4 次调用记成"前两次价格、第三次指标"，但参数无从查证。
- 上一轮报告中 "AAPL/NVDA 原有 57/58 个 fact_id 与精确值逐项相同" 含义已核实为：
  **AAPL 快照共 57 个 fact_id，NVDA 共 58 个，逐项数值与基线 fixture 相同**（见 8.5）。

### 8.3 249 字节错误的根因（离线复原）

把当前 `_dispatch` 能产生的全部错误消息逐条算成 envelope 字节数，**只有一条恰好
249 字节**：

```
invalid_tool_arguments: 分档历史只接受目标标的、resolution 和 fields。
```

旧代码里这一个分支同时由 5 个条件共用（`name == "get_fundamental_metrics"`、
`symbol != ticker`、`end_date is not None`、`full_history`、`limit is not None`），
所以前 3 次调用**都落在同一分支**，报错原因不唯一。两种最可能的触发都在当前 repo
离线复现了：

1. **SPY 分档历史**：工具 schema 的 `ticker` 描述写 "use the target or SPY"，
   系统提示词写"可调用目标股票与 SPY"，但分发层拒绝 `SPY + resolution` ——
   `_dispatch` 的 `symbol != ticker` 分支，返回上面那条 249 字节错误。
2. **旧参数与 `resolution` 混用**：`limit`/`full_history`/`end_date` 已从 schema
   删掉，模型仍可能带上，命中同一分支。旧运行第 4 次调用直接用 `full_history`
   触发 `resolution_required`。

结论：三个 249 字节错误属于同一类缺陷——**模型可见的说明/schema 宣传了分发层
不接受的能力**。修复方向是让宣传面与分发层一致，而不是放宽分发层。

### 8.4 本轮修复（只改模型可见契约与错误消息）

1. `fields` 枚举按工具拆分，`history_view`、工具 schema、目录文字共用
   `evidence.TOOL_HISTORY_FIELDS` 一个来源。
2. schema `ticker` 描述与系统提示词写明 "SPY 只可用于默认最新页"。
3. 目录文字由 `history_field_help()` 生成，按工具列出支持的字段。
4. 全部工具错误消息给出可操作提示（未允许的字段名、可选 resolution、该工具支持的
   fields、要加/要去的参数）。
5. 系统提示词版本 `v2 → v3`，`evidence_windows` 之外不改卡片协议。

**没有改动的**：`validate_card` 与全部 D 规则、卡片字段与价位规则、8 个指标、
resolver、Stage 1/2/3、冻结基线、`full_history` 的拒绝行为（只是消息更具体）。
`history_view` 的字段白名单数值不变，只是提取成常量。

### 8.5 离线验收数字（2026-10-04）

fact_id 回归（`capture_snapshot` + `fact_index`，as_of `2026-10-04`，对比基线
fixture `tests/fixtures/decision_baseline_2026-10-04.json`）：

| ticker | 改动前总数 | 改动后总数 | 相同 | 新增 | 消失 | 值变化 |
|---|---:|---:|---:|---:|---:|---:|
| AAPL | 57 | 57 | 57 | 0 | 0 | 0 |
| NVDA | 58 | 58 | 58 | 0 | 0 | 0 |

新增测试：`tests/test_decision_tool_contract.py`（23 项）覆盖"schema/说明/提示词
宣传的每个参数与枚举值分发层都接受"、"错误消息可操作"、"错误不终止分析"；
`tests/test_decision_evidence.py` 增加按 ticker 的 fact_id 增删改计数断言。
全量 `pytest` 473 → 498 通过，`ruff` 干净，stress runner 仍 580/952 = 60.9244%，
`eval/stage3_stress_8metric.json` sha256 仍为
`d247434ddd8ed2086dda4a73cca51e5b7f144cf6e6d8d3a7bacfc446b3ffe237`。

## 9. 第三轮：三张真实卡的违规复核与规则加严（已定记录，2026-10-04）

本轮**没有任何真实 API 调用**：Harness 环境读不到 `DEEPSEEK_API_KEY`，真实运行由用户
在自己的终端执行。以下全部来自 `data/decisions/cards.db` 的存档记录与离线回放。

### 9.1 三张真实卡的违规汇总（已定）

三次真实分析各"修正 1 次"即通过。第一稿被拒的规则、位置与内容全部来自
`decision_attempts.violations_json`：

| 运行 | 标的 | 第一稿违规 |
|---|---|---|
| `48a9ff03` | AAPL | `D00 / entry_range`：买点须是两个价位或 null。实际写成 `{"low":326,"high":335}` 对象 |
| `5fb24ad9` | NVDA | `D00 / entry_range`：同上，实际写成 `{"low":223.75,"high":236.0}` |
| `ae4bccea` | TSLA | `D02` ×6：`reasons[0]` 事实数字未用占位符，`reasons[2]/[3]/[5]` 与 `invalidations[0]` 含裸数字，`invalidations[1]` 事实数字未用占位符；另 `D05 / entry_range/stop_loss/target_price`：持有须无买点区间（实际给了 `[347.5,365.0]`） |

**反复违反的证据**：`entry_range` 形状在 3 次首稿里错了 2 次（AAPL、NVDA），
所以 v4 提示词的 schema 示例把 `entry_range` 改成 `[300,320]` 数组形式并明确
"不得写成对象"——这是本轮唯一针对违规证据的提示词改动。D02 只在 TSLA 一次出现
（虽 6 处），证据不足以单独改提示词，只保留既有措辞。

### 9.2 三张存档卡的 D11 回放（已定）

用 `validate_card(..., version="decision-validator-1")` 回放，检查新规则 D11：

| 卡 | 动作 | D11 | 命中位置与内容 |
|---|---|---|---|
| AAPL `68405fbc` | 买入 | **被拒**（符合预期） | `reasons[4].text`：动作词"分批"（"故采用分批建仓…"） |
| NVDA `f904ecd9` | 买入 | **被拒**（符合预期） | `reasons[5].text`：动作词"分批"（"故采用分批而非一次性买入"） |
| TSLA `241be83d` | 持有 | **不触发**（符合预期） | 理由正文无其它动作词；失效条件里的"转为回避""可上调为分批买入"不检查 |

回放同时发现：三张 v1 卡在新显示规则下会额外报 `D01 / facts`，根因是**仅 `display`
字符串变了**（旧卡用旧的显示函数写入）。逐项核对：**0 条事实的精确值或 `fact_id` 变化**，
TSLA 卡 6 条事实的 `display` 不同（RSI 4→1 位、ratio 类改为 %/倍）。
D01 不做放宽，因此"用新校验器全量重校旧卡"必然报这一条；这是显示规则变更的已知后果，
不是数值问题。用 v1 版本回放则不触发 D12/D13。

### 9.3 本轮加严的规则与显示（已定）

D11（理由正文动作一致）、D12（止损/目标依据）、D13（周期一致）、动作 `观望`、
校验器版本 `decision-validator-2`，以及单位中文化、指标中文名、比率类显示与
"自动计算（Python）"一节，全部见 [decision-mode.md](decision-mode.md) 第 10、13 节。

### 9.4 离线验收数字（2026-10-04）

`pytest` 498 → **581 通过**，`ruff` 干净，stress runner 仍 580/952 = 60.9244%，
`eval/stage3_stress_8metric.json` 的 sha256 仍为
`d247434ddd8ed2086dda4a73cca51e5b7f144cf6e6d8d3a7bacfc446b3ffe237`。
AAPL 57 / NVDA 58 个 `fact_id` 逐项不变、0 冲突。

## 10. 第四轮：第二批真实卡复核（已定记录，2026-10-04）

本轮**没有任何真实 API 调用**：Harness 环境读不到 `DEEPSEEK_API_KEY`，真实运行由用户在自己的
终端执行。以下全部来自 `data/decisions/cards.db` 的存档记录、源码只读核实与离线回放。

### 10.1 三张 v4 真实卡的违规汇总（已定）

| 运行 | 标的 | 周期 | 稿次 | 违规 |
|---|---|---|---|---|
| `c2658304` | AAPL | 短期 | 1 | `D00 / model_output`：输出不是有效 JSON。raw 3431 字节，`json.loads` 报 `Unterminated string starting at line 1 column 3397` —— JSON 在 `fact_ids` 数组中间、一个未闭合字符串处结束（末尾是 `"sec_metric|AAPL|net_debt_to_ebitda`） |
| | | | 2 | `D13 / horizon`：`horizon` 字段写成 `中期`，请求周期是 `短期` |
| | | | 3 | 通过 |
| `d1ab4f31` | NVDA | 长期 | 1 | `D02` ×6：`reasons[1]`（事实数字未用占位符）、`reasons[2]`、`invalidations[0]`、`invalidations[1]`、`stop_rationale`、`target_rationale`（裸数字，多为“63 日/252 日/200 日”这类窗口天数） |
| | | | 2 | 通过 |
| `44dac4e2` | TSLA | 中期 | 1 | 无（一次通过，动作观望） |

### 10.2 单轮输出上限：有无被截断的证据（已定）

- `agent.py` 发给 API 的 `max_tokens = min(8192, remaining - estimated_input)`。
- `decision_model_calls` 表**没有** `finish_reason` 或等价字段（列为 `model_call_id`、
  `analysis_id`、`round_no`、`requested_model`、`returned_model`、`fingerprint`、
  `input_tokens`、`output_tokens`、`cache_hit_tokens`、`created_at`）。`DeepSeekClient.complete`
  返回了 `finish_reason`，但 `run_analysis` 没有落库。
- 观测到的 `completion_tokens`：AAPL 第 2 轮 **8192**（正好等于配置上限），
  NVDA 第 2 轮 **8103**（不是上限）。
- **结论：没有明确证据证明被截断。** 8192 只是"等于上限"这一可疑事实；同一轮的 `content`
  是完整可解析的 JSON（被解析出来并因 D13 被拒），说明内容本身闭合；没有 `finish_reason`
  就无法区分 `stop` 与 `length`。
- 因此**本轮不改 `max_tokens`**。若要拿到证据，最小改动是给 `decision_model_calls`
  增加一列记录 `finish_reason`，留待有真实运行的一轮。

### 10.3 价格库刷新机制（只读核实，未改代码）（已定）

`ingest_price_history`（`prices.py:176`）对每个 symbol：

- **跳过**的唯一条件是已存窗口完全覆盖请求窗口
  （`stored_start <= start_date and stored_end >= end_date`），此时不发请求；
- 否则按 union 窗口 `[min(start_date, stored_start), max(end_date, stored_end)]`
  **整段重抓**，并且先 `DELETE FROM daily_prices / price_windows WHERE symbol=?` 再整段插入 ——
  **不是增量追加**。
- `prices_batch.run_batch` 每次最多 `max_requests=10` 个请求，CLI 未暴露该参数；
  universe 是 15 个 ticker + SPY = 16，所以一次跑不完，需要跑两次。

刷新命令：`uv run prices-ingest`，再重复一次（第二次会跳过已刷新的 10 个、处理剩下 6 个）。
D03 要求价格 `data_end_date` 与 `as_of` 相差不超过 5 个日历日，因此需要定期手动刷新，
否则 `analyze` 会因数据过期被拒。

## 11. 后续待定（待定）

后续真实验证需再次从 AAPL 开始：确认模型在 SPY/分档参数被拒后能改用目标 ticker 的
分档请求，并跑完一轮出卡；本轮没有取得通过的真实卡，也没有真实 token 消耗。
到期评估与文本检索仍不在本轮实现范围。
