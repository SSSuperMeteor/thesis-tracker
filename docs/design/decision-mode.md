# Decision Mode（决策建议层）边界

本文把已拍板的 Decision Mode 边界落成文字。**每节都标注 已定 / 待定**：
标 已定 的是已经决定的内容；标 待定 的是尚未决定、需要单独决定的问题。
2026-10-04 的确定性内核决定见第 10 节；第 8 节存储位置已确定。

术语（英文专业术语第一次出现时给中文解释）：

- **Decision Mode（决策建议层）**：基本面层之外的独立模块，产出交易建议。
- **建议卡**：Decision Mode 的输出单位，由事实表、判断区、数据缺口三部分组成。
- **validator（校验器）**：Python 侧机械校验，不通过就不出卡。
- **as_of（数据截至日期）**：卡片所用数据的截止日期。
- **fact_id（事实编号）**：事实的编号，定义见 `docs/design/tool-contract.md`。
- **fingerprint（指纹）**：用来标识模型实际返回身份的哈希或标识。
- **prompt 版本（提示词版本）**：生成请求所用提示词的版本号。
- **buy-and-hold（买入持有）**：买入后不再操作的对照策略。
- **SPY**：跟踪标普 500 的 ETF，用作市场基准。

---

## 1. 定位与边界（已定）

- 基本面层保持不给交易建议。
- Decision Mode 是单独模块，只消费工具返回的结果。

---

## 2. 建议卡结构（已定）

建议卡由三部分组成。

### 2.1 事实表（已定）

- 全部来自工具。
- 每条带 `fact_id`、来源、`as_of`。

### 2.2 判断区（已定）

明确标成"AI 判断"，包含：

- 倾向：看多 / 中性 / 看空。
- 动作：买入 / 分批 / 持有 / 减仓 / 回避。
- 买点区间。
- 止损位。
- 目标位或预期持有期。
- 理由：每条引用 `fact_id`。
- 失效条件：什么情况下这个判断是错的。
- 置信度：只给低 / 中 / 高三档，并标"未校准"，不给百分比。

### 2.3 数据缺口（已定）

- 哪些指标没算出来必须写明。

---

## 3. 周期三档（已定）

| 档位 | 区间 |
|---|---|
| 短期 | ≤ 1 个月 |
| 中期 | 1–6 个月 |
| 长期 | > 6 个月 |

---

## 4. 放开（已定）

倾向、动作、买点、止损、目标位允许 AI 给明确结论，不要求措辞保守。

---

## 5. Python validator 必须拦住（已定）

校验失败不出卡。必须拦住：

1. 标为事实的数字与对应 `fact_id` 的工具返回值不一致。
2. 价格数据不可用。
3. 价位不自洽（例如做多时止损高于买点）。
4. 缺少数据缺口披露。
5. 缺少失效条件。

---

## 6. 落库字段与到期评估（已定）

每张卡落库，字段：

- `card_id`
- 创建时间
- `as_of`
- 创建时价格
- 周期
- 倾向和各价位
- 失效条件
- 工具结果快照
- 模型请求名、实际返回名、fingerprint、prompt 版本

到期后对照实际走势、SPY 和 buy-and-hold。

---

## 7. 免责声明（已定）

卡片底部固定一行：本卡为 AI 研究输出，不构成持牌投资建议。

---

## 8. 存储位置（已定，2026-10-04）

建议卡写入 `data/decisions/cards.db`。这是本地运行期持久化数据，独立于可重建的
`data/cache/`；`.gitignore` 忽略 `data/decisions/`。SQLite 建表即用，不增加 ORM 或迁移工具。
每张卡内嵌完整快照 JSON，并存相同字节的 SHA-256、validator 版本与全部校验结果。
表只公开追加和按 `card_id` 读取接口；数据库触发器同时拒绝 `UPDATE` 与 `DELETE`。
依据：现有项目使用本地 SQLite；卡及证据必须长期留存、可复核，不能放在可重建缓存。

以下是选型前的持久化盘点，保留作为依据。

| 现有持久化 | 证据（文件:行号） | 形态 |
|---|---|---|
| Stage 1/2 语料库 | `data/corpus.db`；默认路径见 `ingest/cli.py:41`、`ingest/pipeline.py:32`、`financial/sec_source.py:296`、`retrieve/bm25.py:14`、`retrieve/vector.py:20`、`retrieve/citation.py:13` | SQLite；当前 `documents` 15 行、`chunks` 2310 行；建表语句在 `ingest/sec_adapter.py:2511` 与 `:2555` |
| 向量索引 | `store/vectors`，默认路径 `retrieve/vector.py:21` | Chroma 持久化目录（`store/vectors/chroma.sqlite3`） |
| AI 概念提议缓存 | `financial/ai_cache.py:77` 的 `SqliteConceptProposalCache`，建表 `:86` | SQLite 单表 `concept_proposal_cache`；路径由调用方传入，仓库内未配置默认路径 |
| 原始申报文本 | `data/raw/*.txt`（当前 15 个文件） | 落盘纯文本 |
| 评测/审计产物 | `eval/*.json`、`eval/*.md`（例如 `eval/stage3_stress_8metric.json`） | 提交进 git 的文件 |
| 本地运行日志与临时产物 | `data/cache/`（例如 `data/cache/ingest_batch.log`、`data/cache/stage3_stress_8metric.json`） | 未跟踪的本地文件 |

补充证据：

- `.gitignore` 把 `data/corpus.db`、`data/corpus.db-*`、`data/raw/`、`data/cache/`、
  `store/vectors/`、`store/cache/`、`store/artifacts/`、`eval/results/` 列为不提交；
  而 `eval/` 下的 JSON 与 markdown 是提交的。
- 全仓没有 ORM（对象关系映射）、没有数据库迁移工具、没有通用 key-value/元数据表；
  现有 SQLite 都是建表即用（`CREATE TABLE IF NOT EXISTS`，`ingest/sec_adapter.py:2511`、
  `:2555`，`financial/ai_cache.py:86`）。

到期评估的存储和计算仍待定；本轮不实现。

---

## 9. 其它待定项

- 置信度三档由谁定档、依据什么。
- "到期"取周期档的哪一端，"实际走势"用什么口径。
- 短/中/长三个周期是一张卡还是三张卡。

## 10. 确定性内核（已定，2026-10-04）

### 10.1 卡和快照（已定）

一次分析的 `EvidenceSnapshot` 记录目标 `ticker`、`as_of`，以及**每次**实际工具调用的
工具名、原始参数和原样 envelope。`fact_index` 由 Python 从调用记录重建；不信任卡片或
快照中缓存的索引。索引只收有非空数值的价格收盘价、技术指标和财务指标，记录
`fact_id`、名称、原值、单位、日期/财期、标的、来源。价格日线 `fact_id` 对应原始收盘价；
其它 OHLCV 字段须另建明确身份后才能引用。目标之外（包括 SPY）的编号不可写进卡。
一个编号在快照内出现不同值或来源时拒绝。依据：现有三个工具 envelope 的字段和
`tests/test_decision_core.py` 的真实库快照。

草稿字段：`ticker`、`as_of`、`horizon`、`bias`、`action`、`confidence`、
`entry_range`（两个数或 `null`）、`stop_loss`、`target_price`、`fact_ids`、
`reasons`（每条 `text` + `fact_ids`）、`invalidations`（每条 `kind`、`price`、`text`）。
Python 写入 `facts`、`gaps`、`confidence_calibration=未校准`、固定免责声明；
模型不得撰写这些字段。事实表逐项保留原数值、单位、期间与来源。
存档另含 `card_id`、UTC 创建时间、创建时价格（由快照价格行复现）、
快照完整 JSON、SHA-256、validator 版本、校验结果，及可空的模型请求名、
实际返回名、fingerprint、prompt 版本。代码入口为 `decision.core`；
`decision-snapshot TICKER --as-of YYYY-MM-DD` 仅用三个本地只读工具默认参数打印事实与缺口。

### 10.2 校验规则（已定）

每次校验返回所有违规项，各含 `rule`、`location`、中文说明；任一违规则不渲染、不存档。

| 编号 | 机械规则 |
|---|---|
| D01 | `ticker`/`as_of` 与快照一致；每个引用在本快照、本标的的无冲突索引内；事实表与 Python 重建结果逐字段相等；每次工具调用的 `as_of` 一致。 |
| D02 | 理由或失效条件文字中的事实值只用 `{fact:<fact_id>}`；占位符必须有有效引用；与本快照任一事实显示值相同的裸数字拒绝。 |
| D03 | 必须有可用价格与收盘价；价格 `data_end_date` 距 `as_of` 不超过 5 个日历日（含第 5 日）；所有工具的 `data_end_date` 均不得晚于 `as_of`。 |
| D04 | 倾向/动作符合下表。 |
| D05 | 价位遵守下文规则；判断价位均为正数。 |
| D06 | `gaps` 与快照里所有数值为 `null`、状态为 `unavailable`/`not_applicable` 的指标完全一致，另保留不可用工具的原因。 |
| D07 | 至少一条 `close_below` 或 `close_above` 的机器可检查条件，阈值为正数；所有填写的条件价位为正数。 |
| D08 | 周期仅短/中/长三档；置信度仅低/中/高，且必须标“未校准”。 |
| D09 | 至少一条理由，每条至少一个有效 `fact_id`。 |
| D10 | 卡底免责声明与第 7 节完全相同，由 Python 写入。 |

| 倾向 | 允许动作 | 保守拒绝 |
|---|---|---|
| 看多 | 买入、分批、持有 | 减仓、回避 |
| 中性 | 分批、持有、回避 | 买入、减仓 |
| 看空 | 减仓、回避 | 买入、分批、持有 |

买入、分批：`0 < 止损 < 买点下沿 ≤ 买点上沿 < 目标`。持有：买点区间为空，
`0 < 止损 < as_of 可见最新原始收盘价 < 目标`。减仓、回避：买点、止损、目标
均为空；价位建议对这两个动作不适用，仍须给出机器可检查的收盘价失效条件。
这些配对避免把“看空却买入”等歧义交给模型解释。依据：用户给定的风险边界、
价格工具的原始收盘价语义和 `tests/test_decision_core.py` 配对/价位测试。

裸数字例外只包括 ISO 日期 `YYYY-MM-DD`、财期 `Q1`–`Q4`/`YYYY-Q1`–`YYYY-Q4`，
以及“整数 个季度/月/交易日/工作日/年度/年”这类时间计数。价位字段中的判断数字
自由填写；自由文本里与快照事实值相同的数字即使写成不同小数精度也拒绝。
其它裸数字也拒绝；失效价位放在 `price` 字段，不在文字里重复。
依据：事实数字由 Python 渲染可复核；日期、周期和时间计数是语法标签或持续时间。
