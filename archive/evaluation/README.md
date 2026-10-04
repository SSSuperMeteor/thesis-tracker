# archive/evaluation

归档,当前数据上不可运行。历史版本见 `archive/pre-cleanup` 分支。

这两个脚本在 2026-09-19 runtime data reset 之前用于 Stage 2 检索/QA 基准。
reset 之后语料重建,它们依赖的输入产物(旧 baseline JSON、旧结果 JSON)已删除,
且 Chroma 里已没有它们需要的 `sec_chunks_qwen3_vl_embedding_1024_chunkv2` collection
(当前只有 `sec_chunks_qwen3_vl_embedding_1024`)。

| 文件 | 行数 | 归档原因 |
|---|---:|---|
| `retrieval_stage2_parentcollapse.py` | 400 | 零 importer、零测试。依赖已删除的 `eval/retrieval_stage2_results.json`(baseline)与 `eval/retrieval_stage2_chunkv2_results.json`(raw-v2),并依赖缺席的 chunkv2 collection。实测 `python -m thesis_tracker.evaluation.retrieval_stage2_parentcollapse` 立即抛 `ValueError: question manifest ticker mismatch`,未发生网络调用。 |
| `sec_qa_chunkv2_difficult5.py` | 288 | 零 importer、零测试、无参数解析。`run_difficult_five()` 第一条语句读取已删除的 `eval/sec_qa_stage2_acceptance_30_results.json`。实测 `python -m thesis_tracker.evaluation.sec_qa_chunkv2_difficult5` 立即抛 `FileNotFoundError`,未发生 API 调用。 |

核实方式(归档时当前 HEAD):

- 全仓 `grep -rn` 于 `src/`、`tests/`、`scripts/` 未命中这两个模块名或文件名;唯一的文本
  命中来自 `docs/code-inventory.md`(盘点报告本身)与 reset/plan 历史文档,后者引用的是
  它们早已删除的**输出产物**(`*_results.json`、`*_report.md`),不是脚本。
- 仓库中 `importlib` / `__import__` / `import_module` / 字符串形式模块路径均为 0 处,
  不存在动态加载入口。

这不是删除:需要恢复时用 `git mv` 移回 `src/thesis_tracker/evaluation/`,或从
`archive/pre-cleanup` 分支取回原文件。归档后无任何代码引用它们,`pytest` / `ruff`
均不受影响。
