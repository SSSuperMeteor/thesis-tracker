# 应该下沉成代码的约束

以下规则原本写在 SKILL.md 里，但它们是**机械可判定**的——靠 prompt 约束等于
"希望模型记得"，靠代码约束是"违反就报错"。按项目第一原则（Python 管验证），
这些应该迁进 validator / dataclass / 类型系统，然后从 skill 文本里删掉。

每迁走一条，skill 就短一点，信噪比就高一点。

## 高优先级（现在就该有）

| 约束 | 实现方式 |
|---|---|
| 单位不兼容不得比较/相减/合并 | `Unit` 为枚举或带单位类型；跨单位运算在 `__sub__` 抛 `IncompatibleUnitError` |
| missing / not_applicable / zero 三态不得混淆 | `FactResult` 为 sum type（`Resolved` \| `Failed(code)`），不允许返回裸 `None` 或 `0.0` |
| derived fact 必须自我标识 | `FinancialFact.origin: Literal["reported", "derived"]`，derived 必填 `derivation_method` + `source_fact_ids` |
| provenance 必填 | dataclass 必填字段：`source`、`accession`、`concept`、`unit`、`context_id`、`filed_at`、`resolver_path`。缺字段构造即失败。period 字段见下一行 |
| instant fact 没有 period_start | 不要为了填满 dataclass 造一个假的 start date。两种写法二选一：<br>① 分成 `InstantContext(period_end)` / `DurationContext(period_start, period_end)` 两个类型<br>② `period_start: Optional[date]`，validator 强制 `fact_kind == instant → period_start is None`，`fact_kind == duration → period_start is not None` |
| instant vs duration 用错 | concept registry 标注 `fact_kind: instant \| duration`；resolver 在 kind 不匹配时直接 `invalid_context` |
| 失败必须可序列化 | `FailureDiagnostic` dataclass + `to_dict()`；stress runner 只接受这个类型 |

## Duration：先摸清 calendar pattern，别先定全局容差

52/53 周财年的公司（NVDA 1 月底、AVGO 11 月初）period_end 不落月末，单季
可能 91 天也可能 98 天。严格 `90 ± 2` 会大面积误杀，这点确定。

但**不要**直接去找"覆盖全部合法样本的最小区间"。那条路会被个别 104 天的
合法季度逼着一路放宽，最后 validator 宽到没有判别力。

正确顺序：

**第一步 — 统计，不写规则。** 对 15 家各拉 8 个季度，导出：

```
ticker, fiscal_calendar, period_start, period_end,
duration_days, FP, filing_reporting_boundary
```

看是否收敛成几个稳定的 calendar pattern（日历年制 / 52-53 周制 / 特殊财年
月份），而不是先求一个全局 tolerance。

**第二步 — 按 pattern 分别处理。** 每种 pattern 的合法 duration 是各自的，
不是共用一个区间。

**第三步 — duration 只做 sanity check。**

这是关键：季度身份应该由 **filing reporting boundary 和相邻 fiscal
boundary** 证明，不是由"天数像不像一个季度"推断。天数范围只用来发现明显
异常（比如一个标成单季的 fact 实际 270 天），不作为认定季度的核心证据。

用 duration 推断身份等于把启发式当证明，和 `financial-fact-invariants`
第 1 条直接冲突。

## 与 latest-filing discovery 的关系

两条独立问题，互不决定：

```
duration / calendar 校准  → 解决 duration_unavailable、period_unavailable 的误杀
latest-filing discovery   → 解决 source_stale
```

校准之后 `period_unavailable` 数量下降，**不能**推出 freshness 层不用做。
反过来也一样。

唯一值得先做的一次性验证（几分钟）：拉一次 companyconcept，确认 XOM 那个
case 是真的 stale，还是 resolver 查错了 concept/duration——

```
https://data.sec.gov/api/xbrl/companyconcept/CIK0000034088/us-gaap/Revenues.json
```

看有没有 `end = 2026-06-30`。这不是决定要不要做 discovery 层，而是决定
discovery 层能不能拿到一个经过验证的真实 regression case。

## 保留在 skill 里的

以下无法编码，只能靠文本约束，属于判断而非规则：

- 概念语义等价的判断（`NetIncomeLoss` vs `ProfitLoss`）
- 什么时候算"issuer-specific 确实合理"
- 一组 failure 是否属于同一 root cause
- 某个 metric 对某个行业是否 `not_applicable`
- 何时应该停下来报告而不是继续修

## Decision Mode validator（已由代码强制，2026-10-04）

`decision.core.validate_card` 实现第 10.2 节 D01–D10，全部违规项一次返回；
`append_card` 和 `render_card` 均在违规时拒绝。对应
`tests/test_decision_core.py` 的合法卡、对抗变异、配对矩阵、价位、只读/断网、存档测试。

| 约束 | 实现方式 |
|---|---|
| 事实数字与对应 `fact_id` 不一致 | D01；`test_adversarial_mutations_are_rejected` |
| 价格不可用或过期 | D03；`test_unavailable_price_rejected`、`test_stale_or_future_tool_data_rejected` |
| 价位不自洽 | D05；`test_hold_and_avoid_price_rules`、对抗变异 |
| 缺少数据缺口披露 | D06；对抗变异 |
| 缺少机器可检查的失效条件 | D07；对抗变异、`test_every_invalidation_price_must_be_positive` |
