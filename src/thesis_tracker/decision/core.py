"""Capture tool evidence, build and validate cards, and append immutable archives."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from thesis_tracker.financial.pit_store import DEFAULT_FACT_DB
from thesis_tracker.financial.tool import get_fundamental_metrics
from thesis_tracker.indicator_tool import get_indicators
from thesis_tracker.prices import DEFAULT_DB as DEFAULT_PRICE_DB
from thesis_tracker.prices import get_price_history

DISCLAIMER = "本卡为 AI 研究输出，不构成持牌投资建议。"
VALIDATOR_VERSION = "decision-validator-3"
# D03: the newest stored price must be within this many calendar days of as_of.
# The local web app reads this same constant to flag stale prices, so the banner
# and the validator can never disagree about what "stale" means.
PRICE_STALENESS_DAYS = 5
# Rules added after a given archived validator version.  Replaying an old card
# with its own version skips exactly these; D11 is a pure text rule with no
# schema dependency and stays version-independent.
RULES_NOT_IN = {
    "decision-validator-1": frozenset({"D12", "D13", "D14", "D15"}),
    "decision-validator-2": frozenset({"D14", "D15"}),
}
DEFAULT_ARCHIVE = Path("data/decisions/cards.db")
ALLOWED_ACTIONS = {
    "看多": {"买入", "分批", "持有"},
    "中性": {"分批", "持有", "回避", "观望"},
    "看空": {"减仓", "回避"},
}
# D11 vocabulary: every action word belongs to exactly one action category.
ACTION_WORDS = {
    "买入": ("买入",),
    "分批": ("分批", "分步", "加仓"),
    "持有": ("持有",),
    "减仓": ("减仓", "卖出", "清仓", "离场"),
    "回避": ("回避",),
    "观望": ("观望",),
}
# An action word starting within NEGATION_WINDOW characters after one of these
# is a negated mention and is allowed ("不宜分批", "分批而非一次性买入" is not).
NEGATIONS = ("而非", "不宜", "避免", "不要", "勿")
NEGATION_WINDOW = 6
RATIONALE_FIELDS = ("stop_rationale", "target_rationale")
PLACEHOLDER = re.compile(r"\{fact:([^{}]+)\}")
NUMBER = re.compile(r"(?<![A-Za-z0-9_.])[-+]?\d+(?:\.\d+)?(?![A-Za-z0-9_.])")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fact(item: dict, ticker: str, date_value: str | None, source: dict | None, name: str) -> dict:
    from thesis_tracker.decision.evidence import display_value

    return {"fact_id": item["fact_id"], "name": name, "value": item["value"],
            "display": display_value(item["value"], item["unit"], name=name),
            "unit": item["unit"], "date_or_period": date_value, "ticker": ticker,
            "source": source}


def _negated(text: str, start: int) -> bool:
    """True when the action word at ``start`` follows one of the negators."""
    for negator in NEGATIONS:
        cursor = text.find(negator)
        while cursor != -1:
            end = cursor + len(negator)
            if end <= start <= end + NEGATION_WINDOW:
                return True
            cursor = text.find(negator, end)
    return False


def foreign_action_words(text: str, action: str | None) -> list[str]:
    """Action words in ``text`` that belong to a different action category."""
    own = set(ACTION_WORDS.get(action or "", ()))
    found: list[str] = []
    for category, words in ACTION_WORDS.items():
        if category == action:
            continue
        for word in words:
            if word in own or word in found:
                continue
            cursor = text.find(word)
            while cursor != -1:
                if not _negated(text, cursor):
                    found.append(word)
                    break
                cursor = text.find(word, cursor + len(word))
    return found


def fact_index(snapshot: dict) -> tuple[dict[str, dict], list[str]]:
    """Rebuild from envelopes; never trust a supplied index or a card's values."""
    index: dict[str, dict] = {}
    conflicts: list[str] = []
    for call in snapshot.get("calls", []):
        envelope = call.get("envelope", {})
        data = envelope.get("data") or {}
        tool = call.get("tool")
        ticker = str(call.get("args", {}).get("ticker", call.get("args", {}).get("symbol", ""))).upper()
        source = envelope.get("source")
        found = []
        if tool in {"get_price_history", "get_price_history_page"} and envelope.get("status") == "ok":
            latest = data.get("latest_close")
            if tool == "get_price_history" and isinstance(latest, dict) and latest.get("value") is not None:
                found.append(_fact({"fact_id": envelope["fact_id"], **latest}, ticker,
                                   data.get("data_end_date"), source, "close"))
            for row in data.get("rows", []):
                close = row.get("close")
                if isinstance(close, dict) and close.get("value") is not None:
                    found.append(_fact({"fact_id": row["fact_id"], **close}, ticker,
                                       row.get("date"), source, "close"))
        elif tool in {"get_indicators", "get_indicators_history"} and envelope.get("status") == "ok":
            records = ([data.get("latest")] if tool == "get_indicators" else []) + data.get("rows", [])
            for record in records:
                if not isinstance(record, dict):
                    continue
                for name, metric in record.get("values", {}).items():
                    if metric.get("fact_id") and metric.get("value") is not None:
                        found.append(_fact(metric, ticker, metric.get("date"), source, name))
        elif tool in {"get_fundamental_metrics", "get_fundamental_metrics_history"}:
            groups = ([data.get("metrics", {})] if tool == "get_fundamental_metrics" else []) + [
                row.get("metrics", {}) for row in data.get("rows", [])]
            for metrics in groups:
                for name, metric in metrics.items():
                    if metric.get("fact_id") and metric.get("status") == "ok" and metric.get("value") is not None:
                        found.append(_fact(metric, ticker, metric.get("period_end"), source, name))
        for item in found:
            existing = index.get(item["fact_id"])
            if existing is not None and existing != item:
                conflicts.append(item["fact_id"])
            else:
                index[item["fact_id"]] = item
    if "derived_facts" in snapshot:
        from thesis_tracker.decision.evidence import recompute_derived

        expected = recompute_derived(snapshot)
        if snapshot["derived_facts"] != expected:
            conflicts.append("derived_facts")
        for item in expected:
            if item["fact_id"] in index:
                conflicts.append(item["fact_id"])
            else:
                index[item["fact_id"]] = item
    return index, conflicts


def data_gaps(snapshot: dict) -> list[dict]:
    """List unavailable/null indicators and metrics from every captured page."""
    gaps: dict[str, dict] = {}
    for call in snapshot.get("calls", []):
        tool = call.get("tool")
        envelope = call.get("envelope", {})
        data = envelope.get("data") or {}
        ticker = str(call.get("args", {}).get("ticker", call.get("args", {}).get("symbol", ""))).upper()
        if envelope.get("status") != "ok":
            key = f"{tool}|{ticker}|{envelope.get('as_of')}|tool"
            gaps[key] = {"id": key, "name": tool, "status": envelope.get("status"),
                         "reason": envelope.get("reason")}
        if tool in {"get_indicators", "get_indicators_history"}:
            records = ([data.get("latest")] if tool == "get_indicators" else []) + data.get("rows", [])
            groups = [(r.get("date"), r.get("values", {})) for r in records if isinstance(r, dict)]
        elif tool == "get_fundamental_metrics":
            groups = [("latest", data.get("metrics", {}))]
            groups += [(r.get("period_end"), r.get("metrics", {})) for r in data.get("rows", [])]
        else:
            groups = []
        for period, metrics in groups:
            for name, metric in metrics.items():
                if metric.get("value") is None or metric.get("status") in {"unavailable", "not_applicable"}:
                    identity = metric.get("period_end") or metric.get("date") or period
                    key = f"{tool}|{ticker}|{identity}|{name}"
                    gaps[key] = {"id": key, "name": name,
                                 "status": metric.get("status") or "unavailable",
                                 "reason": metric.get("reason")}
    return [gaps[key] for key in sorted(gaps)]


def capture_snapshot(ticker: str, as_of: str, *,
                     price_db: Path | str = DEFAULT_PRICE_DB,
                     fact_db: Path | str = DEFAULT_FACT_DB) -> dict:
    """Call the three local read-only tools with their defaults.

    The database paths default to the same module constants the tools use, so
    omitting them is exactly the previous behaviour; passing them lets a caller
    (the local web app's tests) point the same read-only path at another store.
    """
    ticker = ticker.upper()
    date.fromisoformat(as_of)
    calls = []
    for name, function, key in (
        ("get_price_history", get_price_history, "symbol"),
        ("get_indicators", get_indicators, "symbol"),
        ("get_fundamental_metrics", get_fundamental_metrics, "ticker"),
    ):
        args = {key: ticker, "as_of": as_of}
        extra = {"db_path": fact_db if name == "get_fundamental_metrics" else price_db}
        calls.append({"tool": name, "args": args,
                      "envelope": function(ticker, as_of=as_of, **extra)})
    snapshot = {"ticker": ticker, "as_of": as_of, "calls": calls}
    snapshot["fact_index"] = fact_index(snapshot)[0]
    return snapshot


def _creation_price(snapshot: dict) -> Any:
    for call in snapshot.get("calls", []):
        if (call.get("tool") == "get_price_history" and
                call.get("args", {}).get("symbol") == snapshot.get("ticker")):
            return (((call.get("envelope", {}).get("data") or {}).get("latest_close") or {})
                    .get("value"))
    return None


def build_card(draft: dict, snapshot: dict) -> dict:
    """Fill facts, gaps and disclaimer in Python; judgment fields come from the draft."""
    index, _ = fact_index(snapshot)
    card = {key: value for key, value in draft.items() if key not in {
        "facts", "gaps", "disclaimer", "confidence_calibration", "creation_price", "evidence_windows"}}
    card["creation_price"] = _creation_price(snapshot)
    card["facts"] = [index[fact_id] for fact_id in card.get("fact_ids", []) if fact_id in index]
    card["gaps"] = data_gaps(snapshot)
    card["disclaimer"] = DISCLAIMER
    card["confidence_calibration"] = "未校准"
    card["evidence_windows"] = snapshot.get("evidence_windows", [])
    return card


def _number(value: Any) -> Decimal | None:
    try:
        if isinstance(value, bool) or value is None:
            return None
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _free_numbers(text: str) -> list[Decimal]:
    text = PLACEHOLDER.sub("", text)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", text)
    text = re.sub(r"\b(?:20\d{2}-)?Q[1-4]\b", "", text, flags=re.I)
    text = re.sub(r"\d+\s*个(?:季度|月|交易日|工作日|年度|年)", "", text)
    return [number for match in NUMBER.finditer(text)
            if (number := _number(match.group())) is not None]


# Chat validation (webapp/chat) must apply the card validator's naked-number
# rule and action-word list rather than keeping a second copy that could drift.
# These two module-level aliases expose the same objects; no behaviour in this
# module changes because of them, and each rule stays defined in one place.
free_numbers = _free_numbers
chat_action_words = ACTION_WORDS


def validate_card(card: dict, snapshot: dict, *,
                  version: str = VALIDATOR_VERSION) -> list[dict]:
    """Return every mechanical violation. A nonempty result forbids publication."""
    errors: list[dict] = []
    skip = RULES_NOT_IN.get(version, frozenset())

    def add(rule: str, location: str, message: str) -> None:
        if rule in skip:
            return
        errors.append({"rule": rule, "location": location, "message": message})

    ticker, as_of = snapshot.get("ticker"), snapshot.get("as_of")
    if card.get("ticker") != ticker or card.get("as_of") != as_of:
        add("D01", "ticker/as_of", "卡片的标的或截至日与证据快照不一致。")
    if card.get("creation_price") != _creation_price(snapshot):
        add("D01", "creation_price", "创建时价格必须由 Python 从快照的原始收盘价填入。")
    index, conflicts = fact_index(snapshot)
    for fact_id in conflicts:
        add("D01", f"snapshot.fact_index.{fact_id}", "同一事实编号在快照里有冲突值。")
    refs = set(card.get("fact_ids") or [])
    for i, fact_id in enumerate(card.get("fact_ids") or []):
        item = index.get(fact_id)
        if item is None or item["ticker"] != ticker or fact_id in conflicts:
            add("D01", f"fact_ids[{i}]", "事实编号不属于本标的的当前快照。")
    expected = [index[fact_id] for fact_id in card.get("fact_ids", [])
                if fact_id in index and index[fact_id]["ticker"] == ticker]
    if card.get("facts") != expected:
        add("D01", "facts", "事实表必须由 Python 按快照中的编号、数值和来源完整填入。")
    if card.get("evidence_windows") != snapshot.get("evidence_windows", []):
        add("D01", "evidence_windows", "证据窗口必须由 Python 按实际展示记录填入。")
    for i, call in enumerate(snapshot.get("calls", [])):
        envelope = call.get("envelope", {})
        if envelope.get("as_of") != as_of or call.get("args", {}).get("as_of") != as_of:
            add("D01", f"snapshot.calls[{i}].as_of", "工具调用的截至日与本次分析不一致。")
        end = (envelope.get("data") or {}).get("data_end_date")
        age = None
        if end:
            try:
                age = (date.fromisoformat(as_of) - date.fromisoformat(end)).days
                if age < 0:
                    add("D03", f"snapshot.calls[{i}].data_end_date", "工具数据日期晚于分析截至日。")
            except ValueError:
                add("D03", f"snapshot.calls[{i}].data_end_date", "工具数据日期无效。")
        if call.get("tool") == "get_price_history" and call.get("args", {}).get("symbol") == ticker:
            if envelope.get("status") != "ok" or not end or not (envelope.get("data") or {}).get("latest_close"):
                add("D03", f"snapshot.calls[{i}]", "价格数据不可用。")
            elif age is not None and age > PRICE_STALENESS_DAYS:
                add("D03", f"snapshot.calls[{i}].data_end_date",
                    f"价格数据距分析截至日超过 {PRICE_STALENESS_DAYS} 个日历日。")
    prices = [call for call in snapshot.get("calls", []) if call.get("tool") == "get_price_history"
              and call.get("args", {}).get("symbol") == ticker]
    if not prices:
        add("D03", "snapshot.calls", "缺少本标的的价格工具调用。")
    values = {_number(item["value"]) for item in index.values() if item["ticker"] == ticker}
    # The naked-number rule reads the same display formatter as the card, so any
    # number the card would show ("50.06%", "1.10 倍", "333.69 美元/股") is bare.
    from thesis_tracker.decision.evidence import display_text

    for item in index.values():
        if item["ticker"] != ticker:
            continue
        shown = display_text(item["value"], item["unit"], name=item["name"])
        if shown:
            values.update(number for match in NUMBER.finditer(shown)
                          if (number := _number(match.group())) is not None)
    for i, reason in enumerate(card.get("reasons") or []):
        ids = reason.get("fact_ids") or []
        if not ids:
            add("D09", f"reasons[{i}].fact_ids", "每条理由至少引用一个事实编号。")
        for fact_id in ids:
            if fact_id not in refs or fact_id not in index or index[fact_id]["ticker"] != ticker:
                add("D01", f"reasons[{i}].fact_ids", "理由引用了无效的事实编号。")
        content = str(reason.get("text") or "")
        for fact_id in PLACEHOLDER.findall(content):
            if fact_id not in ids or fact_id not in index or index[fact_id]["ticker"] != ticker:
                add("D02", f"reasons[{i}].text", "事实占位符没有对应的有效引用。")
        free = _free_numbers(content)
        if free:
            message = ("理由中的事实数字必须使用事实占位符。" if any(value in values for value in free)
                       else "理由中的裸数字仅允许日期、财期和时间计数。")
            add("D02", f"reasons[{i}].text", message)
        for word in foreign_action_words(content, card.get("action")):
            add("D11", f"reasons[{i}].text",
                f"理由正文写了与动作“{card.get('action')}”不一致的动作词“{word}”。")
    if not card.get("reasons"):
        add("D09", "reasons", "至少需要一条引用事实的理由。")
    for i, condition in enumerate(card.get("invalidations") or []):
        content = str(condition.get("text") or "")
        if condition.get("price") is not None and (
            (condition_price := _number(condition.get("price"))) is None or condition_price <= 0
        ):
            add("D07", f"invalidations[{i}].price", "失效条件中填写的每个价位都必须大于零。")
        for fact_id in PLACEHOLDER.findall(content):
            if fact_id not in refs or fact_id not in index or index[fact_id]["ticker"] != ticker:
                add("D02", f"invalidations[{i}].text", "失效条件含无效事实占位符。")
        free = _free_numbers(content)
        if free:
            message = ("失效条件中的事实数字必须使用占位符。" if any(value in values for value in free)
                       else "失效条件文字中的裸数字仅允许日期、财期和时间计数；价位请填 price 字段。")
            add("D02", f"invalidations[{i}].text", message)
    if card.get("bias") not in ALLOWED_ACTIONS or card.get("action") not in ALLOWED_ACTIONS.get(card.get("bias"), set()):
        add("D04", "bias/action", "倾向与动作不在允许的配对表中。")
    action = card.get("action")
    for field in RATIONALE_FIELDS:
        raw = card.get(field)
        if action not in {"买入", "分批", "持有"}:
            if raw not in (None, ""):
                add("D12", field, "观望、减仓或回避时不得填写止损或目标依据。")
            continue
        if not isinstance(raw, str) or not raw.strip():
            add("D12", field, "买入、分批或持有时必须填写止损与目标依据。")
            continue
        if not PLACEHOLDER.search(raw):
            add("D12", field, "止损与目标依据必须至少引用一个事实占位符。")
        else:
            for fact_id in PLACEHOLDER.findall(raw):
                if fact_id not in refs or fact_id not in index or index[fact_id]["ticker"] != ticker:
                    add("D02", field, "止损或目标依据含无效事实占位符。")
        free = _free_numbers(raw)
        if free:
            message = ("止损或目标依据中的事实数字必须使用事实占位符。"
                       if any(value in values for value in free)
                       else "止损或目标依据中的裸数字仅允许日期、财期和时间计数。")
            add("D02", field, message)
    low_high = card.get("entry_range")
    stop, target = _number(card.get("stop_loss")), _number(card.get("target_price"))
    if action in {"买入", "分批"}:
        if not (isinstance(low_high, list) and len(low_high) == 2 and
                (low := _number(low_high[0])) is not None and
                (high := _number(low_high[1])) is not None and
                stop is not None and target is not None and 0 < stop < low <= high < target):
            add("D05", "entry_range/stop_loss/target_price", "买入或分批须满足 0 < 止损 < 买点下沿 ≤ 上沿 < 目标。")
    elif action == "持有":
        close = next(((((call.get("envelope", {}).get("data") or {}).get("latest_close") or {}).get("value"))
                      for call in prices[:1]), None)
        closing = _number(close)
        if not (low_high is None and stop is not None and closing is not None and target is not None
                and 0 < stop < closing < target):
            add("D05", "entry_range/stop_loss/target_price", "持有须无买点区间且满足 0 < 止损 < 截至日收盘价 < 目标。")
    elif action in {"观望", "减仓", "回避"}:
        if low_high is not None or stop is not None or target is not None:
            add("D05", "entry_range/stop_loss/target_price", "观望、减仓或回避不得填写买点、止损或目标价。")
    close = _number(card.get("creation_price"))
    if action in {"买入", "分批"}:
        bounds = low_high if isinstance(low_high, list) and len(low_high) == 2 else [None, None]
        low, high = _number(bounds[0]), _number(bounds[1])
        if low is not None and high is not None and close is not None and not low <= close <= high:
            add("D14", "entry_range",
                f"买入或分批要求截至日收盘价 {close} 落在买点区间 [{low}, {high}] 内；"
                "现价不在区间内时应改为观望，并在理由和失效条件里写出等待的价位条件。")
    if card.get("gaps") != data_gaps(snapshot):
        add("D06", "gaps", "数据缺口必须与快照中全部不可用指标一致，不得删改。")
    conditions = card.get("invalidations") or []
    if not any(item.get("kind") in {"close_below", "close_above"} and
               (value := _number(item.get("price"))) is not None and value > 0
               for item in conditions):
        add("D07", "invalidations", "至少要有一条收盘价跌破或突破正数价位的机器可检查条件。")
    if action in {"买入", "分批", "持有"} and not any(
            item.get("kind") == "close_below" and _number(item.get("price")) == stop
            for item in conditions):
        add("D15", "invalidations", "买入、分批或持有必须有一条阈值等于止损位的 close_below 失效条件。")
    if card.get("horizon") not in {"短期", "中期", "长期"} or card.get("confidence") not in {"低", "中", "高"} or card.get("confidence_calibration") != "未校准":
        add("D08", "horizon/confidence", "周期仅三档，置信度仅低中高并须标为未校准。")
    requested_horizon = snapshot.get("requested_horizon")
    if requested_horizon is not None and card.get("horizon") != requested_horizon:
        add("D13", "horizon", f"卡片周期必须等于命令行请求的周期（{requested_horizon}）。")
    if card.get("disclaimer") != DISCLAIMER:
        add("D10", "disclaimer", "固定免责声明必须由 Python 原样写入。")
    return errors


def auto_computed(card: dict, snapshot: dict) -> list[dict]:
    """Display-only arithmetic over card prices and snapshot facts.

    Nothing here participates in validation; a missing input yields a plain
    "无法计算" line with the reason instead of a guessed number.
    """
    index, _ = fact_index(snapshot)
    ticker = snapshot.get("ticker")
    action = card.get("action")

    def fact_value(name: str) -> Decimal | None:
        matches = [item for item in index.values()
                   if item["ticker"] == ticker and item["name"] == name
                   and item["value"] is not None]
        return _number(matches[-1]["value"]) if matches else None

    def unavailable(reason: str) -> str:
        return f"无法计算（{reason}）"

    close = _number(card.get("creation_price"))
    stop, target = _number(card.get("stop_loss")), _number(card.get("target_price"))
    entry_range = card.get("entry_range")
    entry: Decimal | None = None
    if action in {"买入", "分批"} and isinstance(entry_range, list) and len(entry_range) == 2:
        low, high = _number(entry_range[0]), _number(entry_range[1])
        if low is not None and high is not None:
            entry = (low + high) / 2
    elif action == "持有":
        entry = close

    items: list[dict] = []
    if entry is not None:
        atr = fact_value("atr_14")
        if stop is None or entry == 0:
            items.append({"label": "止损距离", "text": unavailable("缺少止损位或入场价")})
        else:
            text = f"{(entry - stop) / entry * 100:.2f}%"
            if atr is not None and atr > 0:
                text += f"（{(entry - stop) / atr:.2f} 倍 ATR）"
            items.append({"label": "止损距离", "text": text})
        if target is None or entry == 0:
            items.append({"label": "目标距离", "text": unavailable("缺少目标位或入场价")})
        else:
            text = f"{(target - entry) / entry * 100:.2f}%"
            if atr is not None and atr > 0:
                text += f"（{(target - entry) / atr:.2f} 倍 ATR）"
            items.append({"label": "目标距离", "text": text})
        if stop is None or target is None or entry == stop:
            items.append({"label": "盈亏比",
                          "text": unavailable("缺少止损位、目标位，或止损等于入场价")})
        else:
            items.append({"label": "盈亏比", "text": f"{(target - entry) / (entry - stop):.1f}"})
    high = fact_value("high_52w")
    if high is None or close is None or high == 0:
        items.append({"label": "距52周高点", "text": unavailable("缺少 52 周高点或最新收盘价")})
    else:
        items.append({"label": "距52周高点",
                      "text": f"低于 52 周高点 {(high - close) / high * 100:.2f}%"})
    return items


def render_card(card: dict, snapshot: dict, *,
                version: str = VALIDATOR_VERSION) -> str:
    from thesis_tracker.decision.evidence import (
        display_label,
        display_text,
        fact_category,
    )

    errors = validate_card(card, snapshot, version=version)
    if errors:
        raise ValueError(_canonical(errors))
    index, _ = fact_index(snapshot)
    occurrences = Counter(item["name"] for item in card["facts"])

    def fill(match: re.Match) -> str:
        item = index[match.group(1)]
        return display_text(item["value"], item["unit"], name=item["name"])

    lines = [f"{card['ticker']}｜{card['as_of']}｜{card['horizon']}"]
    close_fact = next((item for item in card["facts"] if item["name"] == "close"), None)
    if close_fact is not None and close_fact.get("date_or_period"):
        lines.append(f"价格数据截至 {close_fact['date_or_period']}")
    lines += [
        f"AI 判断：{card['bias']} / {card['action']}｜置信度 {card['confidence']}（未校准）",
        f"买点 {card.get('entry_range')}｜止损 {card.get('stop_loss')}｜目标 {card.get('target_price')}"]
    if card.get("stop_rationale"):
        lines.append(f"止损依据：{PLACEHOLDER.sub(fill, card['stop_rationale'])}")
    if card.get("target_rationale"):
        lines.append(f"目标依据：{PLACEHOLDER.sub(fill, card['target_rationale'])}")
    lines.append("事实表：")
    lines += [f"- {display_label(item['name'], item['date_or_period'], multiple=occurrences[item['name']] > 1, category=fact_category(item))}: "
              f"{display_text(item['value'], item['unit'], name=item['name'])} ({item['fact_id']})"
              for item in card["facts"]]
    lines += ["理由：", *[f"- {PLACEHOLDER.sub(fill, item['text'])}" for item in card["reasons"]],
              "失效条件："]
    for item in card["invalidations"]:
        threshold = _number(item.get("price"))
        shown = display_text(threshold, "USD/share", name="close") if threshold is not None else "未填阈值"
        verb = {"close_below": "跌破", "close_above": "站上"}.get(item.get("kind"), str(item.get("kind")))
        lines.append(f"- 机器检查：收盘价{verb} {shown}")
        lines.append(f"  说明：{PLACEHOLDER.sub(fill, item.get('text', ''))}")
    lines.append("自动计算（Python）：")
    lines += [f"- {item['label']}: {item['text']}" for item in auto_computed(card, snapshot)]
    lines.append("数据缺口：")
    lines += [f"- {display_label(item['name'])}: {item['status']} ({(item['reason'] or {}).get('message', '')})"
              for item in card["gaps"]]
    return "\n".join([*lines, card["disclaimer"]])


def _connect(path: Path | str) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE IF NOT EXISTS decision_cards (
        card_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, ticker TEXT NOT NULL,
        as_of TEXT NOT NULL, creation_price TEXT, card_json TEXT NOT NULL, snapshot_json TEXT NOT NULL,
        snapshot_sha256 TEXT NOT NULL, validator_version TEXT NOT NULL,
        validation_json TEXT NOT NULL, requested_model TEXT, returned_model TEXT,
        fingerprint TEXT, prompt_version TEXT, input_tokens INTEGER,
        output_tokens INTEGER, cache_hit_tokens INTEGER
    )""")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(decision_cards)")}
    for name in ("input_tokens", "output_tokens", "cache_hit_tokens"):
        if name not in columns:
            conn.execute(f"ALTER TABLE decision_cards ADD COLUMN {name} INTEGER")
    for action in ("UPDATE", "DELETE"):
        conn.execute(f"""CREATE TRIGGER IF NOT EXISTS decision_cards_no_{action.lower()}
            BEFORE {action} ON decision_cards BEGIN SELECT RAISE(ABORT, 'immutable'); END""")
    conn.commit()
    return conn


def append_card(path: Path | str, card: dict, snapshot: dict, *, model: dict | None = None) -> str:
    result = validate_card(card, snapshot)
    if result:
        raise ValueError(_canonical(result))
    model = model or {}
    snapshot_json = _canonical(snapshot)
    card_id = str(uuid.uuid4())
    with _connect(path) as conn:
        conn.execute("""INSERT INTO decision_cards (
            card_id, created_at, ticker, as_of, creation_price, card_json, snapshot_json,
            snapshot_sha256, validator_version, validation_json, requested_model,
            returned_model, fingerprint, prompt_version, input_tokens, output_tokens,
            cache_hit_tokens) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            card_id, datetime.now(timezone.utc).isoformat(), card["ticker"], card["as_of"],
            str(card["creation_price"]),
            _canonical(card), snapshot_json, hashlib.sha256(snapshot_json.encode()).hexdigest(),
            VALIDATOR_VERSION, _canonical(result), model.get("requested_model"), model.get("returned_model"),
            model.get("fingerprint"), model.get("prompt_version"), model.get("input_tokens"),
            model.get("output_tokens"), model.get("cache_hit_tokens"),
        ))
    return card_id


def read_card(path: Path | str, card_id: str) -> dict:
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM decision_cards WHERE card_id=?", (card_id,)).fetchone()
    if row is None:
        raise KeyError(card_id)
    item = dict(row)
    item["card"] = json.loads(item.pop("card_json"))
    item["validation_result"] = json.loads(item.pop("validation_json"))
    return item
