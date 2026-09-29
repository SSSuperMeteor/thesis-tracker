# Stage 3 (v1) 已知局限

冻结标签：`stage3-metrics-v1`。本文件记录 Stage 3 冻结时的真实数据表现与已知局限。
Stage 3 不再修复剩余 failure（用户决定），见「放弃的任务」。

## 数据来源与口径

- 所有 coverage 数字来自本轮用当前代码（基线 HEAD `36f2a56`）完整重跑的
  `eval/stage3_stress_8metric.json`，runner 为 `scripts/stage3_stress_8metric.py`。
- 范围：15 家公司（AAPL AMD AMZN AVGO GOOGL JPM LITE META MSFT NVDA ORCL SNDK TSLA VRT XOM），
  每个 metric 每家最多 8 个 period，共 119 个 requested period（`per_metric.*.requested`）。
- 本轮未调用任何 LLM：runner 不传 `ai_fallback`（默认 `None`），未启用 AI fallback。
- failure 分类依据 `eval/stage3_failure_root_cause_audit.json`。该 audit 是基于**修复 lookback
  窗口之前**的旧 stress 产物做的诊断回放（`audit_scope.stress_rerun = false`），
  没有针对本次重跑再审计。本文只用 audit 逐行 join 得到的结论；audit 没覆盖的一律标「未审计」。
- 「failure 数」是 `failure_taxonomy` 里的 failure 记录数，不等于 `unavailable`。
  JPM 的 `not_applicable` 每个 metric 只记 1 条 failure，但 8 个 period 都不可用。
  7 个 metric 合计 failure 记录 325 条，`unavailable` 为 346，差 21 = 3 个 metric × 7 个未单独记录的 JPM period。

## 每个 metric 的 coverage 与 failure 分布

| metric | requested | success | coverage | failure code 分布 |
|---|---:|---:|---:|---|
| accruals_ratio | 119 | 98 | 82.3529% | ambiguous 18, duration_unavailable 1, invalid_context 1, period_unavailable 1 |
| cash_conversion | 119 | 98 | 82.3529% | ambiguous 18, duration_unavailable 1, invalid_context 1, period_unavailable 1 |
| ar_growth_vs_rev_growth | 119 | 90 | 75.6303% | period_unavailable 11, registry_gap 8, ambiguous 1, invalid_context 1, not_applicable 1 |
| diluted_share_count_yoy | 119 | 65 | 54.6218% | registry_gap 24, duration_unavailable 23, period_unavailable 4, invalid_context 3 |
| interest_coverage | 119 | 87 | 73.1092% | period_unavailable 12, registry_gap 8, duration_unavailable 2, invalid_context 2, not_applicable 1 |
| net_debt_to_ebitda | 119 | 49 | 41.1765% | period_unavailable 22, invalid_context 19, registry_gap 11, ambiguous 10, not_applicable 1 |
| net_buyback_yield | 119 | 0 | 0.0000% | missing_external_data 119 |
| **7 个新 metric 合计** | **833** | **487** | **58.4634%** | `overall`：unavailable 346 |
| gross_margin_trend | 119 | 93 | 78.1513% | 未记录（见下） |
| **8 个 metric 合计** | **952** | **580** | **60.9244%** | |

gross_margin_trend：runner 只记录 observation 数（`gross_margin.new_observations = 93`），
没有记录 26 个 failure 的 code 分布，这 26 个 failure **未审计**。
JPM、ORCL、XOM 的 gross_margin observation 为 0（`per_ticker.*.gross_margin_observations`），原因未审计。

## Failure 分类（依据 root-cause audit）

分类的四类：真实数据缺失、真正的 ambiguous、已知但未修的 resolver 缺口、not_applicable。
另有 `missing_external_data`（缺外部输入，不属于四类）和「未审计」。

7 个 metric 共 206 条 failure（不含 net_buyback_yield 的 119 条）：

| 分类 | 条数 | 构成 |
|---|---:|---|
| 真实数据缺失 | 63 | 见下 |
| 真正的 ambiguous | 46 | 候选值互不相同 |
| 已知但未修的 resolver 缺口 | 6 | XOM `ReceivablesNetCurrent` |
| not_applicable | 3 | JPM（ar_growth、interest_coverage、net_debt 各 1） |
| 未审计 | 88 | 见下 |

### 真实数据缺失（63）

audit 判定该 fact 或历史在 filing 中确实不存在：

- ar_growth_vs_rev_growth：`period_unavailable` 5（SNDK，`issuer_history_or_required_fact_not_disclosed`）。
- interest_coverage：`period_unavailable` 12，其中 11 为 `standalone_interest_expense_not_disclosed`
  （AAPL 8、META 3），1 为 SNDK 历史不足；`registry_gap` 8（XOM，operating income 未披露）。
- net_debt_to_ebitda：`period_unavailable` 22，其中 21 为 `required_aggregate_absent_components_present`
  （精确 aggregate 未披露，组件存在；audit 归入 `required_exact_fact_or_period_not_disclosed`），
  1 为 SNDK 历史不足；`registry_gap` 8（XOM，operating income 未披露）。
  这 21 条正是被放弃的「net_debt_to_ebitda 聚合规则」任务所针对的情形。
- diluted_share_count_yoy：`registry_gap` 8（XOM，diluted share 未披露，audit 判定为真实缺失而非自定义 tag）。

### 真正的 ambiguous（46）

audit 逐行核对：46 行全部是 `true_value_conflict`，`different_decimal` 46、`same_decimal` 0，未加任何 tie-break。

- accruals_ratio 18、cash_conversion 18、net_debt_to_ebitda 10。
- 集中在 TSLA（accruals 8、cash 8）、XOM（accruals 8、cash 8）、AVGO（net_debt 7）、AMD（accruals 2、cash 2）等；
  按 ticker 的分布见 `per_ticker`。

### 已知但未修的 resolver 缺口（6）

- ar_growth_vs_rev_growth，XOM，`period_unavailable` 6：`unregistered_standard_receivables_candidate`，
  XOM 有标准 tag `ReceivablesNetCurrent` 的候选值，但未登记到 registry，resolver 无法使用。

### not_applicable（3）

- JPM 的 ar_growth、interest_coverage、net_debt 各 1 条，由 SIC 适用性规则产生。
  audit 未逐条审计，这里按 failure code 归类。

### 未审计（88）

audit 没覆盖，不做推断：

| metric | 条数 | 构成 |
|---|---:|---|
| accruals_ratio | 3 | duration_unavailable 1, invalid_context 1, period_unavailable 1 |
| cash_conversion | 3 | 同上 |
| ar_growth_vs_rev_growth | 10 | registry_gap 8（LITE）, ambiguous 1（SNDK）, invalid_context 1（ORCL） |
| diluted_share_count_yoy | 46 | duration_unavailable 23, registry_gap 16（MSFT 8、ORCL 8）, period_unavailable 4, invalid_context 3 |
| interest_coverage | 4 | duration_unavailable 2, invalid_context 2 |
| net_debt_to_ebitda | 22 | invalid_context 19, registry_gap 3（GOOGL 2、MSFT 1） |

audit 里另有一条仅记录、未修复的发现：ORCL 的 filing 级 fiscal-year 元数据冲突，
造成两个 boundary 共用 `2026-Q1` 标签。audit 判断当前已审计的 case 仍是 fail-closed，但未来 period/context
解析可能把两个不同财年当成同一个 target_period。

### net_buyback_yield 为 0%

`missing_external_data` 119/119。原因是计算需要市值，而 Stage 3 没有价格数据来源，
runner 传入 `market_cap=None`。这不是 fact 解析问题，需要价格数据才能计算。价格相关工作不在 Stage 3 范围内。

## audit 之后被修复的部分

lookback 窗口修复（`max_filings` 由 12 改为 `None`）之后，`period_unavailable` 中
`resolver_boundary_window_truncation` 的 11 条转为成功：

- ar_growth_vs_rev_growth：`period_unavailable` 22 → 11，success 79 → 90。
- diluted_share_count_yoy：`period_unavailable` 12 → 4，success 57 → 65。

其余 metric 的 success 数和每个 failure code 的计数与旧版完全相同。

## XOM 的状态

XOM 保留在 15 家 universe 中，用户决定不移除、不再审计。

- XOM 在 8 个 metric 上的表现：accruals 0/8（ambiguous 8）、cash_conversion 0/8（ambiguous 8）、
  ar_growth 2/8（period_unavailable 6）、diluted 0/8（registry_gap 8）、interest_coverage 0/8（registry_gap 8）、
  net_debt 0/8（registry_gap 8）、net_buyback_yield 0/8、gross_margin 0 个 observation。
- audit 对其 24 条 registry_gap 的结论：diluted share 8 条、operating income 16 条均为真实缺失，
  没有找到自定义 / extension 等价 tag（`custom_or_extension_equivalent_found = 0`）。
- 6 条 ar_growth 的 `ReceivablesNetCurrent` 缺口见上。以上均未修。

## 放弃的任务（用户决定不修）

1. XOM 移除 / 审计。
2. 非 XOM 的 diluted share `registry_gap` 审计（对应上面未审计的 MSFT 8、ORCL 8）。
3. `net_debt_to_ebitda` 聚合规则（对应上面 21 条 `required_aggregate_absent_components_present`）。

Stage 3 剩余的其他 failure 同样不再修复。

## 校验记录

- gross_margin baseline：`max_filings=12` 与默认日期窗口各 93 个 observation，完整 `to_dict()` 相等；
  稳定 JSON（`sort_keys=True, separators=(",", ":")`）的 SHA-256 均为
  `b7fed6ec6019fb739a6c04546ab0539cc3d2aad131ff932d180a0bd358a5a385`，与 audit 记录一致。
- 8 个 metric 合计 580/952 = 60.9244%，与 audit 声称的 60.92% 一致。
- 测试：346 passed。9/20 报告为 345，多出的一条是
  `tests/test_financial_sec_source.py::test_stage3_default_history_keeps_yoy_predecessor_beyond_twelve_filings`
  （lookback 修复时新增；文件修改时间晚于 9/20 报告）。该测试与 Stage 3 全部代码在同一个提交 `624676c` 里，
  git 历史无法进一步分开。

## 解冻条件

只有两种情况才重新改动 Stage 3：

1. 下游发现 Stage 3 输出了**错误的数**（不是缺数）。
2. 下游确实需要更高的 coverage，并且能给出理由。

否则 Stage 3 保持 v1 冻结，缺数（fail-closed）是预期行为，不是需要修复的缺陷。
