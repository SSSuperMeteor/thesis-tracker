# thesis-tracker

SEC/XBRL 财报研究 Agent。主要工作在 Stage 3（financial facts + metrics），
Stage 1 (SEC ingestion) 与 Stage 2 (hybrid retrieval + QA + citation) 已实现。

Stage 1/2 的既有 correctness 行为默认保持兼容。除非本轮任务明确要求修改对应
Stage，否则不要顺手重构；但任务明确要求时，正常修改，不要因为"已实现"而拒绝。

## Commands

```bash
uv run pytest            # 全量测试
uv run ruff check .      # lint
uv run ingest NVDA AMD --latest   # 真实数据验证（Stage 1 SEC 采集，写入本地 runtime data）
```

改动提交前，与本轮相关的命令必须全部通过。只跑相关的，不为形式全跑。

## Iron rules

1. Python 管事实、状态、数学与验证；LLM 只做语义与无法规则化的判断。
   LLM 的输出永远要经过 Python 校验，不得绕过 validator。
2. Fail-closed。无法证明某个 fact 属于目标 period / concept / filing 时，
   返回带 failure code 的结构化失败，不得回退到"取最新""取最大""取看起来对的"。
3. 禁止 `except Exception: return None`。每个失败都要带 failure code。
4. 禁止 ticker 特判（`if ticker == "XOM"`），除非会计行为本身确实是
   issuer-specific 且写明原因。修 failure class，不修单个公司。
5. 不得为提高覆盖率或让测试变绿而放松 validator、改测试预期、
   把 missing 当 0、或隐藏 ambiguity。
6. 不得修改 `parser_version` / `normalizer_version`，除非任务明确要求。
7. 本轮任务结束即停止。不要顺手实现下一个 Stage 或未来 TODO。
8. 一轮只解决一个明确 failure / capability。

## Executor behaviour

自主 inspect → edit → test → debug → 迭代。不要每改一行就来确认。
测试失败且由本轮改动引起时，继续定位修复，不要在第一次失败就停。

只有两种情况应该停下来报告：
- 需要改变已确定的 architecture
- 正确行为无法从 repo 和当前 spec 判断

最终必须用中文总结：改了哪些文件、核心实现、测试结果、真实数据结果、剩余问题。
有数字就给精确数字，不要写 "tests passed successfully"。

## Skill routing

本 repo 的规则优先于任何已安装 skill。冲突时以本文件为准。

| 任务 | 加载 |
|---|---|
| 任何触碰 financial fact / period / resolver / metric / XBRL 的改动 | `financial-fact-invariants` |
| 需要给 failure 定类、选 recovery 路径 | `failure-taxonomy` |
| 多公司 / 多期间批量跑 | `stress-test-runbook` |
| 根因未知的 bug | `systematic-debugging` |
| 行为明确、可稳定复现的实现或 bug fix | `test-driven-development` |
| citation / claim support / QA evaluation | `citation-layers` |
| 声称完成前 | `verification-before-completion` |

典型组合：Stage 3 bug → systematic-debugging + financial-fact-invariants +
failure-taxonomy。15 家 stress test → financial-fact-invariants +
failure-taxonomy + stress-test-runbook。

diagnostic-only 的 stress pass **不触发** TDD。先跑完诊断、选定要修的 root
cause，再进入 test-driven-development。顺序反了会变成给还没定性的现象写测试。

不要无关加载。citation-layers 不参与纯财务任务。

## Secrets

`DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` / `EDGAR_IDENTITY` 只从环境变量读取。
不得硬编码、不得提交、不得打印完整值到日志。
