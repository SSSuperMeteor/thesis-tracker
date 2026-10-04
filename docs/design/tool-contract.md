# 工具接口约定（Tool Contract）

本文把已拍板的 agent 工具接口约定落成文字。**每节都标注 已定 / 待定**：
标 已定 的是已经决定、可以直接实现的内容；标 待定 的是尚未决定的问题。
本文只写设计约定与现状证据，不写改造方案，也不改动任何代码或测试。

术语（英文专业术语第一次出现时给中文解释）：

- **envelope（信封）**：所有 agent 可调用工具共用的统一返回外壳。
- **as_of（数据截至日期）**：工具只返回不晚于该日期的数据。
- **fact_id（事实编号）**：每条事实一个编号，用途见第 4 节。
- **reason code（原因代码）**：机器可判定的失败原因标签。
- **validator（校验器）**：Python 侧的机械校验，不通过就拒绝。
- **comps（可比公司分析）**：用同业公司做相对估值。

---

## 1. Envelope 字段（已定）

所有 agent 可调用的工具返回同一种 envelope（信封）。

| 字段 | 含义 | 说明 |
|---|---|---|
| `status` | 本次调用结果状态 | 取值见第 2 节 |
| `data` | 结构化 JSON 结果 | 各工具自己的 schema |
| `source` | 数据来源 | 财报用 accession（SEC 申报编号）；价格用接口和时间窗口 |
| `as_of` | 数据截至日期 | 工具只返回不晚于该日期的数据 |
| `fact_id` | 每条事实一个编号 | 用途见第 4 节 |
| `reason` | 原因 | `status` 非 `ok` 时必填：一句大白话原因 + 一个原因代码 |

---

## 2. status 取值（已定）

`ok` / `unavailable` / `not_applicable` / `error`。

---

## 3. reason 与原因代码（已定）

- `status` 非 `ok` 时 `reason` 必填，内容 = 一句大白话原因 + 一个原因代码。
- 财务类工具沿用现有 failure taxonomy（失败分类法）。**本文不复制其正文**，
  权威定义见 `.agents/skills/failure-taxonomy/SKILL.md`。
- 价格类新增三个原因代码：`no_data`、`rate_limited`（被限流）、`provider_error`
  （数据提供方报错）。

---

## 4. 设计原则：让 AI 读得最明确（已定）

- 字段名用直白英文，不用缩写。
- 每个数字带单位和期间或日期。
- 算不出来时用大白话写清原因。
- 默认只返回最新值和结论；完整历史序列走单独调用；单次返回量要有上限。
- `fact_id` 的用途是 Python 校验 AI 引用的数字，**不是给 AI 读的**。

---

## 5. 硬约束（已定）

- 工具内部不调用 LLM（大语言模型）、不猜值。
- 查不到就返回 `unavailable` 加 `reason`，不返回近似值。
- 所有工具接收 `as_of` 参数；基本面按 `filed_at <= as_of` 过滤。
- 工具只读；需要联网的先落库并记录 `retrieved_at`（实际取数时间），
  agent 读到的是库里的值。

---

## 6. 第一批工具（已定）

| 工具 | 说明 |
|---|---|
| `get_price_history` | 价格历史 |
| `get_indicators` | 技术指标 |
| `get_fundamental_metrics` | 包装 Stage 3 |
| `search_filings` | 包装 Stage 2，带引用 |

可比公司（comps）与新闻工具本轮不设计（已定，明确在范围外）。

---

## 7. 现有接口与 envelope 的差距（已定：仅现状证据）

本节只记录现状，**不写改造方案**。每条结论附 `文件:行号`。

### 7.1 财务层 `src/thesis_tracker/financial/`（已定：仅现状证据）

**`load_stage3_inputs`（`financial/sec_source.py:293`）**

返回 `Stage3Inputs`（定义在 `sec_source.py:53`），字段为 `selected_boundary` /
`boundaries` / `facts` / `semantic_contexts`；`selected_boundary` 是
`Stage1Boundary`（`sec_source.py:43`）。

- 没有 `status`：成功即返回对象；失败抛 `SourceValidationError`（`:34`），
  构造时带 `FailureCode` 和 message（`:37`），但不序列化成 envelope。
- 没有 `as_of` 入参：签名（`:293`）只有 `ticker` / `db_path` / `history_years` /
  `max_filings` / `include_semantic_contexts`。"截至哪天"来自数据库：`load_stage1_boundary`
  （`:90`）按 `ingestion_status = 'success'` 取行，用 `rows[-1]` 的 `filed_at`，
  再交给 `_DateBoundCompany(..., until=selected.filed_at)`（`:60`、`:308`）。
  调用方无法指定 `as_of`。
- 不是只读：`load_stage1_boundary` 用 `sqlite3.connect(db_path)`（`:93`，未带只读参数）。
- 会联网：`:308` 构造 `Company(requested_ticker)` 并 `get_filings` 取在线 filing，
  没有"先落库、记 `retrieved_at`"的中间层。

**事实与失败类型（`financial/models.py`）**

- `FailureCode`（`:12`）有 12 个成员，含 metric 级 `zero_denominator`（`:29`）
  与 `missing_external_data`（`:30`）。
- `FinancialFact`（`:120`）已带 `fact_id` property（`:186`，值为
  `accession|concept|context_id`）、`accession`、`unit`、`period_start`/`period_end`、
  `filed_at`、`origin`、`resolver_path`，并有 `to_dict()`（`:189`，`value` 由
  `Decimal` 转 `str`）。
- `FailureDiagnostic`（`:223`）带 `final_failure` / `recovery_history` /
  `rejection_reason`，并有 `to_dict()`（`:237`）；`FactResult = ResolvedFact | FailedFact`
  （`:264`）。
- 与 envelope 的差距：这些是"事实对象"而不是信封，没有 `status`、没有 `as_of`。
  `Unit`（`:33`）只有 `USD` / `shares` / `USD/shares` / `pure` 四种，价格与百分比类
  工具需要的单位不在其中；失败对象没有 `fact_id`（失败本身没有事实）。

**指标入口（`metrics/financial.py`）**

- `compute_gross_margin_trend`（`:124`）返回 `GrossMarginTrend`（`:70`，`to_dict` `:76`）；
  另外 7 个 `compute_*`（`:628`、`:674`、`:743`、`:818`、`:894`、`:956`、`:1006`）
  返回 `MetricResult`（`:441`，`to_dict` `:447`）。
- 两者都是 `observations` 与 `failures` 两个并列 tuple，没有单一 `status`；
  同一个 ticker 可以同时有成功 observation 和 failure。
- 没有"只返回最新值"的模式：`max_periods: int = 8`（`:129`、`:633` 等），
  也没有返回量上限参数。
- `Stage3Observation`（`:415`）与 `GrossMarginObservation`（`:50`）都**没有 unit 字段**；
  单位只存在于 `provenance.source_facts[].unit`（`MetricProvenance` 见 `:33`）。
- 无 `as_of` 入参；`value` 是 `Decimal`，`to_dict` 输出 `str`（`:426`、`:58`）。
- 纯计算、不联网、默认不调 LLM（`ai_fallback` 默认 `None`，`:131`）。

### 7.2 检索层 `src/thesis_tracker/retrieve/`（已定：仅现状证据）

**`BM25Retriever.search`（`retrieve/bm25.py:53`，方法 `:62`）**

返回 `list[BM25Result]`（`:24`，`to_dict` `:33`；字段 `chunk_id` / `ticker` /
`form_type` / `accession` / `section` / `title` / `bm25_score` / `text`）。

- 返回裸 list，没有 envelope：空 list 无法区分"没检索到"与"数据缺失"。
- 没有 `as_of`；返回里没有 `filed_at`，只有 `accession`。
- `text` 是 chunk 全文，没有返回量上限；`top_k` 默认 5（`:66`），不设上限。
- 只读：`sqlite3.connect(database_uri, uri=True)`，URI 带 `mode=ro`（`:158`）。

**`VectorRetriever.search`（`retrieve/vector.py:88`，方法 `:225`）**

返回 `list[VectorResult]`（`:42`，`to_dict` `:51`，字段含 `distance`）。

- 同样无 envelope、无 `as_of`；`distance` 是距离（越小越近），不是相似度分值，
  返回结构里没有说明方向。
- 读库是只读的（`mode=ro`，`:373`），但 `search` 会调用 embedding（向量化）服务联网。
- 向量库默认路径 `store/vectors`（`:21`）。

**`HybridRetriever.search`（`retrieve/hybrid.py:219`，方法 `:248`）**

返回 `list[HybridResult]`（`:73`，`to_dict` `:100`）。

- 字段名大量缩写：`rrf_score`、`bm25_rank`、`vector_rank`，`search` 参数里的
  `top_k`、`candidate_k`（`:248`–`:256`），与第 4 节"不用缩写"不一致。
- 无 envelope、无 `as_of`。

**`verify_evidence`（`retrieve/citation.py:38`）**

返回 `CitationVerificationResult`（`:24`）：`valid` / `reason` / `chunk_id` /
`evidence_text`；`reason` 取值来自 `CitationReason`（`:16`）= `exact_match` /
`evidence_not_found` / `chunk_not_found` / `empty_evidence`。

- 只有 `chunk_id`，没有 `accession` 等 `source` 字段；没有 `as_of`。
- `valid: bool` 把"引文不匹配"和"chunk 不存在/数据缺失"压成同一个 `False`，
  没有区分 envelope 的 `unavailable` 与 `error`。
- 只读：`mode=ro`（`:67`）。

**`verify_sec_claim`（`retrieve/claim_support.py:138`）**

返回 `SecClaimVerificationResult`（`:121`）：`grounding_valid` / `grounding_reason` /
`support_status` / `reason` / `confidence` / `chunk_id` / `claim` / `evidence_text`。

- **会调用 LLM**：`:172` 在未传 provider 时构造 `DeepSeekClaimSupportProvider`（`:71`）。
  与第 5 节"工具内部不调用 LLM"直接冲突（若把它包装成工具）。
- `accession` 只是 prompt（提示词）入参（`:138` 签名内），不在返回值里；无 `as_of`。
- `confidence` 是模型给出的档位。

**`answer_sec_question`（`qa/sec_qa.py:144`，Stage 2 的公开入口，`search_filings` 包装的对象）**

返回 `dict[str, Any]`，骨架见 `_empty_result`（`:652`）：`question` / `ticker` /
`form_type` / `status` / `retrieved_chunks` / `candidate_claims` / `verified_claims` /
`rejected_claims` / `final_answer` / `final_output_mode` / `evidence_only` / `error`。

- 已有 `status`，但取值是 Stage 2 自己的状态机：`pending`（`:657`）、`success`（`:242`）、
  `error`（`:198`、`:209`）、`no_evidence`（`:187`）、`evidence_only`（`:261`）、
  `insufficient_evidence`（`:273`），不是第 2 节的四态。
- **会调用 LLM**（claim 生成 + claim support）。
- 没有 `as_of`；没有 `fact_id`，claim 只有 `evidence_chunk_id`。
- `retrieved_chunks` 是检索结果全量，没有返回量上限。

---

## 8. 待定（开放问题，本轮不决定）

- 各工具 `data` 的具体字段 schema。
- `source` 在价格/指标工具里的具体结构（"接口 + 时间窗口"如何编码）。
- 价格/指标类 `fact_id` 的生成方式。财报侧已有 `FinancialFact.fact_id`
  （`financial/models.py:186`）；但指标 observation（`metrics/financial.py:415`）
  只有 `provenance.source_fact_ids`，自身没有 `fact_id`。
- "单次返回量要有上限"的具体数值与截断策略。
- `as_of` 的精度（只到日期，还是含时间戳，例如盘中价格）。
- envelope 在代码里的表示形式（`TypedDict` / dataclass / JSON Schema）。
- 财务类原因代码的映射口径：`FailureCode`（`financial/models.py:12`）有 12 个成员，
  比 `.agents/skills/failure-taxonomy/SKILL.md` 正文列出的多了 metric 级
  `zero_denominator` 与 `missing_external_data`（`:29`、`:30`），两者如何对应
  `reason` 待定。
- Stage 2 已有的 `evidence_only` / `insufficient_evidence`（`qa/sec_qa.py:261`、`:273`）
  如何映射到 envelope 的 `status`。`citation-layers` skill 第 4 节要求这两者保持
  可区分、不得并进"已回答"；差异见第 7.2 节。
- 价格数据的来源与落库位置（与 `docs/design/decision-mode.md` 的存储待定是同一问题）。
