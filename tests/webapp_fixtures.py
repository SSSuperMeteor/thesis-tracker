"""Deterministic local databases for the local web app tests.

Every fixture here is a miniature copy of the real runtime databases.  The fact
rows are taken from the repository's own ``data/cache/financial_facts.db`` when
it exists, so the read-only web queries are exercised against real SEC payloads
rather than hand-written approximations.  The matching expectations live in
``tests/fixtures/webapp_fixture_expectations.json``, written only by the
explicit regeneration entry point (``uv run python tests/webapp_fixtures.py``)
and otherwise read-only, so tests never depend on mutable local state.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from thesis_tracker.financial.models import FactKind, FactOrigin, FinancialFact, Unit

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_FACT_DB = REPO_ROOT / "data/cache/financial_facts.db"
EXPECTATIONS_PATH = Path(__file__).resolve().parent / "fixtures/webapp_fixture_expectations.json"
ANCHOR_DAY = "2024-01-02"
STALE_ANCHOR_DAY = "2025-11-03"
# 700 weekdays from 2024-01-02 end on 2026-09-07, which is also the fixture's
# analysis date.  Both D03 and the point-in-time read are satisfied there: the
# newest stored price is the as_of day itself (age 0), and the newest filing in
# FILINGS is filed 2026-08-26, so no stored fact leaks past as_of.
FRESH_DAYS = 700
FIXTURE_AS_OF = "2026-09-07"

SCHEMA = """
CREATE TABLE filing_snapshots (
    accession TEXT PRIMARY KEY, original_accession TEXT NOT NULL,
    ticker TEXT NOT NULL, cik TEXT NOT NULL, form TEXT NOT NULL,
    period_end TEXT NOT NULL, filed_at TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL, fiscal_period TEXT NOT NULL,
    acceptance_at TEXT, issuer_sic TEXT, registered_count INTEGER NOT NULL,
    retrieved_at TEXT NOT NULL
);
CREATE TABLE financial_fact_snapshots (
    accession TEXT NOT NULL, ordinal INTEGER NOT NULL,
    filed_at TEXT NOT NULL, retrieved_at TEXT NOT NULL,
    payload_json TEXT NOT NULL, PRIMARY KEY(accession, ordinal),
    FOREIGN KEY(accession) REFERENCES filing_snapshots(accession)
);
CREATE TABLE price_windows (
    symbol TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
    provider TEXT NOT NULL, retrieved_at TEXT NOT NULL, row_count INTEGER NOT NULL,
    PRIMARY KEY(symbol, start_date, end_date)
);
CREATE TABLE daily_prices (
    symbol TEXT NOT NULL, date TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL,
    low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL,
    adj_open REAL, adj_high REAL, adj_low REAL, adj_close REAL, adj_volume REAL,
    dividend_cash REAL, split_factor REAL,
    window_start TEXT NOT NULL, window_end TEXT NOT NULL,
    retrieved_at TEXT NOT NULL, provider TEXT NOT NULL,
    PRIMARY KEY(symbol, date)
);
CREATE TABLE decision_cards (
    card_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, ticker TEXT NOT NULL,
    as_of TEXT NOT NULL, creation_price TEXT, card_json TEXT NOT NULL, snapshot_json TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL, validator_version TEXT NOT NULL,
    validation_json TEXT NOT NULL, requested_model TEXT, returned_model TEXT,
    fingerprint TEXT, prompt_version TEXT, input_tokens INTEGER,
    output_tokens INTEGER, cache_hit_tokens INTEGER
);
CREATE TABLE decision_attempts (
    attempt_id TEXT PRIMARY KEY, analysis_id TEXT NOT NULL, attempt_no INTEGER NOT NULL,
    ticker TEXT NOT NULL, as_of TEXT NOT NULL, raw_output TEXT NOT NULL,
    violations_json TEXT NOT NULL, passed INTEGER NOT NULL, created_at TEXT NOT NULL,
    requested_horizon TEXT, UNIQUE(analysis_id, attempt_no)
);
CREATE TABLE decision_model_calls (
    model_call_id TEXT PRIMARY KEY, analysis_id TEXT NOT NULL, round_no INTEGER NOT NULL,
    requested_model TEXT NOT NULL, returned_model TEXT, fingerprint TEXT,
    input_tokens INTEGER, output_tokens INTEGER, cache_hit_tokens INTEGER,
    created_at TEXT NOT NULL, UNIQUE(analysis_id, round_no)
);
CREATE TABLE decision_tool_calls (
    tool_call_id TEXT PRIMARY KEY, analysis_id TEXT NOT NULL, call_no INTEGER NOT NULL,
    tool_name TEXT NOT NULL, arguments_json TEXT NOT NULL, envelope_status TEXT NOT NULL,
    created_at TEXT NOT NULL, UNIQUE(analysis_id, call_no)
);
"""

# Three issuers with three different fiscal calendars on purpose: AAPL closes
# quarters in late December/March/June/September, NVDA in late
# January/April/July/October, MSFT on the calendar month end.
COMPANIES = ("AAPL", "NVDA", "MSFT")
CIK = {"AAPL": "0000320193", "NVDA": "0001045810", "MSFT": "0000789019"}

# (accession, form, period_end, filed_at, fiscal_year, fiscal_period, registered)
FILINGS = {
    "AAPL": (
        ("0000320193-25-000008", "10-Q", "2024-12-28", "2025-01-31", 2025, "Q1", 111),
        ("0000320193-25-000057", "10-Q", "2025-03-29", "2025-05-02", 2025, "Q2", 175),
        ("0000320193-25-000073", "10-Q", "2025-06-28", "2025-08-01", 2025, "Q3", 175),
        ("0000320193-25-000079", "10-K", "2025-09-27", "2025-10-31", 2025, "Q4", 184),
        ("0000320193-26-000006", "10-Q", "2025-12-27", "2026-01-30", 2026, "Q1", 125),
        ("0000320193-26-000013", "10-Q", "2026-03-28", "2026-05-01", 2026, "Q2", 203),
        ("0000320193-26-000020", "10-Q", "2026-06-27", "2026-07-31", 2026, "Q3", 202),
    ),
    "NVDA": (
        ("0001045810-25-000010", "10-Q", "2025-04-27", "2025-05-28", 2026, "Q1", 180),
        ("0001045810-25-000045", "10-Q", "2025-07-27", "2025-08-27", 2026, "Q2", 181),
        ("0001045810-25-000088", "10-Q", "2025-10-26", "2025-11-19", 2026, "Q3", 182),
        ("0001045810-26-000004", "10-K", "2026-01-25", "2026-02-25", 2026, "Q4", 190),
        ("0001045810-26-000041", "10-Q", "2026-04-26", "2026-05-20", 2026, "Q1", 183),
        ("0001045810-26-000079", "10-Q", "2026-07-26", "2026-08-26", 2026, "Q2", 184),
    ),
    "MSFT": (
        ("0000789019-25-000011", "10-Q", "2024-12-31", "2025-01-28", 2025, "Q2", 150),
        ("0000789019-25-000052", "10-Q", "2025-03-31", "2025-04-29", 2025, "Q3", 151),
        ("0000789019-25-000090", "10-K", "2025-06-30", "2025-07-29", 2025, "Q4", 190),
        ("0000789019-25-000120", "10-Q", "2025-09-30", "2025-10-28", 2026, "Q1", 152),
        ("0000789019-26-000013", "10-Q", "2025-12-31", "2026-01-27", 2026, "Q2", 153),
        ("0000789019-26-000049", "10-Q", "2026-03-31", "2026-04-28", 2026, "Q3", 154),
        ("0000789019-26-000088", "10-K", "2026-06-30", "2026-07-28", 2026, "Q4", 191),
    ),
}

# A sparse 10-K/A over the January quarter: it must not hide the original 10-K
# facts, and it must be reported as an amendment of that original accession.
AMENDMENT = ("0001045810-26-000090", "10-K/A", "2026-01-25", "2026-03-10", 2026, "Q4", 1,
             "0001045810-26-000004")

# A 10-K landing in the same calendar quarter as an existing 10-Q, to prove the
# calendar-quarter grouping is independent of form type and fiscal year.  Its
# period end (February) shares the January-March column with AAPL's 10-Q for
# 2026-03-28, which is the state the overview must survive.
QUARTER_COLLISION = ("0000320193-26-000031", "10-K", "2026-02-15", "2026-03-20", 2026, "Q4")

# The calendar quarter shared by 2026-02-15 and 2026-03-28.
COLLISION_QUARTER = "2026Q1"

REJECTED_RULES = (
    ("D02", "reasons[0].text", "理由中的事实数字必须使用事实占位符。"),
    ("D11", "reasons[1].text", "理由正文写了与动作“分批”不一致的动作词“买入”。"),
)

CARD_CREATED_AT = "2026-10-05T00:44:06+00:00"


@dataclass(frozen=True, slots=True)
class Fixture:
    """Paths plus the expectations a web app test needs."""

    fact_db: Path
    price_db: Path
    card_db: Path
    reference_date: str
    card_id: str
    rejected_analysis_id: str
    passed_analysis_id: str
    real_facts: bool
    expectations: dict


def _business_days(start: str, count: int) -> list[str]:
    day = date.fromisoformat(start)
    days: list[str] = []
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def _close(index: int, base: float) -> float:
    return round(base + index * 0.1, 4)


def _synthetic_payloads(ticker: str, accession: str, form: str, period_end: str,
                        filed_at: str, fiscal_year: int, fiscal_period: str) -> list[str]:
    """Stand-in payloads for a checkout without runtime data.

    The accession must match the filing row exactly: ``load_snapshot`` rejects a
    payload whose ``fact.accession`` differs from its filing snapshot.
    """
    fact = FinancialFact(
        ticker=ticker, accession=accession, concept="us-gaap:Revenues",
        value=Decimal("1000000"), unit=Unit.USD, period_start=None,
        period_end=date.fromisoformat(period_end), filed_at=date.fromisoformat(filed_at),
        form=form, fiscal_year=fiscal_year, fiscal_period=fiscal_period,
        source="sec_filing_xbrl", extraction_path="fixture", context_id="c-1",
        dimensions=(), origin=FactOrigin.REPORTED, resolver_path=("stage1_boundary",),
        fact_kind=FactKind.INSTANT,
    )
    return [json.dumps(fact.to_dict(), sort_keys=True)]


def _real_payloads(accession: str) -> list[str] | None:
    if not REPO_FACT_DB.exists():
        return None
    connection = sqlite3.connect(f"file:{REPO_FACT_DB.resolve()}?mode=ro", uri=True)
    try:
        rows = [row[0] for row in connection.execute(
            "SELECT payload_json FROM financial_fact_snapshots WHERE accession=? "
            "ORDER BY ordinal", (accession,))]
    finally:
        connection.close()
    return rows or None


def _payloads(ticker: str, accession: str, form: str, period_end: str, filed_at: str,
              year: int, period: str) -> list[str]:
    real = _real_payloads(accession)
    if real is not None:
        return real
    return _synthetic_payloads(ticker, accession, form, period_end, filed_at, year, period)


def build_fact_db(path: Path) -> bool:
    """Write the miniature SEC snapshot database; return True when facts are real."""
    real = REPO_FACT_DB.exists()
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    retrieved = "2026-10-05T00:00:00+00:00"
    rows: list[tuple] = []
    for ticker in COMPANIES:
        for accession, form, period_end, filed_at, year, period, registered in FILINGS[ticker]:
            rows.append((accession, accession, ticker, form, period_end, filed_at, year,
                         period, registered,
                         _payloads(ticker, accession, form, period_end, filed_at, year, period)))
    accession, form, period_end, filed_at, year, period, registered, original = AMENDMENT
    rows.append((accession, original, "NVDA", form, period_end, filed_at, year, period,
                 registered,
                 _payloads("NVDA", accession, form, period_end, filed_at, year, period)))
    accession, form, period_end, filed_at, year, period = QUARTER_COLLISION
    payloads = _payloads("AAPL", accession, form, period_end, filed_at, year, period)
    rows.append((accession, accession, "AAPL", form, period_end, filed_at, year, period,
                 len(payloads), payloads))
    with connection:
        for (accession, original, ticker, form, period_end, filed_at, year, period,
             registered, payloads) in rows:
            connection.execute(
                "INSERT INTO filing_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (accession, original, ticker, CIK[ticker], form, period_end, filed_at,
                 year, period, None, "3571", registered, retrieved))
            for ordinal, payload in enumerate(payloads):
                connection.execute(
                    "INSERT INTO financial_fact_snapshots VALUES (?,?,?,?,?)",
                    (accession, ordinal, filed_at, retrieved, payload))
    connection.close()
    return real


def _write_series(connection: sqlite3.Connection, symbol: str, days: list[str],
                  base: float, retrieved: str) -> None:
    with connection:
        for index, day in enumerate(days):
            close = _close(index, base)
            connection.execute(
                "INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (symbol, day, close, round(close + 1.0, 4), round(close - 1.0, 4), close,
                 1_000_000.0, close, round(close + 1.0, 4), round(close - 1.0, 4),
                 close, 1_000_000.0, 0.0, 1.0, days[0], days[-1], retrieved, "tiingo"))
        connection.execute(
            "INSERT INTO price_windows VALUES (?,?,?,?,?,?)",
            (symbol, days[0], days[-1], "tiingo", retrieved, len(days)))


def build_price_db(path: Path) -> dict:
    """AAPL has fresh prices, NVDA has stale prices, MSFT has none at all."""
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    fresh = _business_days(ANCHOR_DAY, FRESH_DAYS)
    assert fresh[-1] == FIXTURE_AS_OF, fresh[-1]
    newest_filing = max(filed_at for rows in FILINGS.values() for _, _, _, filed_at, _, _, _ in rows)
    assert newest_filing < FIXTURE_AS_OF, newest_filing
    stale = _business_days(STALE_ANCHOR_DAY, 100)
    _write_series(connection, "AAPL", fresh, 180.0, "2026-10-05T00:40:00+00:00")
    _write_series(connection, "SPY", fresh, 470.0, "2026-10-05T00:40:00+00:00")
    _write_series(connection, "NVDA", stale, 140.0, "2026-10-05T00:41:00+00:00")
    connection.close()
    return {
        "AAPL": {"start": fresh[0], "end": fresh[-1], "rows": len(fresh),
                 "latest_close": _close(len(fresh) - 1, 180.0)},
        "NVDA": {"start": stale[0], "end": stale[-1], "rows": len(stale),
                 "latest_close": _close(len(stale) - 1, 140.0)},
        "MSFT": None,
    }


def fixture_snapshot(ticker: str, price_db: Path, fact_db: Path) -> dict:
    """Build an EvidenceSnapshot from the fixture databases, without writing."""
    from thesis_tracker.decision.core import fact_index
    from thesis_tracker.financial.tool import get_fundamental_metrics
    from thesis_tracker.indicator_tool import get_indicators
    from thesis_tracker.prices import get_price_history

    as_of = FIXTURE_AS_OF
    calls = [
        {"tool": "get_price_history", "args": {"symbol": ticker, "as_of": as_of},
         "envelope": get_price_history(ticker, as_of=as_of, db_path=price_db)},
        {"tool": "get_indicators", "args": {"symbol": ticker, "as_of": as_of},
         "envelope": get_indicators(ticker, as_of=as_of, db_path=price_db)},
        {"tool": "get_fundamental_metrics", "args": {"ticker": ticker, "as_of": as_of},
         "envelope": get_fundamental_metrics(ticker, as_of=as_of, db_path=fact_db)},
    ]
    snapshot = {"ticker": ticker, "as_of": as_of, "calls": calls}
    snapshot["fact_index"] = fact_index(snapshot)[0]
    return snapshot


def build_card_db(path: Path, *, price_db: Path, fact_db: Path) -> tuple[str, str, str]:
    """Archive one passing card and one rejected attempt. Returns their ids."""
    from thesis_tracker.decision.core import build_card, fact_index, validate_card

    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    card_id = "c" * 36
    passed_analysis = "a" * 36
    rejected_analysis = "b" * 36
    ticker = "AAPL"
    snapshot = fixture_snapshot(ticker, price_db, fact_db)
    index = fact_index(snapshot)[0]
    day = snapshot["calls"][0]["envelope"]["data"]["data_end_date"]
    close = index[f"tiingo|{ticker}|{day}|daily"]
    rsi = index[f"tiingo|{ticker}|{day}|indicator|rsi_14"]
    price = Decimal(str(close["value"]))
    stop = (price * Decimal("0.90")).quantize(Decimal("0.01"))
    target = (price * Decimal("1.20")).quantize(Decimal("0.01"))
    low = (price * Decimal("0.99")).quantize(Decimal("0.01"))
    high = (price * Decimal("1.01")).quantize(Decimal("0.01"))
    draft = {
        "ticker": ticker, "as_of": FIXTURE_AS_OF, "horizon": "中期",
        "bias": "看多", "action": "分批", "confidence": "中",
        "entry_range": [float(low), float(high)], "stop_loss": float(stop),
        "target_price": float(target),
        "fact_ids": [close["fact_id"], rsi["fact_id"]],
        "reasons": [
            {"text": f"收盘价 {{fact:{close['fact_id']}}} 站上短均线。",
             "fact_ids": [close["fact_id"]]},
            {"text": f"RSI 为 {{fact:{rsi['fact_id']}}}，未过热。",
             "fact_ids": [rsi["fact_id"]]},
        ],
        "invalidations": [{"kind": "close_below", "price": float(stop),
                           "text": "收盘价跌破止损位，分批逻辑失效。"}],
        "stop_rationale": f"跌破 {{fact:{rsi['fact_id']}}} 所示支撑即离场。",
        "target_rationale": f"上看 {{fact:{close['fact_id']}}} 上方的前高区域。",
    }
    card = build_card(draft, snapshot)
    violations = validate_card(card, snapshot)
    assert violations == [], violations
    card_json = json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"))
    with connection:
        connection.execute(
            "INSERT INTO decision_cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (card_id, CARD_CREATED_AT, ticker, FIXTURE_AS_OF, str(card["creation_price"]),
             card_json, snapshot_json,
             hashlib.sha256(snapshot_json.encode()).hexdigest(), "decision-validator-3",
             "[]", "deepseek-flash", "deepseek-flash", None,
             "decision-agent-v5-entry-stop-rules-2026-10-04", 22262, 6415, 9344))
        connection.execute(
            "INSERT INTO decision_attempts VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("attempt-passed", passed_analysis, 1, ticker, FIXTURE_AS_OF,
             json.dumps(draft, ensure_ascii=False), "[]", 1, CARD_CREATED_AT, "中期"))
        connection.execute(
            "INSERT INTO decision_model_calls VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("call-passed", passed_analysis, 1, "deepseek-flash", "deepseek-flash",
             None, 22262, 6415, 9344, CARD_CREATED_AT))
        connection.execute(
            "INSERT INTO decision_attempts VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("attempt-rejected", rejected_analysis, 1, ticker, FIXTURE_AS_OF,
             json.dumps(draft, ensure_ascii=False),
             json.dumps([{"rule": rule, "location": location, "message": message}
                         for rule, location, message in REJECTED_RULES],
                        ensure_ascii=False),
             0, "2026-10-05T00:50:00+00:00", "中期"))
    connection.close()
    return card_id, rejected_analysis, passed_analysis


def build_fixture(root: Path) -> Fixture:
    """Build all three databases under ``root`` and return their expectations."""
    root.mkdir(parents=True, exist_ok=True)
    fact_db = root / "financial_facts.db"
    price_db = root / "prices.db"
    card_db = root / "cards.db"
    real = build_fact_db(fact_db)
    prices = build_price_db(price_db)
    card_id, rejected, passed = build_card_db(card_db, price_db=price_db, fact_db=fact_db)
    expectations = json.loads(EXPECTATIONS_PATH.read_text(encoding="utf-8"))
    # The reference day is the newest stored price itself: AAPL is "0 days old"
    # and NVDA is months stale, so both the fresh and the expired branch are
    # exercised by the same fixture.
    reference = prices["AAPL"]["end"]
    return Fixture(fact_db=fact_db, price_db=price_db, card_db=card_db,
                   reference_date=reference, card_id=card_id,
                   rejected_analysis_id=rejected, passed_analysis_id=passed,
                   real_facts=real, expectations=expectations)


def regenerate(path: Path = EXPECTATIONS_PATH) -> dict:
    """Rebuild the checked-in expectations from the repository's real databases.

    Run explicitly (``uv run python -m tests.webapp_fixtures``) whenever the
    local runtime data is refreshed; never from inside a test.
    """
    from thesis_tracker.financial.tool import get_fundamental_metrics

    if not REPO_FACT_DB.exists():
        raise SystemExit("data/cache/financial_facts.db is missing; cannot regenerate")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fact_db = root / "facts.db"
        build_fact_db(fact_db)
        metrics = {
            ticker: {
                "status": (envelope := get_fundamental_metrics(
                    ticker, as_of=FIXTURE_AS_OF, db_path=fact_db))["status"],
                "data_end_date": (envelope.get("data") or {}).get("data_end_date"),
                "values": {name: item["value"]
                           for name, item in ((envelope.get("data") or {}).get("metrics")
                                              or {}).items() if item["status"] == "ok"},
            }
            for ticker in COMPANIES
        }
        prices = build_price_db(root / "prices.db")
    payload = {
        "as_of": FIXTURE_AS_OF,
        "metric_envelopes": metrics,
        "prices": prices,
        "collision_quarter": COLLISION_QUARTER,
        "amendment": {
            "accession": AMENDMENT[0],
            "original_accession": AMENDMENT[7],
            "form": AMENDMENT[1],
            "period_end": AMENDMENT[2],
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return payload


if __name__ == "__main__":
    regenerate()
    print(f"wrote {EXPECTATIONS_PATH}")
