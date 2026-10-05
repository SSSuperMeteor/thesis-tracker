"""The read-only tools a chat round may call, and their dispatch.

Three rules hold this module together:

* the model cannot choose a date — ``as_of`` is injected by the program and any
  value the model sends is dropped, not negotiated;
* the model cannot leave the conversation's company (plus SPY, which is a
  benchmark, not a second subject);
* the only way to reach a new opinion is ``request_new_card``, which writes a
  pending proposal and never runs anything.

Card judgment fields are exposed as *pseudo-facts* with ids of the form
``card|<card_id>|<field>`` so that "how far is the close from the stop" becomes
a deterministic subtraction instead of mental arithmetic by the model.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any

from thesis_tracker.decision.core import fact_index, read_card
from thesis_tracker.decision.evidence import (
    HORIZON_LABELS,
    RESOLUTIONS,
    TOOL_HISTORY_FIELDS,
    bucket_dates,
    display_text,
    fact_category,
)
from thesis_tracker.financial.tool import get_fundamental_metrics
from thesis_tracker.indicator_tool import get_indicators
from thesis_tracker.prices import MAX_ROWS, get_price_history

# Fields a card exposes as citable pseudo-facts, in a stable order.
CARD_PRICE_FIELDS = ("action", "tendency", "horizon", "entry_low", "entry_high",
                     "stop_loss", "target_price", "created_at")
# compare_facts operations and the formula each one records in the derived fact.
COMPARE_OPS = {
    # The three the round specifies, plus the two that make a card's own printed
    # arithmetic reachable: a buy range's midpoint is (low + high) / 2, which
    # needs an addition and a halving.  Without them the model would have to
    # invent that number, which C01 rejects.
    "difference": "a - b",
    "ratio": "a / b",
    "pct_change": "(a - b) / b",
    "addition": "a + b",
    "product": "a * scale",
}
# The only dimensionless factors a comparison may use, written exactly.  They are
# ratios of published numbers rather than measurements, so they are the one kind
# of operand the model may supply itself: "half the range" must be expressible
# without the model doing arithmetic C01 would then reject.
ALLOWED_SCALES = (0.5, 1.0, 2.0)
# A product is the one operation whose unit is not the operands' own, so it is
# the only one that may combine a quantity with a dimensionless factor.  This is
# what lets a buy range's midpoint be reached from the card's own fields.
UNIT_FREE_OPS = frozenset({"ratio", "pct_change", "product"})
DERIVED_PREFIX = "derived|chat|"
CARD_ANNOTATION = "AI 判断"

SYSTEM_FACT_LABELS = {"action": "动作", "tendency": "倾向", "horizon": "周期",
                      "entry_low": "买点下沿", "entry_high": "买点上沿",
                      "stop_loss": "止损", "target_price": "目标",
                      "created_at": "创建时间"}
COMPARE_LABELS = {"difference": "差值", "ratio": "比值", "pct_change": "百分比变化",
                  "addition": "合计", "product": "乘积"}

TOOL_NAMES = ("get_price_history", "get_indicators", "get_fundamental_metrics",
              "list_cards", "get_card", "compare_facts", "request_new_card")


def _error(code: str, message: str, *, as_of: str, tool: str) -> dict:
    return {"status": "error", "data": None, "source": {"tool": tool},
            "as_of": as_of, "fact_id": None,
            "reason": {"code": code, "message": message}}


def _envelope(*, as_of: str, data: Any, source: Any = None,
              fact_id: str | None = None) -> dict:
    return {"status": "ok", "data": data, "source": source, "as_of": as_of,
            "fact_id": fact_id, "reason": None}


def card_price_facts(card: dict, card_id: str, *,
                     created_at: str | None) -> list[dict]:
    """Card judgment fields as citable pseudo-facts.

    ``card_id`` and ``created_at`` are passed in rather than read from the card:
    the archive keeps them in its own columns, not inside the card JSON.  This is
    a module function so the conversation context can show a pinned card's fields
    without constructing a toolbox.
    """
    facts: list[dict] = []
    for field in CARD_PRICE_FIELDS:
        unit = "text"
        if field in {"action", "tendency", "horizon"}:
            value = {"action": card.get("action"), "tendency": card.get("bias"),
                     "horizon": card.get("horizon")}[field]
            display = value
        elif field == "created_at":
            from thesis_tracker.webapp import display as display_module

            value = created_at
            display = display_module.format_timestamp(value)
        elif field in {"entry_low", "entry_high"}:
            bounds = card.get("entry_range")
            if not (isinstance(bounds, list) and len(bounds) == 2):
                continue
            value = bounds[0] if field == "entry_low" else bounds[1]
            display = display_text(value, "USD/share", name="close")
            unit = "USD/share"
        else:
            value = card.get(field)
            display = display_text(value, "USD/share", name="close")
            unit = "USD/share"
        if value is None or display is None:
            continue
        facts.append({
            "fact_id": f"card|{card_id}|{field}",
            "name": field,
            "label": SYSTEM_FACT_LABELS[field],
            "value": str(value),
            "unit": unit,
            "display": str(display),
            "date_or_period": card.get("as_of"),
            "ticker": card.get("ticker"),
            "category": "card",
            "card_id": card_id,
            "field": field,
            "annotation": CARD_ANNOTATION,
            "source": {"provider": "decision_archive", "card_id": card_id,
                       "field": field},
        })
    return facts


class ToolBox:
    """One conversation's view of the local data, plus its evidence set."""

    def __init__(self, *, store, conversation: dict, as_of: str, fact_db, price_db,
                 card_db, message_id: str | None = None) -> None:
        self.store = store
        self.conversation = conversation
        self.ticker = conversation["ticker"].upper()
        self.conversation_id = conversation["conversation_id"]
        # The audit rows belong to the user message this turn is answering.
        self.message_id = message_id
        self.as_of = as_of
        self.fact_db = fact_db
        self.price_db = price_db
        self.card_db = card_db
        self.records: list[dict] = []

    # -- schemas -------------------------------------------------------------

    def accepted_arguments(self, name: str) -> set[str]:
        """Arguments the dispatch layer for ``name`` actually reads."""
        return {
            "get_price_history": {"ticker", "resolution", "fields"},
            "get_indicators": {"ticker", "resolution", "fields"},
            "get_fundamental_metrics": {"ticker"},
            "list_cards": set(),
            "get_card": {"card_id"},
            "compare_facts": {"a", "b", "op", "scale"},
            "request_new_card": {"horizon", "reason"},
        }.get(name, set())

    def schemas(self) -> list[dict]:
        """Tool schemas; every advertised argument is one dispatch accepts."""
        ticker_note = (f"The conversation is about {self.ticker}. Only {self.ticker} "
                       "and SPY are allowed; SPY is a benchmark for comparison.")

        def history_tool(name: str, description: str) -> dict:
            return {"type": "function", "function": {
                "name": name, "description": description,
                "parameters": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "ticker": {"type": "string", "description": ticker_note},
                        "resolution": {"type": "string", "enum": list(RESOLUTIONS),
                                       "description": ("Tiered window; give it together "
                                                       "with fields.")},
                        "fields": {"type": "array",
                                   "items": {"type": "string",
                                             "enum": list(TOOL_HISTORY_FIELDS[name])},
                                   "description": ("Fields to return with a resolution. "
                                                   "Supported: "
                                                   + ", ".join(TOOL_HISTORY_FIELDS[name])
                                                   + ".")}},
                    "required": []}}}

        return [
            history_tool("get_price_history",
                         "Read this company's local stored daily prices from the newest "
                         "available day. Omit resolution for the latest price page, or "
                         "give a resolution and fields for a tiered history window."),
            history_tool("get_indicators",
                         "Read this company's local adjusted-price indicators (moving "
                         "averages, RSI, MACD histogram, volume ratio) computed from "
                         "stored prices."),
            {"type": "function", "function": {
                "name": "get_fundamental_metrics",
                "description": ("Read this company's eight point-in-time SEC financial "
                                "metrics, with an explicit reason for any that cannot "
                                "be computed."),
                "parameters": {"type": "object", "additionalProperties": False,
                               "properties": {"ticker": {"type": "string",
                                                         "description": ticker_note}},
                               "required": []}}},
            {"type": "function", "function": {
                "name": "list_cards",
                "description": (f"List the archived advice cards for {self.ticker}, newest "
                                "first. These are previously generated AI judgments, "
                                "not facts about the company."),
                "parameters": {"type": "object", "additionalProperties": False,
                               "properties": {}, "required": []}}},
            {"type": "function", "function": {
                "name": "get_card",
                "description": ("Read one archived advice card: its AI judgment fields, "
                                "its evidence facts with their ids, and its price levels "
                                "as citable placeholder targets."),
                "parameters": {"type": "object", "additionalProperties": False,
                               "properties": {"card_id": {
                                   "type": "string",
                                   "description": "A card id from list_cards."}},
                               "required": ["card_id"]}}},
            {"type": "function", "function": {
                "name": "compare_facts",
                "description": ("Do arithmetic on two facts and return a new fact you may "
                                "cite. Use this instead of calculating yourself. Both "
                                "operands must have the same unit."),
                "parameters": {"type": "object", "additionalProperties": False,
                               "properties": {
                                   "a": {"type": "string", "description": "Left fact id."},
                                   "b": {"type": "string", "description": "Right fact id."},
                                   "op": {"type": "string", "enum": list(COMPARE_OPS),
                                          "description": ("difference: a - b. ratio: a / b. "
                                                          "pct_change: (a - b) / b. "
                                                          "product: a * scale.")},
                                   "scale": {"type": "number",
                                             "enum": list(ALLOWED_SCALES),
                                             "description": ("Only for product: the factor to "
                                                             "multiply a by. Use 0.5 for half "
                                                             "of a span.")}},
                               "required": ["a", "b", "op"]}}},
            {"type": "function", "function": {
                "name": "request_new_card",
                "description": ("Propose generating a new advice card and return its id. "
                                "This runs nothing: the user must confirm it. Never say a "
                                "card has been generated."),
                "parameters": {"type": "object", "additionalProperties": False,
                               "properties": {
                                   "horizon": {
                                       "type": "string", "enum": list(HORIZON_LABELS),
                                       "description": "Requested analysis horizon."},
                                   "reason": {
                                       "type": "string",
                                       "description": ("One sentence: what new judgment "
                                                       "is needed and why.")}},
                               "required": ["horizon", "reason"]}}},
        ]

    # -- dispatch ------------------------------------------------------------

    def call(self, name: str, arguments: dict) -> tuple[dict | None, dict | None]:
        """Run one tool.  Returns (envelope, error_envelope); exactly one is set."""
        arguments = dict(arguments or {})
        arguments.pop("as_of", None)  # the program owns the date
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return None, _error("unknown_tool",
                                f"没有名为 {name} 的工具。可用工具："
                                + "、".join(TOOL_NAMES) + "。",
                                as_of=self.as_of, tool=name)
        envelope = handler(**arguments)
        facts = self._facts_from(envelope, name)
        if facts:
            self.store.add_facts(self.conversation_id, facts)
        payload = json.dumps(envelope, ensure_ascii=False)
        if self.message_id:
            self.store.append_tool_call(self.message_id, tool=name, args=dict(arguments),
                                        envelope=envelope)
        self.records.append({
            "tool": name, "args": dict(arguments),
            "bytes": len(payload.encode("utf-8")),
            "envelope": envelope, "fact_count": len(facts)})
        if envelope.get("status") == "error":
            return None, envelope
        return envelope, None

    def facts(self) -> dict[str, dict]:
        """Everything this conversation may cite."""
        return self.store.facts(self.conversation_id)

    # -- company resolution --------------------------------------------------

    def _company(self, ticker: str | None) -> tuple[str | None, dict | None]:
        symbol = (ticker or self.ticker).strip().upper()
        if symbol not in {self.ticker, "SPY"}:
            return None, _error(
                "ticker_not_in_conversation",
                f"这个对话只讨论 {self.ticker}（以及作为基准的 SPY），不能查询 {symbol}。"
                f"请改用 {self.ticker} 或 SPY。",
                as_of=self.as_of, tool="")
        return symbol, None

    # -- the three analysis tools -------------------------------------------

    def _tool_get_price_history(self, ticker: str | None = None,
                                resolution: str | None = None,
                                fields: list[str] | None = None) -> dict:
        return self._history_tool("get_price_history", ticker, resolution, fields,
                                  get_price_history)

    def _tool_get_indicators(self, ticker: str | None = None,
                             resolution: str | None = None,
                             fields: list[str] | None = None) -> dict:
        return self._history_tool("get_indicators", ticker, resolution, fields,
                                  get_indicators)

    def _history_tool(self, name, ticker, resolution, fields, function) -> dict:
        symbol, refusal = self._company(ticker)
        if refusal is not None:
            refusal["source"] = {"tool": name}
            return refusal
        if resolution is None and fields is None:
            return function(symbol, as_of=self.as_of, db_path=self.price_db)
        if resolution is None or fields is None:
            return _error("resolution_required",
                          f"{name} 要取分档历史必须同时给 resolution 和 fields。"
                          f"可选 resolution：{', '.join(RESOLUTIONS)}；"
                          f"该工具支持的 fields：{', '.join(TOOL_HISTORY_FIELDS[name])}。",
                          as_of=self.as_of, tool=name)
        if resolution not in RESOLUTIONS:
            return _error("resolution_required",
                          f"resolution 只能是 {', '.join(RESOLUTIONS)}，收到 {resolution}。",
                          as_of=self.as_of, tool=name)
        allowed = TOOL_HISTORY_FIELDS[name]
        unwanted = [item for item in fields if item not in allowed]
        if unwanted or not fields:
            return _error("unsupported_fields",
                          f"{name} 只支持这些 fields：{', '.join(allowed)}；"
                          f"收到 {', '.join(unwanted) or '空列表'}。",
                          as_of=self.as_of, tool=name)
        envelope = function(symbol, as_of=self.as_of, full_history=True,
                            limit=MAX_ROWS, db_path=self.price_db)
        if envelope.get("status") != "ok":
            return envelope
        return self._tiered(envelope, resolution, fields)

    def _tiered(self, envelope: dict, resolution: str, fields: list[str]) -> dict:
        """Trim a full history page down to the requested tier's rows."""
        data = envelope.get("data") or {}
        rows = [row for row in (data.get("rows") or []) if row.get("date")]
        if not rows:
            return envelope
        dates = [row["date"] for row in rows]
        wanted = set(bucket_dates(dates, resolution, dates[-1]))
        trimmed = []
        for row in rows:
            if row["date"] not in wanted:
                continue
            if "values" in row:
                values = {key: value for key, value in (row.get("values") or {}).items()
                          if key in fields}
                trimmed.append({"date": row["date"], "values": values})
            else:
                kept = {key: row[key] for key in ("close", "adjusted_close")
                        if key in fields and key in row}
                trimmed.append({"date": row["date"], "fact_id": row.get("fact_id"),
                                **kept})
        return {**envelope,
                "data": {**data, "resolution": resolution, "fields": list(fields),
                         "rows": trimmed, "returned_rows": len(trimmed)}}

    def _tool_get_fundamental_metrics(self, ticker: str | None = None) -> dict:
        symbol, refusal = self._company(ticker)
        if refusal is not None:
            refusal["source"] = {"tool": "get_fundamental_metrics"}
            return refusal
        return get_fundamental_metrics(symbol, as_of=self.as_of, db_path=self.fact_db)

    # -- cards ---------------------------------------------------------------

    def cards(self) -> list[dict]:
        from thesis_tracker.webapp import service

        return service.card_list(card_db=self.card_db, ticker=self.ticker)

    def _tool_list_cards(self) -> dict:
        cards = [{"card_id": item["card_id"], "ticker": item["ticker"],
                  "as_of": item["as_of"], "created_at": item["created_at"],
                  "action": item["action"], "tendency": item["bias"],
                  "horizon": item["horizon"], "confidence": item["confidence"]}
                 for item in self.cards()]
        return _envelope(as_of=self.as_of,
                         data={"ticker": self.ticker, "cards": cards,
                               "count": len(cards)},
                         source={"provider": "decision_archive"},
                         fact_id=f"card_list|{self.ticker}")

    def _tool_get_card(self, card_id: str | None = None) -> dict:
        if not card_id:
            return _error("card_required",
                          "get_card 需要一个 card_id；先调用 list_cards 取编号。",
                          as_of=self.as_of, tool="get_card")
        if card_id not in {item["card_id"] for item in self.cards()}:
            return _error("card_not_found",
                          f"这个对话里没有编号为 {card_id} 的建议卡。"
                          "请先调用 list_cards 取本公司的卡编号。",
                          as_of=self.as_of, tool="get_card")
        archived = read_card(self.card_db, card_id)
        card = archived["card"]
        index, _conflicts = fact_index(json.loads(archived["snapshot_json"]))
        facts = []
        for item in card.get("facts") or []:
            entry = index.get(item["fact_id"])
            if entry is None:
                continue
            facts.append({"fact_id": entry["fact_id"], "name": entry["name"],
                          "label": entry["name"], "value": entry["value"],
                          "unit": entry["unit"],
                          "date_or_period": entry["date_or_period"],
                          "display": display_text(entry["value"], entry["unit"],
                                                  name=entry["name"]),
                          "category": fact_category(entry)})
        return _envelope(
            as_of=self.as_of,
            data={"card_id": card_id, "ticker": card["ticker"], "as_of": card["as_of"],
                  "created_at": archived.get("created_at"),
                  "action": card.get("action"), "tendency": card.get("bias"),
                  "horizon": card.get("horizon"),
                  "confidence": card.get("confidence"),
                  "confidence_calibration": card.get("confidence_calibration"),
                  "facts": facts,
                  "prices": card_price_facts(card, card_id,
                                             created_at=archived.get("created_at")),
                  "disclaimer": card.get("disclaimer"),
                  "note": ("action、tendency、horizon 与价位都是这张卡的 AI 判断，"
                           "不是公司事实；引用时用对应的占位符。")},
            source={"provider": "decision_archive", "card_id": card_id},
            fact_id=f"card|{card_id}")

    # -- arithmetic ----------------------------------------------------------

    def _tool_compare_facts(self, a: str | None = None, b: str | None = None,
                            op: str | None = None, scale: float | None = None) -> dict:
        if op not in COMPARE_OPS:
            return _error("unsupported_op",
                          f"op 只能是 {', '.join(COMPARE_OPS)}；收到 {op}。",
                          as_of=self.as_of, tool="compare_facts")
        known = self.facts()
        if op == "product":
            if scale not in ALLOWED_SCALES:
                return _error("unsupported_scale",
                              f"product 需要一个 scale，且只能是 "
                              f"{'、'.join(str(item) for item in ALLOWED_SCALES)}；"
                              f"收到 {scale}。",
                              as_of=self.as_of, tool="compare_facts")
            right = {"unit": "ratio", "value": str(scale), "fact_id": f"scale|{scale}",
                     "name": "scale"}
            left = known.get(a)
            if left is None:
                return _error("unknown_fact",
                              f"编号 {a} 不在这个对话的证据里。"
                              "只能引用本对话工具返回过的事实或读过的卡价位。",
                              as_of=self.as_of, tool="compare_facts")
            second = _decimal(scale)
            first = _decimal(left["value"])
            if first is None or second is None:
                return _error("not_computable", "这个事实不是可运算的数值。",
                              as_of=self.as_of, tool="compare_facts")
            result = first * second
            unit = left["unit"]
            fact_id = derived_fact_id(a, f"scale:{scale}", op)
            display = display_text(str(result), unit, name=f"compare_{op}")
            if display is None:
                return _error("not_computable", "运算结果无法按现有显示规则呈现。",
                              as_of=self.as_of, tool="compare_facts")
            fact = {"fact_id": fact_id, "name": f"compare_{op}",
                    "label": COMPARE_LABELS[op], "value": _canonical_number(result),
                    "unit": unit, "display": display,
                    "date_or_period": left.get("date_or_period"), "ticker": self.ticker,
                    "category": "derived", "origin": "derived",
                    "source": {"provider": "derived",
                               "formula": f"a * {scale:g}",
                               "source_fact_ids": [a]}}
            return _envelope(as_of=self.as_of,
                             data={"fact": fact, "formula": f"a * {scale:g}",
                                   "operands": {"a": a, "op": op, "scale": scale}},
                             source=fact["source"], fact_id=fact_id)
        left, right = known.get(a), known.get(b)
        if left is None or right is None:
            missing = a if left is None else b
            return _error("unknown_fact",
                          f"编号 {missing} 不在这个对话的证据里。"
                          "只能引用本对话工具返回过的事实或读过的卡价位。",
                          as_of=self.as_of, tool="compare_facts")
        if left["unit"] != right["unit"]:
            # A product may scale one quantity by a dimensionless factor, which
            # is how "half the buy range's span" is expressed with operations the
            # model may compose.  Two quantities of different units still cannot
            # be multiplied: the result would have no meaning to display.
            factor_units = {"ratio", "percent"}
            quantities = [unit for unit in (left["unit"], right["unit"])
                          if unit not in factor_units]
            compatible = (op == "product"
                          and any(unit in factor_units
                                  for unit in (left["unit"], right["unit"]))
                          and len(quantities) <= 1)
            if not compatible:
                return _error("unit_mismatch",
                              f"两个操作数单位不同（{left['unit']} 与 {right['unit']}），"
                              "不能直接运算。请选择单位相同的事实，"
                              "或用 ratio/percent 乘一个数量。",
                              as_of=self.as_of, tool="compare_facts")
        first, second = _decimal(left["value"]), _decimal(right["value"])
        if first is None or second is None:
            return _error("not_computable", "这两个事实里有一个不是可运算的数值。",
                          as_of=self.as_of, tool="compare_facts")
        if op == "difference":
            result = first - second
        elif op == "addition":
            result = first + second
        elif op == "ratio":
            if second == 0:
                return _error("division_by_zero",
                              "第二个操作数为零，比值没有定义（不能除以零）。",
                              as_of=self.as_of, tool="compare_facts")
            result = first / second
        else:
            if second == 0:
                return _error("division_by_zero",
                              "第二个操作数为零，百分比变化没有定义（不能除以零）。",
                              as_of=self.as_of, tool="compare_facts")
            # The shared display rule multiplies a `percent` value by 100, the
            # same way it renders a stored margin of 0.5 as 50.00%.  Handing it
            # the raw fraction displayed a 3.88% move as 0.04%.
            result = (first - second) / second * 100
        unit = _result_unit(op, left["unit"])
        fact_id = derived_fact_id(a, b, op)
        display = display_text(str(result), unit, name=f"compare_{op}")
        if display is None:
            return _error("not_computable", "运算结果无法按现有显示规则呈现。",
                          as_of=self.as_of, tool="compare_facts")
        fact = {"fact_id": fact_id, "name": f"compare_{op}",
                "label": COMPARE_LABELS[op], "value": _canonical_number(result),
                "unit": unit, "display": display,
                "date_or_period": left.get("date_or_period"), "ticker": self.ticker,
                "category": "derived", "origin": "derived",
                "source": {"provider": "derived", "formula": COMPARE_OPS[op],
                           "source_fact_ids": [a, b]}}
        return _envelope(as_of=self.as_of,
                         data={"fact": fact, "formula": COMPARE_OPS[op],
                               "operands": {"a": a, "b": b, "op": op}},
                         source=fact["source"], fact_id=fact_id)

    # -- proposal ------------------------------------------------------------

    def _tool_request_new_card(self, horizon: str | None = None,
                               reason: str | None = None) -> dict:
        if horizon not in HORIZON_LABELS:
            return _error("invalid_horizon",
                          f"horizon 只能是 {', '.join(HORIZON_LABELS)}；收到 {horizon}。",
                          as_of=self.as_of, tool="request_new_card")
        if not reason or not str(reason).strip():
            return _error("reason_required",
                          "request_new_card 需要一句 reason，说明为什么需要新的判断。",
                          as_of=self.as_of, tool="request_new_card")
        proposal = self.store.create_proposal(
            self.conversation_id, self._latest_message_id(), horizon=horizon,
            reason=str(reason).strip())
        return _envelope(
            as_of=self.as_of,
            data={"proposal_id": proposal["proposal_id"], "horizon": horizon,
                  "horizon_label": HORIZON_LABELS[horizon],
                  "reason": proposal["reason"], "status": proposal["status"],
                  "message": ("提议已记录，等用户在界面上确认后才会生成。"
                              "不要在回答里说卡已经生成。")},
            source={"provider": "chat"}, fact_id=f"proposal|{proposal['proposal_id']}")

    def _latest_message_id(self) -> str:
        messages = self.store.messages(self.conversation_id)
        return messages[-1]["message_id"] if messages else ""

    # -- fact extraction -----------------------------------------------------

    def _facts_from(self, envelope: dict, tool: str) -> list[dict]:
        """Compile an envelope's observable values into citable facts.

        The dispatch is by *tool name*, never by the shape of the payload or the
        prefix of an id.  Both of those were tried and both were wrong:
        ``compare_facts`` also returns a ``prices``-less dict, and a derived
        fact's id could be mistaken for a card's, so a computed fact silently
        never entered the evidence set and the next step of a chain could not
        cite it.
        """
        if envelope.get("status") != "ok":
            return []
        data = envelope.get("data")
        if tool == "get_card" and isinstance(data, dict):
            # Reading a card makes two things citable: its judgment fields as
            # pseudo-facts, and its own archived evidence facts.
            card_facts = [{**fact, "ticker": data.get("ticker"),
                           "category": fact.get("category") or "market",
                           "source": {"provider": "decision_archive",
                                      "card_id": data["card_id"]}}
                          for fact in data.get("facts") or []]
            return [*(data.get("prices") or []), *card_facts]
        if tool == "compare_facts" and isinstance(data, dict) and "fact" in data:
            return [data["fact"]]
        return compile_facts(envelope, tool=tool, ticker=self.ticker)


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _canonical_number(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _result_unit(op: str, unit: str) -> str:
    """A difference keeps the operand's unit; a ratio is dimensionless.

    ``ratio`` renders as a plain four-decimal number and ``percent`` as a
    percentage, both by the shared display rule, so the choice matters.
    """
    if op in {"difference", "addition"}:
        return unit
    return "ratio" if op == "ratio" else "percent"


def derived_fact_id(a: str, b: str, op: str) -> str:
    """Deterministic id: the same comparison is always the same fact."""
    digest = hashlib.sha256(f"{a}|{b}|{op}".encode()).hexdigest()
    return f"{DERIVED_PREFIX}{digest[:24]}"


def compile_facts(envelope: dict, *, tool: str, ticker: str) -> list[dict]:
    """Compile one tool envelope into citable facts, with no guessing.

    Each tool reports observations in its own shape, and every observation
    carries the ``fact_id`` the decision tools already assign:

    * ``get_price_history``: ``data.latest_close`` plus ``rows[]`` whose price
      cells are ``{value, unit, adjusted}`` and whose own ``fact_id`` identifies
      the day's raw close.  Only the raw close becomes a fact: the card's own
      derived convention numbers adjusted closes differently, so inventing an id
      here would put two identifiers on one number and give the model something
      to cite that no card can reproduce;
    * ``get_indicators``: ``data.latest = {date, values{}}`` plus ``rows[]`` of
      the same shape, each value carrying its own ``fact_id``;
    * ``get_fundamental_metrics``: ``data.metrics{name}`` each with ``fact_id``,
      ``unit`` and ``period_end``; an unavailable metric is skipped, because a
      ``null`` value is not a fact.

    An observation without a ``fact_id`` is not citable and is dropped.
    """
    data = envelope.get("data") or {}
    facts: list[dict] = []

    def add(fact_id, name, value, unit, date_or_period, category):
        if not fact_id or value is None or isinstance(value, bool):
            return
        display = display_text(value, unit, name=name)
        if display is None:
            return
        facts.append({"fact_id": fact_id, "name": name, "value": str(value),
                      "unit": unit, "display": display,
                      "date_or_period": date_or_period, "ticker": ticker,
                      "category": category, "source": envelope.get("source")})

    if tool == "get_price_history":
        latest = data.get("latest_close")
        if isinstance(latest, dict) and latest.get("value") is not None:
            add(envelope.get("fact_id"), "close", latest["value"],
                latest.get("unit") or "USD/share", data.get("data_end_date"), "market")
        for row in data.get("rows") or []:
            cell = row.get("close")
            if not isinstance(cell, dict) or cell.get("value") is None:
                continue
            add(row.get("fact_id"), "close", cell["value"],
                cell.get("unit") or "USD/share", row.get("date"), "market")
        return facts

    if tool == "get_indicators":
        block = data.get("latest") or {}
        for name, metric in (block.get("values") or {}).items():
            add(metric.get("fact_id"), name, metric.get("value"),
                metric.get("unit") or "ratio", metric.get("date") or block.get("date"),
                "market")
        for row in data.get("rows") or []:
            for name, metric in (row.get("values") or {}).items():
                add(metric.get("fact_id"), name, metric.get("value"),
                    metric.get("unit") or "ratio",
                    metric.get("date") or row.get("date"), "market")
        return facts

    if tool == "get_fundamental_metrics":
        for name, metric in (data.get("metrics") or {}).items():
            if metric.get("status") != "ok":
                continue
            add(metric.get("fact_id"), name, metric.get("value"),
                metric.get("unit") or "ratio", metric.get("period_end"), "fundamental")
        return facts

    return facts
