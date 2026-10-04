"""Point-in-time Stage 3 tool fixtures; all SEC data stays local."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from thesis_tracker.agent.tools import get_fundamental_metrics
from thesis_tracker.financial.models import (
    FactKind,
    FactOrigin,
    FilingBoundary,
    FinancialFact,
    Unit,
)
from thesis_tracker.financial.pit_collect import collect_filing_snapshots
from thesis_tracker.financial.pit_store import (
    DEFAULT_FACT_DB,
    SnapshotFiling,
    append_filings,
)
from thesis_tracker.financial.tool import _METRICS
from thesis_tracker.metrics.financial import (
    MarketCapInput,
    compute_net_buyback_yield,
)
from thesis_tracker.prices_batch import load_universe


def boundary(accession, filed, period, end, *, form="10-Q", year=2025):
    return FilingBoundary(
        ticker="TEST", accession=accession, filed_at=date.fromisoformat(filed),
        form=form, fiscal_year=year, fiscal_period=period,
        period_end=date.fromisoformat(end), source="sec_filing_metadata",
    )


def fact(item, concept, amount, start):
    return FinancialFact(
        ticker=item.ticker, accession=item.accession, concept=concept,
        value=Decimal(amount), unit=Unit.USD, period_start=date.fromisoformat(start),
        period_end=item.period_end, filed_at=item.filed_at, form=item.form,
        fiscal_year=item.fiscal_year, fiscal_period=item.fiscal_period,
        source="sec_filing_xbrl", extraction_path="fixture", context_id=concept,
        dimensions=(), origin=FactOrigin.REPORTED, resolver_path=("fixture",),
        fact_kind=FactKind.DURATION,
    )


def filing(item, income, cash, start, *, original=None, accepted="12:00:00+00:00"):
    return SnapshotFiling(
        boundary=item, original_accession=original or item.accession,
        cik="0000000001", acceptance_at=f"{item.filed_at}T{accepted}",
        issuer_sic="3571", registered_count=2,
        facts=(fact(item, "us-gaap:NetIncomeLoss", income, start),
               fact(item, "us-gaap:NetCashProvidedByUsedInOperatingActivities", cash, start)),
    )


def latest(result, metric="cash_conversion"):
    return result["data"]["metrics"][metric]


def test_original_amendment_and_before_first_filing(tmp_path):
    db = tmp_path / "facts.db"
    q1 = boundary("q1", "2025-05-01", "Q1", "2025-03-31")
    original = boundary("q2", "2025-08-01", "Q2", "2025-06-30")
    amended = boundary("q2a", "2025-08-20", "Q2", "2025-06-30", form="10-Q/A")
    append_filings(db, [filing(q1, "50", "60", "2025-01-01"),
                        filing(original, "100", "150", "2025-04-01"),
                        filing(amended, "100", "200", "2025-04-01", original="q2")])
    before = get_fundamental_metrics("TEST", as_of="2025-04-30", db_path=db)
    assert before["status"] == "unavailable"
    assert "截至该日没有已披露的财报" in before["reason"]["message"]
    between = get_fundamental_metrics("TEST", as_of="2025-08-10", db_path=db)
    after = get_fundamental_metrics("TEST", as_of="2025-08-20", db_path=db)
    assert latest(between)["value"] == "1.5"
    assert latest(between)["accessions"] == ["q2"]
    assert latest(after)["value"] == "2"
    assert latest(after)["accessions"] == ["q2a"]
    assert latest(after)["source_fact_ids"]
    assert latest(after)["fact_id"] != latest(between)["fact_id"]


def test_compute_as_of_excludes_later_filing_and_facts():
    first = boundary("q1", "2025-05-01", "Q1", "2025-03-31")
    second = boundary("q2", "2025-08-01", "Q2", "2025-06-30")
    facts = (*filing(first, "50", "60", "2025-01-01").facts,
             *filing(second, "100", "150", "2025-04-01").facts)
    for name, (compute, _, _) in _METRICS.items():
        kwargs = dict(ticker="TEST", facts=facts, boundaries=(first, second),
                      as_of=date(2025, 5, 2))
        if name == "gross_margin_trend":
            kwargs["ai_fallback"] = None
        if name == "net_buyback_yield":
            kwargs["market_cap"] = None
        result = compute(**kwargs)
        assert all(item.period_end < second.period_end for item in result.observations)
        assert all(item.filing_accession != second.accession for item in result.failures)


def test_sparse_amendment_keeps_complete_original_financial_member(tmp_path):
    db = tmp_path / "facts.db"
    q1 = boundary("q1", "2025-05-01", "Q1", "2025-03-31")
    q2 = boundary("q2", "2025-08-01", "Q2", "2025-06-30")
    amended = boundary("q2a", "2025-08-20", "Q2", "2025-06-30", form="10-Q/A")
    sparse = replace(filing(amended, "100", "200", "2025-04-01", original="q2"),
                     registered_count=1)
    append_filings(db, [filing(q1, "50", "60", "2025-01-01"),
                        filing(q2, "100", "150", "2025-04-01"), sparse])
    result = get_fundamental_metrics("TEST", as_of="2025-08-20", db_path=db)
    assert latest(result)["value"] == "1.5"
    assert latest(result)["accessions"] == ["q2"]


def test_conflicting_accession_snapshot_is_rejected(tmp_path):
    db = tmp_path / "facts.db"
    q1 = boundary("q1", "2025-05-01", "Q1", "2025-03-31")
    original = filing(q1, "50", "60", "2025-01-01")
    conflicting = filing(q1, "50", "90", "2025-01-01")
    append_filings(db, [original])
    with pytest.raises(ValueError, match="conflicting accession snapshot"):
        append_filings(db, [conflicting])
    assert append_filings(db, [original]) == 0


def test_as_of_never_uses_supplied_unversioned_market_cap():
    q1 = boundary("q1", "2025-05-01", "Q1", "2025-03-31")
    cap = MarketCapInput(value=Decimal("1000"), period_end=q1.period_end,
                         source="unversioned_test_value")
    result = compute_net_buyback_yield(
        ticker="TEST", facts=filing(q1, "50", "60", "2025-01-01").facts,
        boundaries=(q1,), as_of=date(2025, 5, 1), market_cap=cap,
    )
    assert result.failures[0].final_failure.value == "missing_external_data"


def test_tool_failure_has_plain_language_reason(tmp_path):
    db = tmp_path / "facts.db"
    q1 = boundary("q1", "2025-05-01", "Q1", "2025-03-31")
    append_filings(db, [filing(q1, "50", "60", "2025-01-01")])
    result = get_fundamental_metrics("TEST", as_of="2025-05-01", db_path=db)
    buyback = latest(result, "net_buyback_yield")
    assert buyback["reason"]["code"] == "missing_external_data"
    assert "历史市值" in buyback["reason"]["message"]


def test_q4_derived_value_waits_for_10k_disclosure(tmp_path):
    db = tmp_path / "facts.db"
    q3 = boundary("q3", "2025-11-01", "Q3", "2025-09-30")
    annual = boundary("fy", "2026-02-15", "Q4", "2025-12-31", form="10-K")
    append_filings(db, [filing(q3, "60", "120", "2025-01-01"),
                        filing(annual, "100", "200", "2025-01-01")])
    before = get_fundamental_metrics("TEST", as_of="2026-02-14", db_path=db,
                                     full_history=True)
    after = get_fundamental_metrics("TEST", as_of="2026-02-15", db_path=db)
    assert all(row["period_end"] != "2025-12-31" for row in before["data"]["rows"])
    assert latest(after)["value"] == "2"
    assert set(latest(after)["accessions"]) == {"q3", "fy"}
    assert all(item["filed_at"] <= "2026-02-15" for item in latest(after)["source_filings"])


def test_same_day_filings_use_sec_acceptance_order(tmp_path):
    db = tmp_path / "facts.db"
    q1 = boundary("q1", "2025-05-01", "Q1", "2025-03-31")
    original = boundary("q2", "2025-08-01", "Q2", "2025-06-30")
    first = boundary("q2a", "2025-08-20", "Q2", "2025-06-30", form="10-Q/A")
    second = boundary("q2b", "2025-08-20", "Q2", "2025-06-30", form="10-Q/A")
    append_filings(db, [filing(q1, "50", "60", "2025-01-01"),
                        filing(original, "100", "150", "2025-04-01"),
                        filing(first, "100", "200", "2025-04-01", original="q2", accepted="10:00:00+00:00"),
                        filing(second, "100", "250", "2025-04-01", original="q2", accepted="15:00:00+00:00")])
    result = get_fundamental_metrics("TEST", as_of="2025-08-20", db_path=db)
    assert latest(result)["value"] == "2.5"
    assert latest(result)["accessions"] == ["q2b"]


def test_tool_never_opens_network_or_uses_ai(tmp_path, monkeypatch):
    import socket

    from thesis_tracker.financial.ai_fallback import AiFallback

    db = tmp_path / "facts.db"
    q1 = boundary("q1", "2025-05-01", "Q1", "2025-03-31")
    q2 = boundary("q2", "2025-08-01", "Q2", "2025-06-30")
    append_filings(db, [filing(q1, "50", "60", "2025-01-01"),
                        filing(q2, "100", "150", "2025-04-01")])

    def forbidden(*args, **kwargs):
        raise AssertionError("network or AI invoked")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(AiFallback, "recover", forbidden)
    result = get_fundamental_metrics("TEST", as_of="2025-08-01", db_path=db)
    assert latest(result)["value"] == "1.5"


def test_pagination_has_same_100_row_limit(tmp_path):
    db = tmp_path / "facts.db"
    q1 = boundary("q1", "2025-05-01", "Q1", "2025-03-31")
    q2 = boundary("q2", "2025-08-01", "Q2", "2025-06-30")
    append_filings(db, [filing(q1, "50", "60", "2025-01-01"),
                        filing(q2, "100", "150", "2025-04-01")])
    result = get_fundamental_metrics("TEST", as_of="2025-08-01", db_path=db, full_history=True, limit=1)
    assert len(result["data"]["rows"]) == 1
    assert result["data"]["next_end_date"] == "2025-06-29"
    with pytest.raises(ValueError):
        get_fundamental_metrics("TEST", as_of="2025-08-01", db_path=db, limit=101)


def test_collector_uses_injected_selector_and_appends_once(tmp_path):
    from types import SimpleNamespace

    original = boundary("q2", "2025-08-01", "Q2", "2025-06-30")
    class Xbrl:
        entity_info = {"fiscal_period": "Q2", "fiscal_year": 2025}
        contexts = {"us-gaap:NetIncomeLoss": SimpleNamespace(dimensions={}),
                    "us-gaap:NetCashProvidedByUsedInOperatingActivities": SimpleNamespace(dimensions={})}

        class Facts:
            def get_facts(self):
                return [dict(concept=concept, period_type="duration", context_ref=concept,
                             period_start="2025-04-01", period_end="2025-06-30", value=value,
                             currency="USD")
                        for concept, value in (("us-gaap:NetIncomeLoss", "100"),
                                               ("us-gaap:NetCashProvidedByUsedInOperatingActivities", "150"))]

        facts = Facts()

    metadata = SimpleNamespace(accession="q2", filing_date=original.filed_at,
                               report_date=original.period_end, form="10-Q", base_form="10-Q",
                               acceptance_datetime=None, cik="0000000001")
    member = SimpleNamespace(metadata=metadata, filing=SimpleNamespace(xbrl=lambda: Xbrl()),
                             company=SimpleNamespace(sic="3571"))
    selector = SimpleNamespace(select=lambda request, today: (SimpleNamespace(members=(member,)),))
    db = tmp_path / "facts.db"
    first = collect_filing_snapshots("TEST", since=date(2025, 1, 1),
                                     until=date(2025, 12, 31), db_path=db, selector=selector)
    second = collect_filing_snapshots("TEST", since=date(2025, 1, 1),
                                      until=date(2025, 12, 31), db_path=db, selector=selector)
    assert (first["added_filings"], second["added_filings"]) == (1, 0)
    assert second["xbrl_loads"] == 0
    result = get_fundamental_metrics("TEST", as_of="2025-08-01", db_path=db)
    assert result["data"]["data_end_date"] == "2025-08-01"
    assert latest(result)["reason"]["code"] == "period_unavailable"


@pytest.mark.skipif(not DEFAULT_FACT_DB.exists(), reason="real SEC fact snapshot not collected")
def test_real_quarterly_as_of_never_leaks_future_filings():
    tickers = load_universe()[:-1]
    assert len(tickers) == 15
    for ticker in tickers:
        for year in range(2019, 2027):
            for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
                cutoff = date(year, month, day)
                if cutoff > date(2026, 9, 30):
                    continue
                result = get_fundamental_metrics(ticker, as_of=cutoff.isoformat(),
                                                 db_path=Path(DEFAULT_FACT_DB))
                if result["data"] is None:
                    assert result["reason"]["code"] == "period_unavailable"
                    continue
                for metric in result["data"]["metrics"].values():
                    for filing in metric["source_filings"]:
                        assert date.fromisoformat(filing["filed_at"]) <= cutoff
