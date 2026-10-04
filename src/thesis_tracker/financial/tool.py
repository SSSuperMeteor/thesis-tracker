"""Read-only point-in-time envelope for the eight deterministic Stage 3 metrics."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from thesis_tracker.financial.models import FailureCode, FinancialFact
from thesis_tracker.financial.pit_store import DEFAULT_FACT_DB, load_snapshot
from thesis_tracker.financial.sec_source import SourceValidationError
from thesis_tracker.metrics.financial import (
    compute_accruals_ratio,
    compute_ar_growth_vs_rev_growth,
    compute_cash_conversion,
    compute_diluted_share_count_yoy,
    compute_gross_margin_trend,
    compute_interest_coverage,
    compute_net_buyback_yield,
    compute_net_debt_to_ebitda,
)
from thesis_tracker.prices import MAX_ROWS, _reason, _result

_METRICS = {
    "gross_margin_trend": (compute_gross_margin_trend, "ratio", "Quarterly gross profit / revenue"),
    "accruals_ratio": (compute_accruals_ratio, "ratio", "(Net income - OCF) / average assets"),
    "cash_conversion": (compute_cash_conversion, "ratio", "Operating cash flow / net income"),
    "ar_growth_vs_rev_growth": (compute_ar_growth_vs_rev_growth, "ratio", "AR YoY growth - revenue YoY growth"),
    "net_buyback_yield": (compute_net_buyback_yield, "ratio", "(Repurchases - SBC) / historical market cap"),
    "diluted_share_count_yoy": (compute_diluted_share_count_yoy, "ratio", "Diluted weighted-average shares YoY change"),
    "interest_coverage": (compute_interest_coverage, "ratio", "Operating income / interest expense"),
    "net_debt_to_ebitda": (compute_net_debt_to_ebitda, "ratio", "Net debt / EBITDA"),
}

_PLAIN_FAILURES = {
    FailureCode.NOT_APPLICABLE: "这类公司不适用该指标。",
    FailureCode.SOURCE_STALE: "截至该日，所需财报来源不符合目标披露时点。",
    FailureCode.REGISTRY_GAP: "财报里没有识别出计算所需的标准项目。",
    FailureCode.CUSTOM_CONCEPT_ONLY: "财报只提供公司自定义项目，无法确认它等同于所需标准项目。",
    FailureCode.INVALID_CONTEXT: "财报事实的期间或来源信息无效，计算已停止。",
    FailureCode.PERIOD_UNAVAILABLE: "截至该日，缺少计算这个财期所需的已披露财报。",
    FailureCode.DURATION_UNAVAILABLE: "财报缺少所需时长的数据，无法计算。",
    FailureCode.AMBIGUOUS: "有多个符合条件的财报事实，无法确定唯一数值。",
    FailureCode.UNRESOLVED: "现有财报事实不足以确定这个数值。",
    FailureCode.TRUE_MISSING: "已披露财报中没有计算所需的项目。",
    FailureCode.ZERO_DENOMINATOR: "分母为零，这个比值没有定义。",
    FailureCode.MISSING_EXTERNAL_DATA: "缺少对应财期的历史市值，无法计算；不会使用今天的市值。",
}


def _status(code: FailureCode) -> str:
    if code is FailureCode.NOT_APPLICABLE:
        return "not_applicable"
    if code is FailureCode.INVALID_CONTEXT:
        return "error"
    return "unavailable"


def _leaf_facts(source_facts: tuple[FinancialFact, ...], available: tuple[FinancialFact, ...]) -> tuple[FinancialFact, ...]:
    index = {fact.fact_id: fact for fact in available}
    leaves: list[FinancialFact] = []
    def walk(fact: FinancialFact) -> None:
        if fact.source_fact_ids:
            for fact_id in fact.source_fact_ids:
                if fact_id not in index:
                    raise SourceValidationError(FailureCode.INVALID_CONTEXT, "derived source fact absent from snapshot")
                walk(index[fact_id])
        else:
            leaves.append(fact)
    for item in source_facts:
        walk(item)
    return tuple(dict.fromkeys(leaves))


def _matches_observation(item, target) -> bool:
    accession = getattr(item, "accession", None)
    if accession is not None:
        return accession == target.accession
    return (item.period_end == target.period_end and
            item.fiscal_year == target.fiscal_year and
            item.fiscal_period == target.fiscal_period)


def _metric_at(metric_name, result, target, facts, boundaries, as_of: date, failure=None):
    _, unit, definition = _METRICS[metric_name]
    base = {"name": metric_name, "value": None, "unit": unit, "definition": definition,
            "period_end": target.period_end.isoformat(), "fiscal_period": target.target_period,
            "accessions": [], "source_filings": [], "source_fact_ids": [],
            "fact_id": None, "status": "unavailable", "reason": None}
    observation = next((item for item in result.observations
                        if _matches_observation(item, target)), None)
    if observation is not None:
        leaves = _leaf_facts(observation.provenance.source_facts, facts)
        if any(fact.filed_at > as_of for fact in leaves):
            raise SourceValidationError(FailureCode.INVALID_CONTEXT, "metric source fact leaks past as_of")
        filing_by_accession = {item.accession: item for item in boundaries}
        sources = sorted({(fact.accession, fact.filed_at.isoformat()) for fact in leaves})
        if any(accession not in filing_by_accession for accession, _ in sources):
            raise SourceValidationError(FailureCode.INVALID_CONTEXT, "metric source filing absent from snapshot")
        fact_ids = sorted({fact.fact_id for fact in leaves})
        value = str(observation.value)
        identity = json.dumps([target.ticker, metric_name, target.period_end.isoformat(),
                               value, observation.provenance.formula, fact_ids], separators=(",", ":"))
        base.update({
            "status": "ok", "value": value, "definition": observation.provenance.formula,
            "accessions": [accession for accession, _ in sources],
            "source_filings": [{"accession": accession, "filed_at": filed_at}
                               for accession, filed_at in sources],
            "source_fact_ids": fact_ids,
            "fact_id": f"sec_metric|{target.ticker}|{metric_name}|{target.period_end}|"
                       f"{hashlib.sha256(identity.encode()).hexdigest()[:20]}",
        })
        return base
    if failure is None:
        raise SourceValidationError(FailureCode.UNRESOLVED, "metric has neither observation nor failure")
    code = failure.final_failure
    boundary = next((item for item in boundaries if item.accession == failure.filing_accession), None)
    base.update({"status": _status(code), "reason": _reason(code.value, _PLAIN_FAILURES[code]),
                 "failure_detail": failure.rejection_reason})
    if boundary is not None:
        base["accessions"] = [boundary.accession]
        base["source_filings"] = [{"accession": boundary.accession,
                                   "filed_at": boundary.filed_at.isoformat()}]
    return base


def get_fundamental_metrics(
    ticker: str, *, as_of: str, db_path: Path | str = DEFAULT_FACT_DB,
    limit: int | None = None, full_history: bool = False, end_date: str | None = None,
) -> dict:
    """Return bounded Stage 3 results from local snapshots with no network or AI calls."""
    cutoff = date.fromisoformat(as_of)
    page_end = min(cutoff, date.fromisoformat(end_date)) if end_date else cutoff
    limit = limit if limit is not None else (MAX_ROWS if full_history else 20)
    if not 1 <= limit <= MAX_ROWS:
        raise ValueError(f"limit must be 1..{MAX_ROWS}")
    try:
        loaded = load_snapshot(db_path, ticker, as_of=cutoff)
        boundaries = loaded.inputs.boundaries
        facts = loaded.inputs.facts
        requested = sorted(boundaries, key=lambda item: item.period_end, reverse=True)
        latest = requested[0]
        pagable = [item for item in requested if item.period_end <= page_end]
        if not pagable:
            return _result("unavailable", as_of, reason=_reason("period_unavailable", "该页面日期之前没有财报期间。"))
        selected = pagable[:limit] if full_history else []
        evaluated = [latest, *selected]
        unique_targets = {item.accession: item for item in evaluated}
        results = {}
        for name, (function, _, _) in _METRICS.items():
            kwargs = {"ticker": ticker.upper(), "facts": facts, "boundaries": boundaries,
                      "max_periods": len(boundaries), "issuer_sic": loaded.issuer_sic,
                      "as_of": cutoff}
            if name == "gross_margin_trend":
                kwargs["ai_fallback"] = None
                kwargs["semantic_contexts"] = ()
            if name == "net_buyback_yield":
                kwargs["market_cap"] = None
            results[name] = function(**kwargs)
        failures_by_target = {}
        for name, result in results.items():
            failure_iter = iter(result.failures)
            mapped = {}
            for target in requested:
                observed = any(_matches_observation(item, target)
                               for item in result.observations)
                if not observed:
                    if len(result.failures) == 1 and result.failures[0].final_failure is FailureCode.NOT_APPLICABLE:
                        mapped[target.accession] = result.failures[0]
                    else:
                        mapped[target.accession] = next(failure_iter, None)
            failures_by_target[name] = mapped
        by_accession = {
            accession: {name: _metric_at(name, result, target, facts, boundaries, cutoff,
                                         failures_by_target[name].get(accession))
                        for name, result in results.items()}
            for accession, target in unique_targets.items()
        }
    except SourceValidationError as error:
        message = str(error) if error.code is FailureCode.PERIOD_UNAVAILABLE else _PLAIN_FAILURES[error.code]
        return _result("unavailable" if error.code is FailureCode.PERIOD_UNAVAILABLE else "error",
                       as_of, reason=_reason(error.code.value, message))
    except sqlite3.Error:
        return _result("error", as_of, reason=_reason("invalid_context", "本地事实库不可读取。"))
    latest_metrics = by_accession[latest.accession]
    rows = [{"period_end": item.period_end.isoformat(),
             "fiscal_period": item.target_period,
             "metrics": by_accession[item.accession]}
            for item in reversed(selected)]
    has_more = full_history and len(pagable) > len(selected)
    next_end = (selected[-1].period_end - timedelta(days=1)).isoformat() if has_more else None
    statuses = {item["status"] for item in latest_metrics.values()}
    status = ("ok" if "ok" in statuses else "not_applicable" if statuses == {"not_applicable"}
              else "error" if statuses == {"error"} else "unavailable")
    reason = None if status == "ok" else _reason("unresolved", "截至该日没有可计算的财务指标；逐项原因见 data.metrics。")
    accessions = sorted({accession for item in latest_metrics.values() for accession in item["accessions"]})
    source = {"provider": "sec_filing_xbrl", "accessions": accessions,
              "retrieved_at": loaded.retrieved_at}
    set_identity = json.dumps(
        [(name, item["fact_id"], item["reason"]["code"] if item["reason"] else None)
         for name, item in sorted(latest_metrics.items())], separators=(",", ":"),
    )
    return _result(status, as_of,
                   data={"ticker": ticker.upper(), "data_end_date": latest.filed_at.isoformat(),
                         "metrics": latest_metrics, "rows": rows, "returned_rows": len(rows),
                         "next_end_date": next_end},
                   source=source,
                   fact_id=f"sec_metric_set|{ticker.upper()}|{latest.period_end}|"
                           f"{hashlib.sha256(set_identity.encode()).hexdigest()[:20]}",
                   reason=reason)
