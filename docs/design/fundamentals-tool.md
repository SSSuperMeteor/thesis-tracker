# 财务指标工具的披露时点语义

**已定（2026-10-03）**：`get_fundamental_metrics(ticker, as_of=...)` 从本地
`data/cache/financial_facts.db` 只读获取 SEC 事实快照，再调用原有八个确定性指标。
`as_of` 是日期，含当天；只允许 `filed_at <= as_of` 的 filing 和事实参与计算。
期间结束日不能代替披露日。最新值对应截至该日已披露的最新财期；
`data_end_date` 显示那份 filing 的披露日，不自动判断过期。

## 采集与读取

`python -m scripts.collect_fundamental_snapshots --since 2015-01-01` 是显式采集步骤，
逐个使用现有 `FilingSelector`、SEC identity 和 edgartools 节流逻辑读取周期申报，
提取现有注册概念的 XBRL 事实。本轮环境中 edgartools 的限速是每秒 9 次；
采集串行运行，低于 [SEC 公布的每秒 10 次上限](https://www.sec.gov/about/developer-resources)。
采集写入独立 SQLite 表 `filing_snapshots` 和 `financial_fact_snapshots`，
不改 `corpus.db` 或其他既有表。
每条事实保留 accession、filed_at、retrieved_at 和完整原始来源字段。
同一 accession 已存在时跳过再次读取 XBRL；新 accession 以事务追加。
若再次提交同一 accession 却带来不同元数据或事实，入库会拒绝冲突。
这与复权价格的整段替换不同：披露的 filing 不会因下一次披露而重算旧 accession，
每条事实的 accession 与 filed_at 足以按时点筛选。SQLite 建表即用，
`.gitignore` 忽略 `data/`；无需 ORM 或迁移工具。

修订申报与原始申报均保留。读取时先按披露日过滤，再按现有
`sec_source._select_financial_member` 的可用财务内容规则选择一个申报成员：
注册事实数多者优先，并列时按披露日、SEC 受理时间和 accession 排序。
这使只包含封面股数的稀疏 10-K/A 不会清空原申报的财务事实；完整修订会替代原值。
同一天的多个受理时间均视为当天已公开，后受理者胜出。
Q4=年报减前三季累计及其他派生值只使用可见的来源；10-K 披露前没有该 Q4。

读取入口以 SQLite `mode=ro` 打开快照库，禁用 gross margin 的 AI sidecar，
也不读取 sidecar 缓存；不提供未证实的历史 `market_cap`，因此
`net_buyback_yield` 保留 `missing_external_data`。八个 `compute_*` 的可选
`as_of` 只过滤输入事实和边界；不传时保持原有计算路径。
公式、resolver 选值和 fail-closed 规则不变。

## 返回与验证

envelope、行数上限和分页见 [tool-contract.md](tool-contract.md)。
指标值为十进制字符串，八项单位均为无量纲 `ratio`，`definition` 给出实际公式。
每项的 `source_filings`、`source_fact_ids` 和确定性 `fact_id` 可供 Python
核验数字及披露时间。失败保留原 `FailureCode` 于 `reason.code`，不填假值。
`invalid_context` 对应 `error`，`not_applicable` 对应同名状态，
其余代码（含 `zero_denominator`、`missing_external_data`）对应 `unavailable`。
截至日早于第一份已披露申报时返回 `period_unavailable`，明说
“截至该日没有已披露的财报”。

## 当前时间及外部输入审计

按 `rg -n 'today|now\(|datetime\.now|date\.today|market_cap|price|Company\('
src/thesis_tracker/financial src/thesis_tracker/metrics` 检查：

| 位置 | 原用途 | 工具路径的处理 |
|---|---|---|
| `financial/sec_source.py:293-323` | 旧 loader 从 Stage 1 库取最新成功 filing，并在线构造 `Company`；传给 selector 的 `today` 实际是选中 filing 的 `filed_at` | 旧路径保留以维持基线；工具改走只读快照，采集在独立入口联网 |
| `financial/ai_fallback.py:392` | AI sidecar 缓存使用真实当前时间判断 TTL | 工具传 `ai_fallback=None`、空语义上下文，不进入缓存或 LLM |
| `financial/pit_collect.py:40-44` | 采集时在线请求 SEC；selector 的 `today` 为显式 `until` | 仅采集步骤使用，不在工具调用路径 |
| `financial/pit_store.py:101` | 入库时间戳 `retrieved_at` | 仅写入时读 UTC 当前时间；时点选择只用 `filed_at` |
| `metrics/financial.py:403-414,842-879` | 市值是外部输入，不能从当前价格推断；`net_buyback_yield` 需它 | 工具传 `None`；当 `as_of` 非空时即使调用方传市值也禁用，返回 `missing_external_data` |

`financial/` 和 `metrics/` 其余 `today/now` 搜索命中不是墙钟读取；
`source_stale` 在这两条路径没有基于今天的判断，工具不引入新的新鲜度推断。
