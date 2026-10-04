# Decision Mode（决策建议层）边界

本文把已拍板的 Decision Mode 边界落成文字。**每节都标注 已定 / 待定**：
标 已定 的是已经决定的内容；标 待定 的是尚未决定、需要单独决定的问题。
本文不改动任何代码，也不改动其他文档。

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

## 8. 存储位置（待定）

本轮**不决定**存储位置。以下只是 repo 现有持久化方式的只读盘点，不含选型。

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

由此产生的开放问题（待定）：

- 建议卡落在上面哪一类持久化里？
- 卡片是否提交进 git（像 `eval/*.json`）还是不提交（像 `data/`）？
- 到期评估的对照结果存在哪里？
- 卡片与"工具结果快照"如何关联（内嵌，还是外键引用）？

---

## 9. 其它待定项（本轮不决定）

- validator 的实现位置，以及它在工具与出卡流程中的调用点。
- "价位不自洽"的完整判定集合（本轮只给了一个例子：做多时止损高于买点）。
- 置信度三档由谁定档、依据什么。
- "到期"取周期档的哪一端，"实际走势"用什么口径。
- 数据缺口披露的最小字段集合。
- 短/中/长三个周期是一张卡还是三张卡。
