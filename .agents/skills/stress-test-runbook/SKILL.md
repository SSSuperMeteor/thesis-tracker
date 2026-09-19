---
name: stress-test-runbook
description: Procedure for running and analysing multi-company or multi-period stress tests of thesis-tracker Stage 3. Use whenever a task runs the pipeline across several tickers, compares behaviour across issuers, collects failure distributions, or verifies that a general fix holds on a broad real-data set. Use it before touching any code in such a run — the diagnostic pass comes first. Do not use for a single-ticker smoke test or one unit test.
---

# Stress Test Runbook

The goal is discovering failure classes, not raising the success rate. A run
surfacing 30 correctly classified failures beats a 100% run achieved by
relaxed validation.

Failure codes: see `failure-taxonomy`. Validity rules: see
`financial-fact-invariants`.

## Phase 1 — diagnostic pass (no fixes)

Run the current system across the requested ticker set. Do **not** patch
failures as they appear; one failure class usually spans several issuers and
patching early hides that.

Only exception: repairing the stress runner itself if it cannot complete.
That never extends to changing correctness behaviour.

Record the baseline before any fix: ticker set, periods, metric/concept set,
total observations, success count, counts by `final_failure`, and
representative failed observations, plus any available code/version
identifiers.

`recovery_history` is reported separately. Never count a code that appears in
recovery_history as a failure — a recovery that ran and succeeded is not a
failed observation, and double-counting it inflates every class it touches.

## Phase 2 — classify

Label every failure with a canonical code. If a case fits none, keep it
`unresolved`, describe why, and judge whether it is genuinely a new class —
one unusual issuer is not a new category.

Then answer: which failures are likely one shared root cause? Which are
legitimate unavailable data (`true_missing`, `not_applicable`) rather than
defects? Never assume a persistent failure is a bug.

## Phase 3 — pick one class

Choose a single failure class or demonstrated shared root cause. Do not
rewrite unrelated parts of the financial layer in the same cycle.

```
bad:  "make XOM pass"
good: "recover facts when the aggregation source lags a newer official filing"

bad:  "make AVGO net income work"
good: "resolve or safely classify multiple surviving net-income candidates"
```

## Phase 4 — fix, then re-run

Representative failing case → unit tests → known-good regression tickers
(NVDA/AMD must not be sacrificed for another issuer) → the broad set.

Report an explicit before/after delta per failure code, and explain why each
count moved. A lower failure count means nothing if a constraint was loosened
to achieve it — state that no invariant changed, or say which did and why.

## Never

Weaken validation, turn missing into zero, pick ambiguous candidates,
substitute an older period, suppress failure records, drop difficult
companies mid-run, or special-case tickers.

## Completion

A diagnostic task is complete when the population ran, failures were
preserved and classified, representative cases were identified, and systemic
patterns were summarised. It does **not** require fixing everything.

If the task also includes one fix: add general implementation, regression
test, real-data validation, and no demonstrated regression.

## Required final report (Chinese)

```
1. 本轮运行范围（ticker 集合 / periods / metrics）
2. 总 observation 数
3. 成功数
4. failure code 分布（精确计数）
5. 受影响 ticker / concept
6. 代表性失败案例
7. 共同 root cause 判断
8. 若改动代码：改了什么、为什么是通用修复
9. before / after 对比
10. 测试与真实数据结果
11. 剩余未解决问题
```

Exact counts, always. Never "most tests passed".
