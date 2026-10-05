"""Read-only view models for the local web app.

Every number and label a page shows is produced here by reusing the existing
display functions (``decision.evidence.display_value`` / ``display_text`` /
``display_label``) and the existing read-only tools.  Nothing in this module
recomputes a financial value, and nothing here writes to any database.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from thesis_tracker.decision.core import (
    DEFAULT_ARCHIVE,
    PRICE_STALENESS_DAYS,
    auto_computed,
    fact_index,
)
from thesis_tracker.decision.evidence import display_label, display_text
from thesis_tracker.financial.pit_store import DEFAULT_FACT_DB
from thesis_tracker.financial.tool import get_fundamental_metrics
from thesis_tracker.prices import DEFAULT_DB as DEFAULT_PRICE_DB
from thesis_tracker.webapp import data as store
from thesis_tracker.webapp import display

# Advisory thresholds for the card page's hint line.  These are heuristics,
# not validated rules: they are shown next to the numbers, never fed back into
# validation, and are expected to be revisited once expiry results exist.
MIN_REWARD_RISK = 1.5
MIN_STOP_ATR_MULTIPLE = 2.0

# Where each kind of fact came from, in the words a reader uses.
SOURCE_LABELS = {
    "sec_filing_xbrl": "SEC 财报",
    "tiingo": "行情",
    "derived": "派生计算",
    "decision_archive": "建议卡（AI 判断）",
}
# Units whose value is text rather than a number.  A card's judgment fields
# (action, tendency, horizon, creation time) are citable facts, but they are not
# measurements, so the numeric display rule does not apply to them: running a
# Decimal conversion over "分批" raises rather than formatting.
TEXT_UNITS = frozenset({"text"})
# Derived facts group last; the other two follow the order a reader checks them.
FACT_GROUP_LABELS = {"market": "行情与指标", "fundamental": "财报指标",
                     "derived": "派生", "card": "建议卡（AI 判断）"}
FACT_GROUP_ORDER = ("market", "fundamental", "derived", "card")

# The eight deterministic Stage 3 metrics, in the order their own module lists
# them.  Labels and units come from decision.evidence, never from this file.
FUNDAMENTAL_METRICS = (
    "gross_margin_trend", "accruals_ratio", "cash_conversion",
    "ar_growth_vs_rev_growth", "net_buyback_yield", "diluted_share_count_yoy",
    "interest_coverage", "net_debt_to_ebitda",
)

USAGE_WINDOW = 5

# The heading the card renderer uses for its arithmetic section; the page strips
# the language from it rather than renaming a section the CLI still prints.
AUTO_COMPUTED_HEADING = "自动计算（Python）"


def quarter_key(period_end: str) -> str:
    """Calendar-quarter bucket of a real period end date, e.g. 2026-04-25 -> 2026Q2.

    Columns are grouped by the calendar quarter the period end falls in, never
    by a fiscal-quarter ordinal: issuers with different fiscal year ends must
    land in different columns, and no page may say "fiscal Q3".
    """
    day = date.fromisoformat(period_end)
    return f"{day.year}Q{(day.month - 1) // 3 + 1}"


def _key_form(form: str) -> str:
    return form.replace("/A", "")


def _decorate(rows: list[dict]) -> list[dict]:
    """Flag amendments from the accession families actually present in the store."""
    by_accession = {row["accession"]: row for row in rows}
    families: dict[tuple[str, str, str], list[dict]] = {}
    for row in rows:
        families.setdefault(
            (row["ticker"], row["original_accession"], row["period_end"]), []
        ).append(row)
    for members in families.values():
        members.sort(key=lambda row: (row["filed_at"], row["accession"]))
    decorated: list[dict] = []
    for row in rows:
        members = families[(row["ticker"], row["original_accession"], row["period_end"])]
        first = members[0]["filed_at"]
        # An amendment is a later accession in the same filing family.  A sparse
        # 10-K/A is later, never "first", so it can never displace the original.
        decorated.append({
            **row,
            "key_form": _key_form(row["form"]),
            "is_amendment": row["filed_at"] > first,
            "family_size": len(members),
            "superseded": row["original_accession"] not in by_accession,
        })
    return decorated


def _entry(row: dict) -> dict:
    return {
        "accession": row["accession"],
        "original_accession": row["original_accession"],
        "form": row["form"],
        "key_form": row["key_form"],
        "is_amendment": row["is_amendment"],
        "period_end": row["period_end"],
        "filed_at": row["filed_at"],
        "fiscal_year": row["fiscal_year"],
        "fiscal_period": row["fiscal_period"],
    }


def _coverage_cell(quarter: str, rows: list[dict]) -> dict:
    """One (ticker, calendar quarter) cell.

    ``entries`` lists every filing that lands in the column, newest period end
    first, so two forms sharing a column (a 10-K and a 10-Q, or an original and
    its amendment at a different period end) are both visible.  The chosen row
    is the newest period end; an original beats its own amendment at the same
    period end, then the later filing and the accession break ties.
    """
    chosen = max(rows, key=lambda row: (
        row["period_end"], not row["is_amendment"], row["filed_at"], row["accession"]))
    entries = [_entry(row) for row in sorted(
        rows, key=lambda row: (row["period_end"], not row["is_amendment"],
                               row["filed_at"], row["accession"]), reverse=True)]
    amended_by = sorted({row["accession"] for row in rows
                         if row["accession"] != chosen["accession"]
                         and row["is_amendment"]})
    return {
        "quarter": quarter,
        "accession": chosen["accession"],
        "original_accession": chosen["original_accession"],
        "form": chosen["form"],
        "key_form": chosen["key_form"],
        "is_amendment": chosen["is_amendment"],
        "period_end": chosen["period_end"],
        "filed_at": chosen["filed_at"],
        "fiscal_year": chosen["fiscal_year"],
        "fiscal_period": chosen["fiscal_period"],
        "amended_by": amended_by,
        "entries": entries,
    }


def price_summary(price_db: Path | str, ticker: str, reference_date: str) -> dict:
    """Stored price range, newest price and D03-consistent expiry for one ticker."""
    bounds = store.price_bounds(price_db).get(ticker.upper())
    if bounds is None:
        return {"available": False, "start_date": None, "end_date": None, "rows": 0,
                "latest_date": None, "latest_close": None, "age_days": None,
                "stale": True, "reason": "no_data", "windows": []}
    try:
        connection = store.read_only(price_db)
    except FileNotFoundError:
        return {"available": False, "start_date": None, "end_date": None, "rows": 0,
                "latest_date": None, "latest_close": None, "age_days": None,
                "stale": True, "reason": "no_data", "windows": []}
    try:
        consistent = store.single_batch(connection, ticker.upper())
    finally:
        connection.close()
    if not consistent:
        return {"available": False, "start_date": None, "end_date": None, "rows": 0,
                "latest_date": None, "latest_close": None, "age_days": None,
                "stale": True, "reason": "mixed_batches",
                "windows": store.price_windows(price_db, ticker)}
    age = (date.fromisoformat(reference_date) - date.fromisoformat(bounds["end_date"])).days
    return {
        "available": True,
        "start_date": bounds["start_date"],
        "end_date": bounds["end_date"],
        "rows": bounds["rows"],
        "latest_date": bounds["latest_date"],
        "latest_close": display_text(bounds["latest_close"], "USD/share", name="close"),
        "age_days": age,
        "stale": age > PRICE_STALENESS_DAYS,
        "reason": None,
        "windows": store.price_windows(price_db, ticker),
    }


def price_overview(companies: dict[str, dict], reference: str) -> dict:
    """One header line about price freshness across every company.

    The date shown is the *oldest* of the companies' newest price dates, so a
    single lagging company is visible instead of hidden behind the freshest one.
    """
    dated = [company["price"]["end_date"] for company in companies.values()
             if company["price"].get("end_date")]
    missing = sum(1 for company in companies.values()
                  if not company["price"].get("end_date"))
    if not dated:
        return {"latest_price_date": None, "lag_days": None, "missing": missing,
                "stale_after_days": PRICE_STALENESS_DAYS}
    oldest = min(dated)
    return {
        "latest_price_date": oldest,
        "lag_days": (date.fromisoformat(reference) - date.fromisoformat(oldest)).days,
        "missing": missing,
        "stale_after_days": PRICE_STALENESS_DAYS,
    }


def trim_quarters(quarters: list[str], companies: dict[str, dict]) -> list[str]:
    """Drop leading columns no company has a filing in.

    Only the leading edge is trimmed: a gap between two reported quarters is
    real information about a company's filing history and stays visible.
    """
    filled = {key for company in companies.values() for key in company["periods"]}
    start = 0
    while start < len(quarters) and quarters[start] not in filled:
        start += 1
    return quarters[start:]


def card_count(card_db: Path | str, ticker: str) -> int:
    return len(store.card_rows(card_db, ticker=ticker))


def overview(*, fact_db: Path | str = DEFAULT_FACT_DB,
             price_db: Path | str = DEFAULT_PRICE_DB,
             card_db: Path | str = DEFAULT_ARCHIVE,
             reference_date: str | None = None) -> dict:
    """Company x calendar-quarter filing coverage plus price state and card counts."""
    reference = reference_date or date.today().isoformat()
    rows = _decorate(store.filing_rows(fact_db))
    bounds = store.price_bounds(price_db)
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["ticker"].upper(), []).append(row)

    companies: dict[str, dict] = {}
    for ticker in sorted(set(grouped) | {symbol for symbol in bounds
                                         if symbol != "SPY"}):
        ticker_rows = grouped.get(ticker, [])
        periods: dict[str, dict] = {}
        for row in ticker_rows:
            key = quarter_key(row["period_end"])
            periods.setdefault(key, []).append(row)
        companies[ticker] = {
            "ticker": ticker,
            "cik": ticker_rows[0]["cik"] if ticker_rows else None,
            "filing_count": len(ticker_rows),
            "latest_period_end": (max(row["period_end"] for row in ticker_rows)
                                  if ticker_rows else None),
            "periods": {key: _coverage_cell(key, value)
                        for key, value in periods.items()},
            "filings": sorted(ticker_rows,
                              key=lambda row: (row["period_end"], row["filed_at"]),
                              reverse=True),
            "price": price_summary(price_db, ticker, reference),
            "card_count": card_count(card_db, ticker),
        }
    quarters = trim_quarters(sorted({key for company in companies.values()
                                     for key in company["periods"]}), companies)
    return {
        "reference_date": reference,
        "stale_after_days": PRICE_STALENESS_DAYS,
        "price_command": "uv run prices-ingest",
        "quarters": quarters,
        "companies": companies,
        "price_summary": price_overview(companies, reference),
    }


def _metric_view(name: str, metric: dict) -> dict:
    """One fundamental metric, labelled and formatted by the shared display rules."""
    period_end = metric.get("period_end")
    return {
        "name": name,
        "label": display_label(name, period_end, category="fundamental"),
        "status": metric.get("status"),
        "display": (display_text(metric["value"], metric.get("unit") or "ratio", name=name)
                    if metric.get("status") == "ok" and metric.get("value") is not None
                    else None),
        "period_end": period_end,
        "fiscal_period": metric.get("fiscal_period"),
        "reason": metric.get("reason"),
        "fact_id": metric.get("fact_id"),
        "source_filings": metric.get("source_filings") or [],
        "accessions": metric.get("accessions") or [],
    }


def fundamentals(fact_db: Path | str, ticker: str, as_of: str) -> dict:
    """Latest computable Stage 3 metrics through the existing read-only tool."""
    envelope = get_fundamental_metrics(ticker, as_of=as_of, db_path=fact_db)
    payload = envelope.get("data") or {}
    metrics = payload.get("metrics") or {}
    ordered = [name for name in FUNDAMENTAL_METRICS if name in metrics]
    ordered += [name for name in sorted(metrics) if name not in FUNDAMENTAL_METRICS]
    return {
        "status": envelope["status"],
        "reason": envelope.get("reason"),
        "data_end_date": payload.get("data_end_date"),
        "metrics": [_metric_view(name, metrics[name]) for name in ordered],
    }


def filings(fact_db: Path | str, ticker: str) -> list[dict]:
    """Every stored filing for one ticker, newest period first."""
    rows = _decorate(store.filing_rows(fact_db, ticker=ticker))
    return sorted(rows, key=lambda row: (row["period_end"], row["filed_at"]), reverse=True)


def card_list(*, card_db: Path | str = DEFAULT_ARCHIVE, ticker: str | None = None,
              horizon: str | None = None) -> list[dict]:
    """Archived cards with the metadata the list page shows; never a model call.

    Timestamps are formatted here and the two long version strings are reduced to
    one short mark plus an "旧规则" flag, so the list page never renders a
    timestamp or abbreviates a version itself.
    """
    from thesis_tracker.decision.agent import PROMPT_VERSION
    from thesis_tracker.decision.core import VALIDATOR_VERSION
    from thesis_tracker.decision.evidence import HORIZON_LABELS

    reverse = {label: key for key, label in HORIZON_LABELS.items()}
    items = []
    for row in store.card_rows(card_db, ticker=ticker):
        card = _card_json(row)
        if horizon is not None and card.get("horizon") != HORIZON_LABELS.get(horizon):
            continue
        current = display.is_current_rules(row["prompt_version"], row["validator_version"],
                                          current_validator=VALIDATOR_VERSION,
                                          current_prompt=PROMPT_VERSION)
        items.append({
            "card_id": row["card_id"],
            "card_id_short": row["card_id"][:8],
            "ticker": row["ticker"],
            "as_of": row["as_of"],
            "created_at": display.format_timestamp(row["created_at"]),
            "version_mark": display.version_mark(row["prompt_version"],
                                                 row["validator_version"]),
            "rules_current": current,
            "rules_label": None if current else "旧规则",
            "creation_price": display_text(card.get("creation_price"), "USD/share",
                                           name="close"),
            "horizon": card.get("horizon"),
            "horizon_key": reverse.get(card.get("horizon")),
            "bias": card.get("bias"),
            "action": card.get("action"),
            "confidence": card.get("confidence"),
            "confidence_calibration": card.get("confidence_calibration"),
            "validator_version": row["validator_version"],
            "prompt_version": row["prompt_version"],
            "requested_model": row["requested_model"],
            "returned_model": row["returned_model"],
        })
    return items


def _number(value) -> Decimal | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def price_band(card: dict) -> dict | None:
    """Marker positions as 0-100 percentages, ordered by price.

    The frontend only places markers; every price and every position is computed
    here.  Only the three priced actions (买入 / 分批 / 持有) have a band: any
    other action, or an action whose prices are all missing, returns None and the
    page writes "此动作没有价位" instead of drawing an axis.
    """
    if card.get("action") not in {"买入", "分批", "持有"}:
        return None
    # Each marker carries a shape as well as a position: the band must survive
    # greyscale, so the closing price is a diamond while the levels are ticks.
    points: list[tuple[str, str, str, Decimal]] = []
    entry = card.get("entry_range")
    if isinstance(entry, list) and len(entry) == 2:
        low, high = _number(entry[0]), _number(entry[1])
        if low is not None and high is not None:
            points.append(("entry_low", "买点下沿", "entry", low))
            points.append(("entry_high", "买点上沿", "entry", high))
    for key, label, shape in (("stop_loss", "止损", "stop"),
                              ("creation_price", "收盘价", "close"),
                              ("target_price", "目标", "target")):
        value = _number(card.get(key))
        if value is not None:
            points.append((key, label, shape, value))
    if len(points) < 2:
        return None
    ordered = sorted(points, key=lambda item: (item[3], item[0]))
    low = min(item[3] for item in ordered)
    high = max(item[3] for item in ordered)
    span = high - low
    markers = []
    for key, label, shape, value in ordered:
        position = Decimal(0) if span == 0 else (value - low) / span * 100
        rounded = round(float(position), 2)
        shown = display_text(value, "USD/share", name="close")
        markers.append({
            "key": key,
            "label": label,
            "shape": shape,
            "value": shown,
            # Both the 0-100 percentage and the 0-1 fraction come from here, so
            # the page only formats them into a CSS length.
            "position": rounded,
            "fraction": round(rounded / 100, 4),
            # Ready to concatenate into a CSS length: no arithmetic on the page.
            "percent": f"{Decimal(str(rounded)):.2f}",
            # Which stacked label row this marker's text goes on.  Prices can sit
            # a fraction of a percent apart, so the row is decided here where the
            # axis width and the label widths are both known.
            "label_row": 0,
        })
    _place_labels(markers)
    return {
        "markers": markers,
        # The shapes and what they mean, so the legend is not written twice.
        "legend": [
            {"shape": "stop", "label": "止损"},
            {"shape": "range", "label": "买点区间"},
            {"shape": "close", "label": "收盘价"},
            {"shape": "target", "label": "目标"},
        ],
        # How much vertical room the stacked labels need; the page sizes the
        # plot from this instead of guessing.
        "label_rows": max((item["label_row"] for item in markers), default=0) + 1,
        "label_row_px": BAND_ROW_PX,
        "block_px": BAND_BLOCK_PX,
        "tick_px": BAND_TICK_PX,
        "tail_px": BAND_TAIL_PX,
        # Labels sit on both sides of the axis, so each side gets the full depth
        # (tick gap + one label block + that side's deepest row) and the axis
        # runs through the middle.
        "plot_height_px": 2 * _band_depth_px(markers),
        "axis_from_top_px": _band_depth_px(markers),
        "entry_low": display_text(_number(entry[0]), "USD/share", name="close")
        if isinstance(entry, list) and len(entry) == 2 else None,
        "entry_high": display_text(_number(entry[1]), "USD/share", name="close")
        if isinstance(entry, list) and len(entry) == 2 else None,
        # The buy range as prepared CSS numbers: the page concatenates them and
        # does no arithmetic of its own.
        # The axis inset and column width the placement above assumed; the CSS
        # uses both so a 0% marker lands exactly where its label was placed.
        "inset_px": round(band_inset_px(markers), 2),
        "column_px": int(BAND_COLUMN_PX),
        # The buy range as 0-1 fractions of the inset axis, which is the unit the
        # CSS calc() that mirrors the inset expects.
        "range_left_fraction": next((item["fraction"] for item in markers
                                     if item["key"] == "entry_low"), None),
        "range_width_fraction": (
            round(next((item["fraction"] for item in markers
                        if item["key"] == "entry_high"), Decimal(0))
                  - next((item["fraction"] for item in markers
                          if item["key"] == "entry_low"), Decimal(0)), 4)
            if any(item["key"] == "entry_low" for item in markers) else None),
    }


def _card_json(row: dict) -> dict:
    import json

    return json.loads(row["card_json"])


def source_label(source: dict | None) -> str:
    """Short human label for where a fact came from."""
    source = source or {}
    if source.get("provider") == "decision_archive":
        # A card's own judgment field: not a company fact, and it must say so
        # wherever it appears rather than reading like market data.
        return SOURCE_LABELS["decision_archive"]
    if source.get("formula"):
        return SOURCE_LABELS["derived"]
    provider = source.get("provider")
    if provider in SOURCE_LABELS:
        return SOURCE_LABELS[provider]
    return provider or SOURCE_LABELS["derived"]


def short_fact_id(fact_id: str) -> str:
    """The eight leading characters, which is what the card shows."""
    return str(fact_id)[:8]


def group_facts(facts: list[dict]) -> list[dict]:
    """Group evidence rows by where each fact came from."""
    grouped: dict[str, list[dict]] = {}
    for fact in facts:
        grouped.setdefault(fact.get("category") or "market", []).append(fact)
    return [{"key": key, "label": FACT_GROUP_LABELS[key], "facts": grouped[key],
             "count": len(grouped[key])}
            for key in FACT_GROUP_ORDER if key in grouped]


def fact_rows(facts: list[dict]) -> list[dict]:
    """Evidence rows as the pages show them.

    This is the one place a fact becomes a label plus a displayed value, so the
    card page and the chat page can never disagree about how a number reads.
    """
    from thesis_tracker.decision.evidence import fact_category

    occurrences: dict[str, int] = {}
    for item in facts:
        occurrences[item["name"]] = occurrences.get(item["name"], 0) + 1
    rows = []
    for item in facts:
        source = item.get("source") or {}
        category = item.get("category") or fact_category(item)
        rows.append({
            "fact_id": item["fact_id"],
            "fact_id_short": short_fact_id(item["fact_id"]),
            "name": item["name"],
            # A card's judgment fields carry their own label because their name
            # is a field key ("action"), not a metric name.  Everything else is
            # resolved here and never trusted from the stored copy: an id may
            # outlive a label, and a raw "close" must never reach the page.
            "label": (item["label"] if category == "card" and item.get("label")
                      else display_label(
                          item["name"], item.get("date_or_period"),
                          multiple=occurrences[item["name"]] > 1,
                          category=None if category == "card" else category)),
            # Numeric values are always recomputed, never taken from the stored
            # copy: the card renderer recomputes them too, and the two must
            # produce identical text for the page to match the archived card byte
            # for byte.  Text-valued facts carry their own already-formatted
            # display, because there is no number to format.
            "display": (item.get("display") if item.get("unit") in TEXT_UNITS
                        else display_text(item["value"], item["unit"],
                                          name=item["name"])),
            "value": str(item["value"]),
            "unit": item["unit"],
            "date_or_period": item.get("date_or_period"),
            "category": category,
            "source_label": source_label(source),
            "provider": source.get("provider"),
            "formula": source.get("formula"),
            "source_fact_ids": source.get("source_fact_ids") or [],
            "card_id": item.get("card_id"),
            "annotation": item.get("annotation"),
        })
    return rows


def _atr_value(index: dict[str, dict] | None, facts: list[dict]) -> Decimal | None:
    """ATR for the hint line.

    The snapshot's fact index is the authority: a card only carries the facts it
    cites, and ATR is usually not one of them, so reading the card's list alone
    would silently disable the hint.
    """
    for item in (index or {}).values():
        if item.get("name") == "atr_14" and item.get("value") is not None:
            return _number(item["value"])
    for fact in facts:
        if fact.get("name") == "atr_14":
            return _number(fact.get("value"))
    return None


def advice_hints(card: dict, facts: list[dict], *, index: dict[str, dict] | None = None,
                 number=_number) -> list[str]:
    """Advisory remarks shown beside the numbers, never part of validation.

    Both thresholds are heuristics rather than facts about the security, so the
    wording says what was measured and leaves the judgement to the reader.
    """
    if card.get("action") not in {"买入", "分批", "持有"}:
        return []
    entry = None
    bounds = card.get("entry_range")
    if card.get("action") in {"买入", "分批"} and isinstance(bounds, list) and len(bounds) == 2:
        low, high = number(bounds[0]), number(bounds[1])
        if low is not None and high is not None:
            entry = (low + high) / 2
    elif card.get("action") == "持有":
        entry = number(card.get("creation_price"))
    if entry is None or entry == 0:
        return []
    stop = number(card.get("stop_loss"))
    target = number(card.get("target_price"))
    hints: list[str] = []
    if stop is not None and target is not None and entry != stop:
        reward_risk = (target - entry) / (entry - stop)
        if reward_risk < Decimal(str(MIN_REWARD_RISK)):
            hints.append(f"盈亏比偏低：{reward_risk:.1f} : 1，"
                         f"低于经验阈值 {MIN_REWARD_RISK:g}")
    atr = _atr_value(index, facts)
    if stop is not None and atr is not None and atr > 0:
        multiple = (entry - stop) / atr
        if multiple < Decimal(str(MIN_STOP_ATR_MULTIPLE)):
            hints.append(f"止损距离只有 {multiple:.1f} 倍 ATR，"
                         "正常波动就可能触发；这是经验阈值，还没有用到期结果检验过")
    return hints


def strip_language_suffix(heading: str) -> str:
    """Drop an implementation-language suffix from a reused heading.

    The card renderer labels its arithmetic section "自动计算（Python）"; which
    language computes it is not something a reader needs, so the page shows the
    same heading without the parenthetical.
    """
    return re.sub(r"（[^）]*）\s*$", "", heading).strip()


def adjust_auto_computed(items: list[dict]) -> list[dict]:
    """Shorten two auto-computed lines that repeat their own label.

    ``core.auto_computed`` builds the text; the card page shows ``距52周高点``
    above it, so the line only needs the measurement.  Only the rendered text
    changes; the archived card and the exact values are untouched.
    """
    adjusted = []
    for item in items:
        text = item.get("text", "")
        if item.get("label") == "距52周高点":
            match = re.search(r"(低于|高于) 52 周高点 (.+)$", text)
            if match:
                text = f"{match.group(1)} {match.group(2)}"
        elif item.get("label") == "盈亏比" and re.fullmatch(r"\d+(?:\.\d+)?", text.strip()):
            text = f"{text.strip()} : 1"
        adjusted.append({**item, "text": text})
    return adjusted


# The axis spans the reading column, whose width app.css fixes at 640px, inset
# by 4.5em on each side (the CSS mirrors these two numbers in --band-inset and
# the marker calc()); 12px is a deliberately conservative advance width for one
# CJK character.
BAND_COLUMN_PX = 640.0
# Clear space kept between two label boxes before they may share a row.
BAND_GAP_PX = 6.0
# The axis is inset so that the label of a marker at 0% or 100% still fits
# inside the plot; the inset is derived from the labels actually present rather
# than hand-tuned, because it depends on the longest price string on the card.
BAND_INSET_PAD_PX = 10.0
BAND_CHAR_PX = 12.0
# Labels alternate above and below the axis, so only every second row shares a
# side and the geometry is symmetric.
BAND_SIDES = ("above", "below")
# A label block is two lines of 12px text.  app.css pins the line box to 16px
# (--band-line) precisely so this arithmetic is exact: two 16px lines are 32px,
# and a 6px gap makes overlap between stacked rows impossible.
BAND_BLOCK_PX = 32
BAND_LINE_PX = 16
BAND_ROW_PX = BAND_BLOCK_PX + 6
# The tick sits 22px above the plot's bottom edge; both mirror --band-tick in
# app.css.
BAND_TICK_PX = 22
BAND_TAIL_PX = 8


def _band_depth_px(markers: list[dict]) -> int:
    """Room one side of the axis needs: tick gap, one label block, deepest row."""
    deepest = 0
    for side in BAND_SIDES:
        offsets = [item["label_offset_px"] for item in markers
                   if item.get("label_side") == side]
        if offsets:
            deepest = max(deepest, max(offsets))
    return BAND_TICK_PX + BAND_BLOCK_PX + (deepest if markers else 0) + BAND_TAIL_PX


def label_width_px(marker: dict) -> float:
    """A conservative rendered width for one marker's label block."""
    return max(len(marker["label"]), len(marker["value"])) * BAND_CHAR_PX


def band_inset_px(markers: list[dict]) -> float:
    """Half the widest label plus a margin: why a 0% marker still fits."""
    if not markers:
        return 0.0
    return max(label_width_px(marker) for marker in markers) / 2 + BAND_INSET_PAD_PX


def _place_labels(markers: list[dict]) -> None:
    """Place band labels so none overlaps another, alternating above and below.

    Labels whose horizontal spans would collide are pushed to the next row on
    the same side, and successive rows alternate sides.  The geometry is decided
    here because only here are the axis width and the label widths both known;
    the page converts the row index into a CSS length.
    """
    inset = band_inset_px(markers)
    axis_px = max(1.0, BAND_COLUMN_PX - 2 * inset)
    placed: dict[tuple[str, int], list[tuple[float, float]]] = {}
    for index, marker in enumerate(markers):
        # Positions are measured along the inset axis, which is what the CSS
        # calc() does too.
        centre = inset + (marker["position"] / 100) * axis_px
        half = label_width_px(marker) / 2
        row = 0
        while True:
            side = BAND_SIDES[index % len(BAND_SIDES)] if row == 0 else (
                BAND_SIDES[(index + row) % len(BAND_SIDES)])
            occupied = placed.setdefault((side, row), [])
            if all(centre - half > right + BAND_GAP_PX
                   or centre + half < left - BAND_GAP_PX
                   for left, right in occupied):
                occupied.append((centre - half, centre + half))
                marker["label_row"] = row
                marker["label_side"] = side
                break
            row += 1
        marker["label_offset_px"] = marker["label_row"] * BAND_ROW_PX


def _card_json(row: dict) -> dict:
    import json

    return json.loads(row["card_json"])


def usage_average(card_db: Path | str = DEFAULT_ARCHIVE, *,
                  window: int = USAGE_WINDOW) -> dict:
    """Mean token usage of the most recent analyses; never any money amount.

    One analysis can archive more than one card (one per horizon), so the totals
    are summed per ``analysis_id`` before averaging, read from
    ``decision_model_calls`` through :func:`webapp.data.analysis_usage`.
    """
    rows = store.analysis_usage(card_db, window=window)
    if not rows:
        return {"analyses": 0, "input_tokens": None, "output_tokens": None,
                "cache_hit_tokens": None, "window": window}
    count = len(rows)
    return {
        "analyses": count,
        "window": window,
        "input_tokens": round(sum(row["input_tokens"] for row in rows) / count),
        "output_tokens": round(sum(row["output_tokens"] for row in rows) / count),
        "cache_hit_tokens": round(sum(row["cache_hit_tokens"] for row in rows) / count),
    }


def job_view(job: dict) -> dict:
    """A stored job with display strings attached.

    Timestamps become local minute strings, the parameter dictionary becomes a
    labelled Chinese line, and the progress events each gain a display time, so
    the task page renders text and never formats anything itself.
    """
    result = job.get("result")
    view = {
        **job,
        "created_at": display.format_timestamp(job.get("created_at")),
        "started_at": display.format_timestamp(job.get("started_at")),
        "finished_at": display.format_timestamp(job.get("finished_at")),
        "parameters": display.parameter_labels(job.get("parameters") or {}),
        "parameter_summary": display.parameter_summary(job.get("parameters") or {}),
        "progress": [{**event, "at_display": display.format_timestamp(event.get("at"))}
                     for event in job.get("progress") or []],
    }
    if isinstance(result, dict):
        view["result"] = {
            **result,
            "card_id_short": (result["card_id"][:8] if result.get("card_id") else None),
        }
    return view


def company_page(*, fact_db: Path | str = DEFAULT_FACT_DB,
                 price_db: Path | str = DEFAULT_PRICE_DB,
                 card_db: Path | str = DEFAULT_ARCHIVE,
                 ticker: str, as_of: str | None = None,
                 reference_date: str | None = None) -> dict:
    """Everything the company page shows for one ticker."""
    reference = reference_date or date.today().isoformat()
    cutoff = as_of or date.today().isoformat()
    rows = filings(fact_db, ticker)
    if not rows:
        raise KeyError(ticker)
    price = price_summary(price_db, ticker, reference)
    return {
        "ticker": ticker.upper(),
        "cik": rows[0]["cik"],
        "as_of": cutoff,
        "reference_date": reference,
        "stale_after_days": PRICE_STALENESS_DAYS,
        "price_command": "uv run prices-ingest",
        "filings": rows,
        "price": price,
        "fundamentals": fundamentals(fact_db, ticker, cutoff),
        "cards": card_list(card_db=card_db, ticker=ticker),
    }


def _segments(raw: str, index: dict[str, dict]) -> list[dict]:
    """Split a placeholder-bearing body into text and fact segments.

    Byte-for-byte reconstruction: concatenating ``value`` in order and rendering
    each fact with the same display function reproduces exactly what
    ``core.render_card`` prints for this field.
    """
    from thesis_tracker.decision.core import PLACEHOLDER

    segments: list[dict] = []
    cursor = 0
    for match in PLACEHOLDER.finditer(raw):
        if match.start() > cursor:
            segments.append({"type": "text", "value": raw[cursor:match.start()]})
        fact_id = match.group(1)
        item = index.get(fact_id)
        segments.append({"type": "fact", "fact_id": fact_id,
                         "display": None if item is None else display_text(
                             item["value"], item["unit"], name=item["name"])})
        cursor = match.end()
    if cursor < len(raw):
        segments.append({"type": "text", "value": raw[cursor:]})
    return segments


def _line(text: str) -> list[dict]:
    return [{"type": "text", "value": text}]


def card_detail(*, card_db: Path | str = DEFAULT_ARCHIVE, card_id: str) -> dict:
    """Structured card detail. Archived cards are never sent to a model again."""
    import json



    row = next((item for item in store.card_rows(card_db)
                if item["card_id"] == card_id), None)
    if row is None:
        raise KeyError(card_id)
    card = _card_json(row)
    snapshot = json.loads(row["snapshot_json"])
    index, conflicts = fact_index(snapshot)
    facts = fact_rows(card["facts"])

    invalidations = []
    for item in card["invalidations"]:
        threshold = _number(item.get("price"))
        verb = {"close_below": "跌破", "close_above": "站上"}.get(item.get("kind"),
                                                                  str(item.get("kind")))
        check = (f"收盘价{verb} {display_text(threshold, 'USD/share', name='close')}"
                 if threshold is not None else "未填阈值")
        invalidations.append({
            "kind": item.get("kind"),
            "price": None if threshold is None else display_text(
                threshold, "USD/share", name="close"),
            "machine_check": _line(check),
            "explanation": _segments(item.get("text") or "", index),
        })

    # Resolve which analysis run wrote this card, then show only that run's
    # drafts: attempts from other analyses of the same ticker are not this
    # card's history.
    analysis_id = _owning_analysis(_attempts_for(card_db, row), row)
    attempts = _attempts_for(card_db, row, analysis_id)

    return {
        "card_id": row["card_id"],
        "card_id_short": row["card_id"][:8],
        "ticker": row["ticker"],
        "as_of": row["as_of"],
        "created_at": display.format_timestamp(row["created_at"]),
        "horizon": card.get("horizon"),
        "bias": card.get("bias"),
        "action": card.get("action"),
        "confidence": card.get("confidence"),
        "confidence_calibration": card.get("confidence_calibration"),
        "creation_price": display_text(card.get("creation_price"), "USD/share",
                                       name="close"),
        "price_data_end": next((item.get("date_or_period") for item in card["facts"]
                                if item["name"] == "close"), None),
        "entry_range": None if card.get("entry_range") is None else [
            display_text(_number(card["entry_range"][0]), "USD/share", name="close"),
            display_text(_number(card["entry_range"][1]), "USD/share", name="close"),
        ],
        "stop_loss": display_text(_number(card.get("stop_loss")), "USD/share",
                                  name="close"),
        "target_price": display_text(_number(card.get("target_price")), "USD/share",
                                     name="close"),
        "price_band": price_band(card),
        "facts": facts,
        "stop_rationale": _segments(card.get("stop_rationale") or "", index),
        "target_rationale": _segments(card.get("target_rationale") or "", index),
        "reasons": [_segments(item.get("text") or "", index)
                    for item in card.get("reasons") or []],
        "invalidations": invalidations,
        "auto_computed": adjust_auto_computed(auto_computed(card, snapshot)),
        "auto_computed_heading": strip_language_suffix(AUTO_COMPUTED_HEADING),
        "hints": advice_hints(card, facts, index=index),
        "fact_groups": group_facts(facts),
        "gaps": [{"name": item["name"],
                  "label": display_label(item["name"], category="fundamental"),
                  "status": item["status"],
                  "reason": item.get("reason")} for item in card.get("gaps") or []],
        "disclaimer": card.get("disclaimer"),
        "attempts": attempts,
        "analysis_id": analysis_id,
        "versions": {
            "validator_version": row["validator_version"],
            "prompt_version": row["prompt_version"],
            "requested_model": row["requested_model"],
            "returned_model": row["returned_model"],
            "fingerprint": row["fingerprint"],
            "snapshot_sha256": row["snapshot_sha256"],
            "validation_result": json.loads(row["validation_json"]),
            "fact_conflicts": conflicts,
        },
        "usage": {"input_tokens": row["input_tokens"],
                  "output_tokens": row["output_tokens"],
                  "cache_hit_tokens": row["cache_hit_tokens"]},
    }


def _owning_analysis(attempts: list[dict], row: dict) -> str | None:
    """Which analysis run produced this card.

    The archive stores no link from a card to its analysis, but the loop appends
    the passing attempt immediately before it writes the card, so the newest
    attempt at or before the card's creation time belongs to it.
    """
    candidates = [item for item in attempts
                  if item["created_at"] <= row["created_at"] and item["passed"]]
    return max(candidates, key=lambda item: item["created_at"])["analysis_id"] \
        if candidates else None


def _attempts_for(card_db: Path | str, row: dict, analysis_id: str | None = None
                  ) -> list[dict]:
    """Draft attempts of one analysis run, including every rejected draft.

    Rejected drafts are shown with their rule number and plain-Chinese message.
    Only the card's own run is listed: showing every attempt ever made for a
    ticker would mix unrelated analyses into one card's history.
    """
    import json

    items = []
    for attempt in store.attempt_rows(card_db, ticker=row["ticker"]):
        if attempt["as_of"] != row["as_of"]:
            continue
        if analysis_id is not None and attempt["analysis_id"] != analysis_id:
            continue
        if analysis_id is None and not attempt["passed"]:
            continue
        violations = json.loads(attempt["violations_json"] or "[]")
        items.append({
            "attempt_no": attempt["attempt_no"],
            "analysis_id": attempt["analysis_id"],
            "created_at": display.format_timestamp(attempt["created_at"]),
            "requested_horizon": attempt["requested_horizon"],
            "passed": bool(attempt["passed"]),
            "violations": violations,
            "headline": ("通过校验并存档" if attempt["passed"]
                         else f"被拒绝：{len(violations)} 条规则违规"),
        })
    # Reading order: first draft first.
    items.sort(key=lambda item: (item["created_at"], item["attempt_no"]))
    return items
