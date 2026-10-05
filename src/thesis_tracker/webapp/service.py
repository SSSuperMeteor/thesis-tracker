"""Read-only view models for the local web app.

Every number and label a page shows is produced here by reusing the existing
display functions (``decision.evidence.display_value`` / ``display_text`` /
``display_label``) and the existing read-only tools.  Nothing in this module
recomputes a financial value, and nothing here writes to any database.
"""

from __future__ import annotations

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

# The eight deterministic Stage 3 metrics, in the order their own module lists
# them.  Labels and units come from decision.evidence, never from this file.
FUNDAMENTAL_METRICS = (
    "gross_margin_trend", "accruals_ratio", "cash_conversion",
    "ar_growth_vs_rev_growth", "net_buyback_yield", "diluted_share_count_yoy",
    "interest_coverage", "net_debt_to_ebitda",
)

USAGE_WINDOW = 5


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
    quarters = sorted({key for company in companies.values()
                       for key in company["periods"]})
    return {
        "reference_date": reference,
        "stale_after_days": PRICE_STALENESS_DAYS,
        "price_command": "uv run prices-ingest",
        "quarters": quarters,
        "companies": companies,
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
    """Archived cards with the metadata the list page shows; never a model call."""
    from thesis_tracker.decision.evidence import HORIZON_LABELS

    reverse = {label: key for key, label in HORIZON_LABELS.items()}
    items = []
    for row in store.card_rows(card_db, ticker=ticker):
        card = _card_json(row)
        if horizon is not None and card.get("horizon") != HORIZON_LABELS.get(horizon):
            continue
        items.append({
            "card_id": row["card_id"],
            "ticker": row["ticker"],
            "as_of": row["as_of"],
            "created_at": row["created_at"],
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
    points: list[tuple[str, str, Decimal]] = []
    entry = card.get("entry_range")
    if isinstance(entry, list) and len(entry) == 2:
        low, high = _number(entry[0]), _number(entry[1])
        if low is not None and high is not None:
            points.append(("entry_low", "买点下沿", low))
            points.append(("entry_high", "买点上沿", high))
    for key, label in (("stop_loss", "止损"), ("creation_price", "收盘价"),
                       ("target_price", "目标")):
        value = _number(card.get(key))
        if value is not None:
            points.append((key, label, value))
    if len(points) < 2:
        return None
    ordered = sorted(points, key=lambda item: (item[2], item[0]))
    low = min(item[2] for item in ordered)
    high = max(item[2] for item in ordered)
    span = high - low
    markers = []
    for key, label, value in ordered:
        position = Decimal(0) if span == 0 else (value - low) / span * 100
        rounded = round(float(position), 2)
        shown = display_text(value, "USD/share", name="close")
        markers.append({
            "key": key,
            "label": label,
            "value": shown,
            # Both the 0-100 percentage and the 0-1 fraction come from here, so
            # the page only formats them into a CSS length.
            "position": rounded,
            "fraction": round(rounded / 100, 4),
            # Which stacked label row this marker's text goes on.  Prices can sit
            # a fraction of a percent apart, so the row is decided here where the
            # axis width and the label widths are both known.
            "label_row": 0,
        })
    _place_labels(markers)
    return {
        "markers": markers,
        # How much vertical room the stacked labels need; the page sizes the
        # plot from this instead of guessing.
        "label_rows": max((item["label_row"] for item in markers), default=0) + 1,
        "label_row_px": BAND_ROW_PX,
        "block_px": BAND_BLOCK_PX,
        "tick_px": BAND_TICK_PX,
        "tail_px": BAND_TAIL_PX,
        "plot_height_px": (BAND_TICK_PX + BAND_BLOCK_PX
                           + max((item["label_offset_px"] for item in markers), default=0)
                           + BAND_TAIL_PX),
        "entry_low": display_text(_number(entry[0]), "USD/share", name="close")
        if isinstance(entry, list) and len(entry) == 2 else None,
        "entry_high": display_text(_number(entry[1]), "USD/share", name="close")
        if isinstance(entry, list) and len(entry) == 2 else None,
    }


def _card_json(row: dict) -> dict:
    import json

    return json.loads(row["card_json"])


# The band's axis is 9em narrower than its column (see --band-inset in app.css),
# and 12px is a deliberately conservative advance width for one CJK character.
BAND_COLUMN_PX = 366.0
BAND_INSET_PX = 54.0
BAND_CHAR_PX = 12.0
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


def _place_labels(markers: list[dict]) -> None:
    """Stack colliding band labels onto separate rows, left to right.

    The band's own axis is 9em narrower than the column, so a marker at 0% or
    100% still has room for its label; the numbers here are the ones the CSS
    formula uses (see ``markerOffset`` in app.js and ``--band-inset``).
    """
    axis_px = max(1.0, BAND_COLUMN_PX - 2 * BAND_INSET_PX)
    placed: dict[int, list[tuple[float, float]]] = {}
    for marker in markers:
        centre = (marker["position"] / 100) * axis_px
        half = max(len(marker["label"]), len(marker["value"])) * BAND_CHAR_PX / 2
        row = 0
        while True:
            occupied = placed.setdefault(row, [])
            if all(centre - half > right + 6 or centre + half < left - 6
                   for left, right in occupied):
                occupied.append((centre - half, centre + half))
                marker["label_row"] = row
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
    occurrences: dict[str, int] = {}
    for item in card["facts"]:
        occurrences[item["name"]] = occurrences.get(item["name"], 0) + 1

    from thesis_tracker.decision.evidence import fact_category

    facts = []
    for item in card["facts"]:
        source = item.get("source") or {}
        facts.append({
            "fact_id": item["fact_id"],
            "name": item["name"],
            "label": display_label(item["name"], item.get("date_or_period"),
                                   multiple=occurrences[item["name"]] > 1,
                                   category=fact_category(item)),
            "display": display_text(item["value"], item["unit"], name=item["name"]),
            "value": str(item["value"]),
            "unit": item["unit"],
            "date_or_period": item.get("date_or_period"),
            "provider": source.get("provider"),
            "formula": source.get("formula"),
            "source_fact_ids": source.get("source_fact_ids") or [],
        })

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
        "ticker": row["ticker"],
        "as_of": row["as_of"],
        "created_at": row["created_at"],
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
        "auto_computed": auto_computed(card, snapshot),
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
            "created_at": attempt["created_at"],
            "requested_horizon": attempt["requested_horizon"],
            "passed": bool(attempt["passed"]),
            "violations": violations,
            "headline": ("通过校验并存档" if attempt["passed"]
                         else f"被拒绝：{len(violations)} 条规则违规"),
        })
    # Reading order: first draft first.
    items.sort(key=lambda item: (item["created_at"], item["attempt_no"]))
    return items
