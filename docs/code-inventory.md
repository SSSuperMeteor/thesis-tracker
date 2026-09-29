# 代码盘点（src/thesis_tracker/）

基线：`main` @ `ec2dab9`（tag `stage3-metrics-v1`），盘点开始时工作区干净。本文件只读盘点，
未修改、删除或移动任何代码。每条结论标注 **[已确认]**（附搜索方式和结果）或 **[推测]**。

## 0. 方法与局限

| 手段 | 做了什么 |
|---|---|
| AST import 图 | 自写脚本（scratchpad，未提交）解析 `src/` 全部 58 个 `.py`，含函数内 lazy import 和相对 import；`tests/`、`scripts/` 同样解析。可达性沿 import 边传递，并把父包 `__init__` 视为被 import。 |
| 入口 | 只有 4 类：pyproject `ingest = thesis_tracker.ingest.cli:main`；`scripts/stage3_stress_8metric.py`；`python -m` 模块入口（有 `if __name__ == "__main__"` 的非 evaluation 模块）；evaluation/ 自带的 `__main__`。 |
| vulture | `uvx vulture src --min-confidence 60`（临时运行，未安装进项目，未改 pyproject）。它只看 `src/`，会把仅被 tests/scripts 使用的名字报为未使用，所以只作交叉验证。 |
| 干净 checkout | `git clone` 到 scratchpad（无 `data/`、`store/vectors/`、`.env`），用项目已有 venv 的 python + `PYTHONPATH=<clone>/src` 跑 pytest，并 `env -u` 去掉 `DEEPSEEK_API_KEY`/`DASHSCOPE_API_KEY`/`EDGAR_IDENTITY`。 |
| 只读 SQLite | `data/corpus.db`、`store/vectors/chroma.sqlite3` 以 `mode=ro` 打开查询。 |

局限：
- 静态分析证明“不可达”，不证明“运行时会执行”。`import` 可达但默认路径不调用的模块单列为 A3。
- **动态引用**：`importlib` / `__import__` / `import_module` 在 `src/`、`scripts/`、`tests/` 中 **0 处**
  （grep `importlib\|__import__\|import_module`）。`getattr` 出现在 `retrieve/hybrid.py:355,357`、
  `ingest/cli.py:82,90`、`ingest/filing_selection.py:427,449`、`qa/sec_qa.py:292,646`、
  `retrieve/vector.py:588`、`financial/ai_concepts.py:164-170`、`financial/semantic_candidates.py:103,107,108,195`、
  `embedding/dashscope.py:86`，对象均是 SDK 响应或数据对象（duck typing），**不是按字符串取模块**。
  字符串形式的模块路径（`"thesis_tracker.` 开头）0 处。因此没有因动态引用而不能判断的模块。
- 我在检查 `evaluation/sec_qa_chunkv2_difficult5.py` 的 `--help` 时，该模块没有参数解析，
  `python -m` 直接执行了 `run_difficult_five()`，在第一条语句读取已删除的
  `eval/sec_qa_stage2_acceptance_30_results.json` 时抛 `FileNotFoundError`，此时尚未发生任何 API 调用，
  也没有写任何文件（`git status` 干净）。

## 1. 总览表

分类：**A1** 产品入口（pyproject/stress runner）可达并执行；**A2** 仅 `python -m` 模块入口可达；
**A3** 从 stress runner 静态 import 可达，但默认路径不执行；**B** 只被 tests 使用；**C** evaluation/ 内模块；
**D** 空占位符；**E** 无法判断。列“入站”= src 内直接 import 它的模块数；“测试”= 直接 import 它的测试文件数。

| 模块 | 行数 | 类 | 入站 | 测试 | 说明 |
|---|---:|:-:|---:|---:|---|
| `thesis_tracker` | 1 | A1 | 0 | 1 | 包根；1 行 docstring |
| `config` | 25 | A1 | 5 | 0 | env 配置；被 5 个模块 import |
| `embedding` | 24 | A1 | 1 | 0 | EmbeddingProvider Protocol |
| `embedding.dashscope` | 142 | A1 | 11 | 1 | ingest.pipeline 建索引使用 |
| `financial` | 17 | A1 | 0 | 0 | 包 __init__，re-export models |
| `financial.models` | 264 | A1 | 7 | 7 | Stage 3 数据模型 |
| `financial.registry` | 82 | A1 | 4 | 1 | 概念注册表 |
| `financial.resolver` | 473 | A1 | 1 | 3 | Stage 3 解析器 |
| `financial.sec_source` | 392 | A1 | 0 | 2 | Stage 3 取数；stress runner 调用 load_stage3_inputs |
| `ingest` | 1 | A1 | 0 | 0 | 包 __init__ |
| `ingest.cli` | 107 | A1 | 1 | 1 | pyproject 入口 `ingest` |
| `ingest.filing_selection` | 509 | A1 | 4 | 3 | Stage 1 选 filing；Stage 3 也复用 |
| `ingest.pipeline` | 189 | A1 | 1 | 2 | ingest CLI 的协调器 |
| `ingest.sec_adapter` | 3404 | A1 | 3 | 4 | Stage 1 采集/切块/写库（见函数级清单） |
| `metrics` | 1 | A1 | 0 | 0 | 包 __init__ |
| `metrics.financial` | 1101 | A1 | 0 | 3 | 8 个 metric；stress runner 调用 |
| `retrieve` | 1 | A1 | 0 | 0 | 包 __init__ |
| `retrieve.vector` | 651 | A1 | 10 | 3 | Chroma 检索；ingest.pipeline 也用 |
| `financial.ai_cache` | 189 | A3 | 1 | 2 | AI sidecar 缓存 |
| `financial.ai_concepts` | 219 | A3 | 3 | 4 | DeepSeek 概念提议 provider |
| `financial.ai_fallback` | 515 | A3 | 1 | 1 | AI fallback；被 metrics.financial 静态 import，默认不启用 |
| `financial.ai_validation` | 301 | A3 | 1 | 1 | AI 提议的 Python 校验 |
| `financial.semantic_candidates` | 287 | A3 | 4 | 3 | 语义候选；sec_source 中受 include_semantic_contexts=False 门控 |
| `ingest.rechunk_v2` | 227 | A2 | 0 | 1 | 一次性迁移脚本（`python -m`），无入站引用 |
| `qa` | 1 | A2 | 0 | 0 | 包 __init__ |
| `qa.sec_qa` | 804 | A2 | 4 | 1 | Stage 2 QA，`python -m` 入口 |
| `retrieve.bm25` | 249 | A2 | 9 | 3 | BM25；无 ingest 引用 |
| `retrieve.citation` | 154 | A2 | 2 | 1 | Layer A 引证校验，`python -m` |
| `retrieve.claim_support` | 302 | A2 | 5 | 1 | Layer B 支持度，`python -m` |
| `retrieve.hybrid` | 466 | A2 | 8 | 2 | RRF 混合检索，`python -m` |
| `evaluation` | 1 | C | 0 | 0 | 包 __init__ |
| `evaluation.retrieval_metrics` | 150 | C | 3 | 0 | 纯函数指标；仅被 evaluation 内部 import |
| `evaluation.retrieval_stage2` | 853 | C | 2 | 2 | Stage 2 基准 |
| `evaluation.retrieval_stage2_chunkv2` | 1232 | C | 4 | 1 | chunk v2 基准 |
| `evaluation.retrieval_stage2_parentcollapse` | 400 | C | 0 | 0 | 零入站引用、零测试 |
| `evaluation.sec_qa_034_diagnostic` | 367 | C | 0 | 1 | 单 case 非确定性诊断 |
| `evaluation.sec_qa_acceptance` | 688 | C | 3 | 1 | 30 case 验收 |
| `evaluation.sec_qa_chunkv2_difficult5` | 288 | C | 0 | 0 | 零入站引用、零测试；输入文件已删 |
| `evaluation.sec_qa_evidence_only` | 439 | C | 0 | 1 | evidence-only 评测 |
| `agent` | 1 | D | 0 | 0 | 1 行占位符 |
| `agent.critic` | 1 | D | 0 | 0 | 1 行占位符 |
| `agent.memory` | 1 | D | 0 | 0 | 1 行占位符 |
| `agent.orchestrator` | 1 | D | 0 | 0 | 1 行占位符 |
| `agent.planner` | 1 | D | 0 | 0 | 1 行占位符 |
| `agent.router` | 1 | D | 0 | 0 | 1 行占位符 |
| `agent.state` | 1 | D | 0 | 0 | 1 行占位符 |
| `agent.tools` | 1 | D | 0 | 0 | 1 行占位符 |
| `decision` | 1 | D | 0 | 0 | 1 行占位符 |
| `decision.mode` | 1 | D | 0 | 0 | 1 行占位符 |
| `eval` | 1 | D | 0 | 0 | 1 行占位符 |
| `eval.runner` | 1 | D | 0 | 0 | 1 行占位符 |
| `ingest.news` | 1 | D | 0 | 0 | 1 行占位符 |
| `ingest.peers` | 1 | D | 0 | 0 | 1 行占位符 |
| `ingest.prices` | 1 | D | 0 | 0 | 1 行占位符 |
| `model` | 1 | D | 0 | 0 | 1 行占位符 |
| `model.canonical` | 1 | D | 0 | 0 | 1 行占位符 |
| `playbooks` | 1 | D | 0 | 0 | 1 行占位符 |
| `playbooks._primitives` | 1 | D | 0 | 0 | 1 行占位符 |

| 分类 | 模块数 | 行数 |
|---|---:|---:|
| A1 | 18 | 7384 |
| A2 | 7 | 2203 |
| A3 | 5 | 1511 |
| B | 0 | 0 |
| C | 9 | 4418 |
| D | 19 | 19 |
| E | 0 | 0 |
| **合计** | **58** | **15535** |

注：A1 里 `ingest.sec_adapter` 一个文件占 3404 行（46%）。18 个 A1 中有 7 个是 1–25 行的包 `__init__`/`config`。

## 2. 各类说明

### A1 — 产品入口可达（18 个模块）

**[已确认]** 依据：AST 图从 `thesis_tracker.ingest.cli` 与 `scripts/stage3_stress_8metric.py`（import
`financial`、`financial.resolver`、`financial.sec_source`、`metrics.financial`）出发的闭包。
- ingest 入口闭包：`ingest.cli → ingest.pipeline → {ingest.sec_adapter, ingest.filing_selection, embedding.dashscope, retrieve.vector}`，
  再带出 `config`、`embedding`。所以 **Stage 1 采集 CLI 同时依赖 Stage 2 的 `retrieve.vector` 与 embedding**（建向量索引）。
- stress 闭包：`metrics.financial → financial.{models, resolver, registry, sec_source}`，`sec_source → ingest.filing_selection`。
  所以 **Stage 3 反向依赖 Stage 1 的 `filing_selection` 与 SQLite 边界**（`load_stage1_boundary` 读 `data/corpus.db`）。

复用判断 **[推测]**：`financial/*`、`metrics/financial`、`ingest/filing_selection`、`ingest/pipeline`、`retrieve/vector` 是后续“输入公司自动跑全流程”最直接的骨架。

### A2 — 只有 `python -m` 入口（7 个模块）

`qa.sec_qa`、`retrieve.{bm25, citation, claim_support, hybrid}`、`ingest.rechunk_v2`（含 `qa/__init__`）。
**[已确认]** 依据：这些模块有 `__main__`，`--help` 可正常输出（exit 0）；pyproject 没登记，README/docs
没有 `python -m` 用法（grep `python -m` 于 README.md docs eval AGENTS.md CLAUDE.md：0 处）。
- `retrieve.bm25` 不在 ingest CLI 闭包内（`ingest.pipeline` 不 import 它）：**[已确认]** 图中入站只有 `retrieve.hybrid`、`qa.sec_qa` 与 evaluation。
- `ingest.rechunk_v2`：无入站引用，仅被 `tests/test_sec_chunking_v2.py` 引用；一次性的 chunk v2 迁移脚本。**[推测]** 迁移已完成（`data/corpus.db` 里 chunk id 全是 `::chunk_NNN`，2310 行）。

Stage 2 QA 不是“产品入口”还是“产品的一部分”是产品决策，见第 10 节。

### A3 — 静态可达、默认不执行（5 个模块，1511 行）

见第 8 节。`financial.{ai_cache, ai_concepts, ai_fallback, ai_validation, semantic_candidates}`。

### B — 只被 tests 使用（0 个模块）

**[已确认]** 没有模块满足。所有 tests 引用的模块都同时被入口或 evaluation 引用。
函数级例外见第 4 节（`sec_adapter.save`）和第 9 节（`semantic_candidates.normalize_presentation_qname` 仅测试用）。

### C — evaluation/（9 个文件，4418 行）

见第 5 节。**[已确认]** 除 `evaluation.*` 自身外，没有别的 src 模块 import evaluation。

### D — 空占位符（19 个文件，各 1 行）

`agent/{__init__, critic, memory, orchestrator, planner, router, state, tools/__init__}`、
`decision/{__init__, mode}`、`eval/{__init__, runner}`、`model/{__init__, canonical}`、
`playbooks/{__init__, _primitives}`、`ingest/{news, peers, prices}`。
**[已确认]** 依据：`wc -l` 均为 1 行 docstring；AST 图中入站为 0；`grep -rn` 于 src/tests/scripts 无任何 import。
- `eval/` 包（占位）与 `evaluation/` 包（4418 行）并存，命名容易混淆 **[已确认]**。
- 仓库根另有 `playbooks/earnings_quality.yaml`（1 行注释）和空的 `playbooks/tests/`，与 `src/thesis_tracker/playbooks/` 重复表意。`eval/{agent_cases,gold_cases,historical_set}.yaml` 也各 1 行占位。

### E — 无法判断（0 个模块）

没有模块因动态引用而无法判断。**项级**的不确定项见第 10 节。

## 3. 依赖声明检查

**[已确认]** pyproject 声明的 8 个依赖在 `src/` 里都有 import（`chromadb` 3 个文件，`dashscope` 1，`edgar` 3，
`openai` 4，`pydantic` 1，`dotenv` 1，`rank_bm25` 1，`tqdm` 5）；没有发现声明了但从未 import 的依赖。
注：`financial/ai_concepts.py:14` 与 `retrieve/claim_support.py:11`、`qa/sec_qa.py:12` 模块级 `from openai import OpenAI`，
所以 stress runner 导入 `metrics.financial` 时会经 `ai_fallback → ai_concepts` 导入 `openai` 包（不联网）。

## 4. ingest/sec_adapter.py 函数级清单（3404 行）

方法：AST 提取顶层 `def`/`class`（共 32 个，合计 2857 行），在 src/tests/scripts 中查
`from …sec_adapter import name`、`sec_adapter.name` 属性访问和同名字符串常量；内部调用图再从外部引用的“根”传递。
“CLI 可达”= 从 `ingest.pipeline` / stress runner 直接引用的名字出发经内部调用可达。

| 名称 | 类型 | 行 | 行数 | 外部引用 | 被文件内谁引用 | 结论 |
|---|---|---|---:|---|---|---|
| `ChunkingConfig` | class | 55-62 | 8 | t:test_sec_chunking_v2.py | apply_chunking_v2, split_text_spans | CLI 可达 |
| `_progress_write` | func | 94-102 | 9 | — | _build, fetch_filing | CLI 可达 |
| `_progress_items` | func | 105-122 | 18 | — | _build | CLI 可达 |
| `_repair_wait_status` | func | 125-187 | 63 | — | _build | CLI 可达 |
| `normalize` | func | 217-239 | 23 | src:retrieve/citation.py | _build, verify_quote | CLI 可达 |
| `sha256` | func | 242-245 | 4 | t:test_sec_chunking_v2.py, t:test_stage1_family_persistence.py, t:test_stage1_ingest_pipeline.py | _build, apply_chunking_v2 | CLI 可达 |
| `Chunk` | class | 252-291 | 40 | src:ingest/rechunk_v2.py, t:test_sec_chunking_v2.py, t:test_stage1_family_persistence.py, t:test_stage1_ingest_pipeline.py | CanonicalDoc, _build, apply_chunking_v2 | CLI 可达 |
| `CanonicalDoc` | class | 294-340 | 47 | src:ingest/pipeline.py, t:test_raw_filing_path.py, t:test_sec_chunking_v2.py, t:test_stage1_family_persistence.py, t:test_stage1_ingest_pipeline.py | _build, _write_document, assert_sane, fetch_filing, save, save_family | CLI 可达 |
| `RepairTarget` | class | 343-359 | 17 | — | _build, _llm_repair | CLI 可达 |
| `split_text_spans` | func | 370-413 | 44 | t:test_sec_chunking_v2.py | apply_chunking_v2 | CLI 可达 |
| `apply_chunking_v2` | func | 416-477 | 62 | src:ingest/rechunk_v2.py, t:test_sec_chunking_v2.py | _build | CLI 可达 |
| `_choose_natural_boundary` | func | 480-497 | 18 | — | split_text_spans | CLI 可达 |
| `_unique_occurrence` | func | 504-543 | 40 | — | _apply_llm_note_span, _apply_llm_section_start, _locate_note_exact, _locate_section_start | CLI 可达 |
| `_locate_section_start` | func | 546-726 | 181 | — | _build | CLI 可达 |
| `_locate_note_exact` | func | 729-754 | 26 | — | _build | CLI 可达 |
| `_locate_note_heading` | func | 757-800 | 44 | — | _build | CLI 可达 |
| `_normalize_item_key` | func | 821-828 | 8 | — | _build, _item_part_lookup, _resolve_section_label | CLI 可达 |
| `_item_part_lookup` | func | 831-893 | 63 | t:test_stage1_ingest_pipeline.py | _build | CLI 可达 |
| `_resolve_section_label` | func | 896-922 | 27 | t:test_stage1_ingest_pipeline.py | _build | CLI 可达 |
| `_llm_repair` | func | 928-1053 | 126 | — | _build | CLI 可达 |
| `_apply_llm_section_start` | func | 1055-1087 | 33 | — | _build | CLI 可达 |
| `_apply_llm_note_span` | func | 1090-1148 | 59 | — | _build | CLI 可达 |
| `fetch_filing` | func | 1155-1204 | 50 | — | — | **仅被 `__main__` 死代码引用** |
| `_sanitize_filename_component` | func | 1211-1288 | 78 | t:test_raw_filing_path.py | _raw_filing_path | CLI 可达 |
| `_raw_filing_path` | func | 1291-1356 | 66 | t:test_raw_filing_path.py | _build | CLI 可达 |
| `_build` | func | 1359-2388 | 1030 | src:ingest/pipeline.py | fetch_filing | CLI 可达 |
| `assert_sane` | func | 2395-2567 | 173 | src:ingest/pipeline.py | — | CLI 可达 |
| `_ensure_schema` | func | 2671-2761 | 91 | t:test_stage1_family_persistence.py | save, save_family | CLI 可达 |
| `_write_document` | func | 2768-3034 | 267 | — | save, save_family | CLI 可达 |
| `save` | func | 3037-3052 | 16 | t:test_sec_chunking_v2.py | — | **仅 tests** |
| `save_family` | func | 3055-3096 | 42 | src:ingest/pipeline.py, t:test_stage1_family_persistence.py | — | CLI 可达 |
| `verify_quote` | func | 3103-3186 | 84 | — | — | **无引用** |

汇总（**[已确认]**，计算结果）：

| 组成 | 行数 |
|---|---:|
| 顶层 def/class（32 个） | 2857 |
| `if __name__ == "__main__"` 块（3193–3404） | 212，其中 3193–3208 有效（转调 `ingest.cli.main`），**3209–3404 为 `raise SystemExit(...)` 之后的不可达代码，196 行** |
| 导入 / 常量 / SQL schema / 注释 | 335 |
| **合计** | **3404** |

没有任何外部引用、且不可达的部分：
- `verify_quote`（3103–3186，84 行）：**[已确认]** `grep -rnE "verify_quote" src tests scripts docs eval *.md` 只命中定义一处；vulture 也报 unused。功能已被 `retrieve/citation.py::verify_evidence` 取代 **[推测]**（同为“quote 是否存在于 source”，且 citation 只 import 了 `sec_adapter.normalize`）。
- `fetch_filing`（1155–1204，50 行）：**[已确认]** 全仓 grep 只命中定义和 `__main__` 死代码块内的一次调用（3254）。
- `__main__` 死代码尾部（3209–3404，196 行）：**[已确认]** vulture 报 `unreachable code after 'raise' (100% confidence)`（3209）。
- 合计 330 行（占 9.7%）。另有 `save`（3037–3052，16 行）**仅被 `tests/test_sec_chunking_v2.py:171` 调用**，src 内无调用方（docstring 称“retained for existing Stage 1 callers”）。

其他观察（**[已确认]** 行数来自 AST）：`_build` 单个函数 1030 行（占文件 30%），`_write_document` 267，
`_locate_section_start` 181，`assert_sane` 173，`_llm_repair` 126。LLM 边界修复路径
（`_llm_repair` 126 + `_apply_llm_section_start` 33 + `_apply_llm_note_span` 59 + `RepairTarget` 17）
由 `_build` 调用，静态上属于 ingest CLI 路径，是否在真实运行中触发见第 10 节。

## 5. evaluation/ 逐文件

“可运行”只判断，未跑真实 SEC/embedding/LLM。所有 8 个模块 `import` 成功（`python -c "import …"`）；
6 个有 argparse 的模块 `--help` 均 exit 0（`retrieval_metrics` 无 CLI，`sec_qa_chunkv2_difficult5` 无参数解析）。

共同前置条件：
- 都读 `eval/retrieval_stage2_questions.json`（tracked，sha256 与 reset 文档记录一致 **[已确认]**）。
- 除 `retrieval_metrics` 外都需要 DashScope embedding（网络 + `DASHSCOPE_API_KEY`）与 `data/corpus.db`、`store/vectors`。
- `sec_qa_*` 另需 DeepSeek（`qa.sec_qa`、`retrieve.claim_support` 用 OpenAI 兼容客户端）。
- **本机现状 [已确认]**：`data/corpus.db` = 15 documents / 2310 chunks；Chroma 只有 `sec_chunks_qwen3_vl_embedding_1024`
  一个 collection，**没有** `sec_chunks_qwen3_vl_embedding_1024_chunkv2`；`retrieval_stage2_questions.json` 70 题中
  **20 题**（LITE `0001628280-26-030777`、SNDK `0001628280-26-029401` 各 10 题）期望的 accession 不在当前库里。
- 各模块读取的基线/旧结果文件（`eval/retrieval_stage2_results.json`、`eval/sec_qa_stage2_acceptance_30_results.json` 等）
  在提交 `175b2c7`（reset）里已删除，`eval/` 下现在没有任何 `*_results.json`（除 Stage 3 的）。

| 文件 | 行数 | 被 tests 引用 | 依赖运行数据 | 现在能否运行 |
|---|---:|---|---|---|
| `retrieval_metrics.py` | 150 | 间接（经 `retrieval_stage2`）；无直接测试 | 无 | 能（纯函数） |
| `retrieval_stage2.py` | 853 | 是：`test_retrieval_stage2_evaluation.py`(3)、`test_retrieval_stage2_chunkv2.py`(8) 用合成语料 | corpus + v1 collection + DashScope | 测试能；真实运行会因 20/70 题校验失败（`load_and_validate_questions` 遇无效题即 `ValueError`）**[已确认]** 读源码；且需联网 |
| `retrieval_stage2_chunkv2.py` | 1232 | 是：`test_retrieval_stage2_chunkv2.py`(8) | chunkv2 collection（本机缺）、baseline 结果（已删）、DashScope | 测试能；真实运行不能 |
| `retrieval_stage2_parentcollapse.py` | 400 | **否**（0 个测试、0 个 importer） | chunkv2 collection（缺）、baseline（已删，`:92` 读取） | 不能 |
| `sec_qa_034_diagnostic.py` | 367 | 是：`test_sec_qa_034_diagnostic.py`(2) | chunkv2 collection（缺）、DashScope、DeepSeek | 测试能；真实运行不能 |
| `sec_qa_acceptance.py` | 688 | 是：`test_sec_qa_acceptance.py`(6) | chunkv2 collection（缺）、DashScope、DeepSeek | 测试能；真实运行不能 |
| `sec_qa_chunkv2_difficult5.py` | 288 | **否**（0 个测试、0 个 importer） | `OLD_RESULTS_PATH`（已删） | **不能**：`python -m` 立即 `FileNotFoundError`（实测，无 API 调用） |
| `sec_qa_evidence_only.py` | 439 | 是：`test_sec_qa_evidence_only.py`(6) | `--old-results` 默认指向已删文件（`:37,:67`）、chunkv2 collection（缺） | 测试能；真实运行不能 |

**[推测]**：Stage 2 基准针对的是 9/19 之前的旧语料；reset 之后语料已重建，这些评测要恢复需要先重建 chunkv2 collection 并重新生成基线。

## 6. tests/ 对运行数据的依赖

**[已确认]**（grep + 干净 checkout 实跑）：
- grep `data/`、`store/`、`corpus.db`：测试里所有 `corpus.db` 均为 `tmp_path / "corpus.db"`；`tests/conftest.py`
  明确用 `eval/retrieval_stage2_questions.json` 现场造合成语料。**没有任何测试读取 `data/` 或 `store/` 下的运行数据。**
- 干净 checkout（无 `data/`、`store/vectors`、无三个环境变量）：**346 passed，0 failed，0 skipped**（3.15 s）。
- **3 个测试依赖 CWD**：用相对路径 `Path("eval/retrieval_stage2_questions.json")`，从仓库根以外的目录跑 pytest 会失败
  （在 scratchpad 目录用 `--rootdir=<clone>` 跑：3 failed, 343 passed）：
  - `tests/test_sec_qa_acceptance.py::test_frozen_selection_has_thirty_balanced_cases`
  - `tests/test_sec_qa_evidence_only.py::test_case_filter_selects_only_requested_cases_in_order`
  - `tests/test_sec_qa_evidence_only.py::test_case_filter_rejects_unknown_ids`
- 依赖**被跟踪**的 `eval/retrieval_stage2_questions.json`：`conftest.py` 加 `test_retrieval_stage2_chunkv2`(8)、
  `test_retrieval_stage2_evaluation`(3)、`test_sec_qa_acceptance`(6)、`test_sec_qa_evidence_only`(6) 四个测试文件（共 23 个测试）。这个文件在 clean checkout 里存在，不是运行数据。
- 网络：**[推测]** 测试用 `Fake*` 替身，未见真实网络调用点，但我没有在断网环境下验证。

## 7. docs/ 与 eval/ 中看起来过时的文件

| 文件 | 判断 | 依据 |
|---|---|---|
| `eval/stage3_final_report.md` | **过时 [已确认]** | 写的是 345 passed、8-metric 561/952=58.9%、ar_growth 79、diluted 57；当前重跑为 346 passed、580/952=60.92%、90、65（`eval/stage3_stress_8metric.json`）。又写该 JSON “not committed”，现已提交。标题“7-metric migration”。已被 `stage3_lookback_window_fix_audit.md` 与 `docs/stage3-known-limitations.md` 取代。 |
| `eval/stage3_failure_root_cause_audit.json` | **引用的产物已变 [已确认]** | `audit_scope.source_artifact = eval/stage3_stress_8metric.json` 且 `stress_rerun=false`；该文件在 `ec2dab9` 被重跑结果覆盖，audit 里 11 条 `resolver_boundary_window_truncation` 在当前产物里已成功（ar_growth `period_unavailable` 22→11）。audit 本身作为“修复前诊断”仍成立，但对着当前 JSON 逐行核对会对不上。旧版在 git `624676c`。 |
| `eval/stage3_lookback_window_fix_audit.md` | **同上 [已确认]** | “before side is `eval/stage3_stress_8metric.json`”，该 before 版本现在只在 git `624676c`。数字与当前重跑一致（+11、+8、580/952）。 |
| `docs/stage3-gross-margin-audit.md` | **历史快照，字面上已过时 [已确认]** | 写 “`metrics/financial.py` and `model/canonical.py` were placeholders”，现 `metrics/financial.py` 为 1101 行（`model/canonical.py` 仍是占位）；写“other seven metrics remain backlog”，现已实现（`624676c`）。 |
| `docs/stage3-gross-margin-stress.md` | 一致，未发现矛盾 | gross_margin 93 observations、JPM 1 条 not_applicable 与当前 stress 一致；文中“uncommitted working tree”是当日状态。 |
| `docs/stage3-ai-concept-experiment.md` | 一致，未发现矛盾 | sidecar 只接 gross_margin `cost_of_revenue`：`metrics/financial.py:131` 仅 `compute_gross_margin_trend` 有 `ai_fallback` 参数。 |
| `docs/runtime-data-reset-2026-09-19.md` | 事件日志；“After reset”状态已被后续 ingest 取代 | 文中 documents=0、chunks=0、raw=0；当前 `corpus.db` 15/2310、`data/raw/` 有 15 个文件、Chroma 1 个 collection。5 个 preserved fixture 的 sha256 与当前文件**全部一致 [已确认]**。 |
| `docs/superpowers/{plans,specs}/*`（4 个，9/19） | **[推测]** 已执行的计划/设计 | Stage 1 reset 与 Stage 3 AI fallback 均已有对应代码；未逐条核对，`plan` 里引用的 `_ensure_schema` 仍存在。 |
| `docs/architecture.md`、`README.md` | 描述目标架构，与现状不符 | 描述 Playbooks / Agent Orchestration / Critic / Decision Mode 各层，这些在 `src/` 中均为 1 行占位（第 2 节 D）；README（21 行）没有任何命令/用法，未提 `ingest`。 |
| `TODO-enforce-in-code.md` | **部分过时 [已确认]** | “高优先级（现在就该有）”表里的单位类型、`ResolvedFact|FailedFact`、origin、provenance、`FailureDiagnostic.to_dict` 已在 Stage 3 落地：`financial/models.py` 有 `Unit`、`FactOrigin`、`FailedFact` 与 `to_dict`，`docs/stage3-gross-margin-audit.md` 自述“implements the high-priority mechanical constraints from TODO-enforce-in-code.md”。 |
| `eval/{agent_cases,gold_cases,historical_set}.yaml`、`playbooks/earnings_quality.yaml` | 空占位 | 各 1 行注释；代码中 0 引用（grep `agent_cases|gold_cases|historical_set|earnings_quality` src tests scripts）。 |
| `eval/sec_claim_support_manual_results.json` | 无代码引用 | grep 文件名 src tests scripts：0 处；reset 文档将其列为人工标注保留。 |

## 8. Stage 3 AI 旁路模块在产品路径里是否可达

**[已确认]** 结论：**import 可达，执行不可达**。

- import 链：stress runner → `metrics.financial`（`:10` import `financial.ai_fallback`，`:29` import `semantic_candidates`）
  → `ai_fallback` → `{ai_cache, ai_concepts, ai_validation, semantic_candidates}`。所以这 5 个模块都在 runner 的 import 闭包里（A3）。
- 执行门控：
  - `compute_gross_margin_trend(..., ai_fallback: AiFallback | None = None)`（`metrics/financial.py:131`），
    使用点 `:232-254` 需要 `ai_fallback is not None and ai_fallback.config.enabled`。stress runner 不传该参数。
  - `load_stage3_inputs(..., include_semantic_contexts=False)`（`sec_source.py:299`），`:383` 才构造 semantic context；runner 显式传 `False`。
  - 只有 `AiConceptFallback`（`ai_fallback.py:324`）与 `DeepSeekConceptProposalProvider`（`ai_concepts.py:124`）会真正调 LLM；
    搜索 `AiConceptFallback(`、`DeepSeekConceptProposalProvider(`、`include_semantic_contexts=True` 于 `src/` `scripts/`（排除 ai_*.py 自身）：**0 处构造点**。只有 tests 构造它们。
- 因此 **B 类语义**：AI 旁路目前只被 tests 真正执行。
- 代价：这 5 个模块共 1511 行，且带来对 `openai` 的模块级 import。**[推测]** 若要保持“Python 校验 + LLM 仅语义”，它们是可选 sidecar，可保留；
  它们不阻塞价格/技术指标工作。

## 9. 建议归档或删除的候选

只是建议，本轮不做。风险列指“做了会碰到什么”。

| 候选 | 行数 | 依据 | 风险 |
|---|---:|---|---|
| `sec_adapter.py` 的 `__main__` 死代码尾部（3209–3404） | 196 | **[已确认]** `raise SystemExit` 之后；vulture 100% | 极低；仅影响 `python src/.../sec_adapter.py` 老用法（已转调 CLI） |
| `sec_adapter.verify_quote` | 84 | **[已确认]** 全仓 grep 仅定义一处 | 低；`retrieve/citation.py::verify_evidence` 已有同类职责 **[推测]** |
| `sec_adapter.fetch_filing` | 50 | **[已确认]** 仅死代码块引用 | 低；删 `__main__` 尾部后即无引用，`_progress_write` 也随之少一个调用方 |
| `sec_adapter.save` | 16 | 仅 `tests/test_sec_chunking_v2.py:171` | **中：被 1 个测试引用**，需要改测试 |
| `evaluation/sec_qa_chunkv2_difficult5.py` | 288 | 零入站、零测试；输入文件已删，实测 `FileNotFoundError` | 低；一次性诊断脚本 |
| `evaluation/retrieval_stage2_parentcollapse.py` | 400 | 零入站、零测试；缺 chunkv2 collection 与 baseline | 低；但它 import 了 `retrieval_stage2_chunkv2` 的常量 |
| `evaluation/` 其余 6 个文件 | 3729 | 依赖 9/19 之前的旧语料、已删基线；真实运行不可用 | **中**：`sec_qa_034_diagnostic`、`sec_qa_acceptance`、`sec_qa_evidence_only`、`retrieval_stage2`、`_chunkv2` 各有测试（这 5 个文件对应 25 个测试）；`retrieval_metrics` 被三者 import |
| `ingest/rechunk_v2.py` | 227 | 无入站引用；迁移一次性脚本 | 中：`tests/test_sec_chunking_v2.py` 引用（该文件含 11 个测试） |
| 19 个 1 行占位文件 + 根目录 `playbooks/`、`eval/*.yaml` 占位 | 19 | **[已确认]** 零引用 | 极低；但下一阶段可能要用 `ingest/prices.py`、`agent/*` 这些名字，删不删是设计选择 |
| `eval/` 包（`eval/runner.py`） | 2 | 与 `evaluation/` 命名冲突 | 极低 |
| `eval/stage3_final_report.md` | 45 | 第 7 节：数字已被取代 | 低；`git log` 可恢复 |
| 死符号：`evaluation/retrieval_stage2_chunkv2.py` 的 `_parent_ranks`（`:152`）、`TOP_K_LEVELS`（`:68`）、`retrieve/hybrid.py` 的 `DEFAULT_CANDIDATE_K`（`:30`） | ~10 | **[已确认]** grep src tests scripts 仅命中定义 | 极低 |

不建议动：`financial/*`、`metrics/financial.py`、`ingest/{cli,pipeline,filing_selection}`、`retrieve/vector`、`embedding/*`、`config`（A1，Stage 3 冻结；且是下一阶段的复用基础）。
`retrieve/{bm25,citation,claim_support,hybrid}` 与 `qa/sec_qa`（A2，共约 1975 行）建议保留：Stage 2 引证与 QA 是最终产品的证据层，没有替代实现。

`semantic_candidates.normalize_presentation_qname` 仅被 `tests/test_financial_semantic_candidates.py:162` 使用（vulture 报未使用，属于测试专用函数），不建议动。

## 10. 无法判断的项（项级，不是模块级）

1. **Stage 2 的 `python -m` CLI 是否算产品路径**：`qa.sec_qa`、`retrieve.*` 没在 pyproject 登记、文档没有用法。是产品决策，代码里看不出。本文按“有入口即 A2”处理。
2. **`sec_adapter` 的 LLM 边界修复路径（约 235 行）在真实采集里是否触发**：静态上由 `_build` 调用；是否触发取决于 parser 定位失败的运行时情况。`docs/superpowers/plans/2026-09-19-stage1-filing-ingest-reset.md` 是否描述过它，未核对。
3. **`ingest/rechunk_v2` 的迁移是否已完成**：**[推测]** 已完成（当前库全是 v2 chunk id），但迁移是否还需对别的库重跑未知。
4. **测试是否可离线运行**：未在断网环境验证。
5. **`docs/superpowers/*` 是否全部执行完**：未逐条核对。
6. **gross_margin 26 个 failure 与 88 条未审计 failure** 不在本盘点范围（见 `docs/stage3-known-limitations.md`）。

## 11. 剩余问题

- 本文的引用图是静态的：`import` 可达不代表被调用；函数级分析只做了 `sec_adapter`，其余大文件（`metrics/financial.py` 1101、`qa/sec_qa.py` 804、`retrieve/vector.py` 651、`evaluation/retrieval_stage2_chunkv2.py` 1232）没有做函数级盘点。
- vulture 只在 `min-confidence 60` 下输出；未对 `scripts/`、`tests/` 反向统计。
