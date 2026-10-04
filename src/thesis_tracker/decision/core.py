"""Capture tool evidence, build and validate cards, and append immutable archives."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from thesis_tracker.financial.tool import get_fundamental_metrics
from thesis_tracker.indicator_tool import get_indicators
from thesis_tracker.prices import get_price_history

DISCLAIMER = "本卡为 AI 研究输出，不构成持牌投资建议。"
VALIDATOR_VERSION = "decision-validator-1"
DEFAULT_ARCHIVE = Path("data/decisions/cards.db")
ALLOWED_ACTIONS = {
    "看多": {"买入", "分批", "持有"},
    "中性": {"分批", "持有", "回避"},
    "看空": {"减仓", "回避"},
}
PLACEHOLDER = re.compile(r"\{fact:([^{}]+)\}")
NUMBER = re.compile(r"(?<![A-Za-z0-9_.])[-+]?\d+(?:\.\d+)?(?![A-Za-z0-9_.])")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fact(item: dict, ticker: str, date_value: str | None, source: dict | None, name: str) -> dict:
    from thesis_tracker.decision.evidence import display_value

    return {"fact_id": item["fact_id"], "name": name, "value": item["value"],
            "display": display_value(item["value"], item["unit"]),
            "unit": item["unit"], "date_or_period": date_value, "ticker": ticker,
            "source": source}


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


def capture_snapshot(ticker: str, as_of: str) -> dict:
    """Call the three local read-only tools with their defaults."""
    ticker = ticker.upper()
    date.fromisoformat(as_of)
    calls = []
    for name, function, key in (
        ("get_price_history", get_price_history, "symbol"),
        ("get_indicators", get_indicators, "symbol"),
        ("get_fundamental_metrics", get_fundamental_metrics, "ticker"),
    ):
        args = {key: ticker, "as_of": as_of}
        calls.append({"tool": name, "args": args, "envelope": function(ticker, as_of=as_of)})
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


def validate_card(card: dict, snapshot: dict) -> list[dict]:
    """Return every mechanical violation. A nonempty result forbids publication."""
    errors: list[dict] = []

    def add(rule: str, location: str, message: str) -> None:
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
            elif age is not None and age > 5:
                add("D03", f"snapshot.calls[{i}].data_end_date", "价格数据距分析截至日超过 5 个日历日。")
    prices = [call for call in snapshot.get("calls", []) if call.get("tool") == "get_price_history"
              and call.get("args", {}).get("symbol") == ticker]
    if not prices:
        add("D03", "snapshot.calls", "缺少本标的的价格工具调用。")
    values = {_number(item["value"]) for item in index.values() if item["ticker"] == ticker}
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
    elif action in {"减仓", "回避"}:
        if low_high is not None or stop is not None or target is not None:
            add("D05", "entry_range/stop_loss/target_price", "减仓或回避不得填写买点、止损或目标价。")
    if card.get("gaps") != data_gaps(snapshot):
        add("D06", "gaps", "数据缺口必须与快照中全部不可用指标一致，不得删改。")
    conditions = card.get("invalidations") or []
    if not any(item.get("kind") in {"close_below", "close_above"} and
               (value := _number(item.get("price"))) is not None and value > 0
               for item in conditions):
        add("D07", "invalidations", "至少要有一条收盘价跌破或突破正数价位的机器可检查条件。")
    if card.get("horizon") not in {"短期", "中期", "长期"} or card.get("confidence") not in {"低", "中", "高"} or card.get("confidence_calibration") != "未校准":
        add("D08", "horizon/confidence", "周期仅三档，置信度仅低中高并须标为未校准。")
    if card.get("disclaimer") != DISCLAIMER:
        add("D10", "disclaimer", "固定免责声明必须由 Python 原样写入。")
    return errors


def render_card(card: dict, snapshot: dict) -> str:
    errors = validate_card(card, snapshot)
    if errors:
        raise ValueError(_canonical(errors))
    index, _ = fact_index(snapshot)
    def fill(match: re.Match) -> str:
        item = index[match.group(1)]
        return f"{item['display']} {item['unit']}"
    lines = [f"{card['ticker']}｜{card['as_of']}｜{card['horizon']}",
             f"AI 判断：{card['bias']} / {card['action']}｜置信度 {card['confidence']}（未校准）",
             f"买点 {card.get('entry_range')}｜止损 {card.get('stop_loss')}｜目标 {card.get('target_price')}",
             "事实表："]
    lines += [f"- {item['name']}: {item['display']} {item['unit']} ({item['fact_id']})"
              for item in card["facts"]]
    lines += ["理由：", *[f"- {PLACEHOLDER.sub(fill, item['text'])}" for item in card["reasons"]],
              "失效条件：", *[f"- {item['kind']} {item.get('price')}: {PLACEHOLDER.sub(fill, item.get('text', ''))}"
                            for item in card["invalidations"]], "数据缺口："]
    lines += [f"- {item['name']}: {item['status']} ({(item['reason'] or {}).get('message', '')})"
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
