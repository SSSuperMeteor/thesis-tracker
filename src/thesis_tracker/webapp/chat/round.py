"""One chat round: a bounded tool loop around the chat validator.

The shape mirrors the analysis loop in ``decision.agent`` (same conservative
token estimate, same per-request gate, same "reject rather than publish a bad
draft" stance) but the limits are its own, because a conversation turn is short:

* ``MAX_TOOL_CALLS`` = 8 tool calls per turn;
* ``MAX_ROUNDS`` = 10 model requests per turn;
* ``MAX_REVISIONS`` = 2 corrections after a rejected draft;
* one request estimated over ``MAX_REQUEST_INPUT_TOKENS`` is never sent;
* a turn whose own tokens pass ``MAX_TURN_TOKENS`` fails.

There is deliberately no conversation-level or company-level ceiling: the user
asked to see what a turn costs, not to be capped.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from thesis_tracker.webapp.chat import PROMPT_VERSION
from thesis_tracker.webapp.chat.context import (
    HISTORY_NOTE,
    HISTORY_WINDOW,
    company_brief,
    history_messages,
    pinned_card,
    system_prompt,
)
from thesis_tracker.webapp.chat.render import render_answer
from thesis_tracker.webapp.chat.tools import ToolBox
from thesis_tracker.webapp.chat.validate import Evidence, validate_answer

MAX_TOOL_CALLS = 8
MAX_ROUNDS = 10
MAX_REVISIONS = 2
MAX_REQUEST_INPUT_TOKENS = 200_000
MAX_TURN_TOKENS = 300_000
# One byte is at most one token, plus room for chat framing; the same
# conservative estimate the analysis loop uses.
FRAMING_TOKENS = 2_048
DEFAULT_MAX_TOKENS = 2_048
# Tool results are trimmed to this before they enter the context, so one large
# history page cannot crowd out the conversation.
MAX_TOOL_RESULT_BYTES = 32 * 1024

# The prompt window is defined in :mod:`context`; it is re-exported here so a
# caller reasoning about one turn's limits finds them all in one module.
__all__ = ["HISTORY_NOTE", "HISTORY_WINDOW", "ChatRound", "run_chat_turn"]


class ChatRound:
    """Run one user message to a validated answer, or a recorded rejection."""

    def __init__(self, *, store, conversation: dict, message: dict, as_of: str,
                 fact_db, price_db, card_db, client: Any, progress=None) -> None:
        self.store = store
        self.conversation = conversation
        self.message = message
        self.as_of = as_of
        self.client = client
        self.progress = progress
        self.ticker = conversation["ticker"].upper()
        self.toolbox = ToolBox(store=store, conversation=conversation, as_of=as_of,
                               fact_db=fact_db, price_db=price_db, card_db=card_db,
                               message_id=message["message_id"])
        self.stats = {"rounds": 0, "tool_calls": 0, "attempts": 0,
                      "input_tokens": 0, "output_tokens": 0, "cache_hit_tokens": 0,
                      "tools": [], "gate_reason": None}
        self._reported_terminal = False

    # -- progress ------------------------------------------------------------

    def emit(self, event: dict) -> None:
        if self.progress is None:
            return
        try:
            self.progress(event)
        except Exception:
            # A broken progress sink must never lose a paid-for answer.
            pass

    # -- the round -----------------------------------------------------------

    def run(self) -> dict:
        brief = company_brief(ticker=self.ticker, price_db=self.toolbox.price_db)
        card = pinned_card(card_db=self.toolbox.card_db,
                           card_id=self.conversation.get("card_id"))
        history, history_note = history_messages(self.store,
                                                 self.conversation["conversation_id"])
        messages: list[dict] = [
            {"role": "system", "content": system_prompt(self.ticker)},
            {"role": "user", "content": json.dumps(
                {**brief, "pinned_card": card}, ensure_ascii=False)},
            *history,
            {"role": "user", "content": self.message["text"]},
        ]
        schemas = self.toolbox.schemas()

        def reject(reason: str, violations: list[dict] | None = None,
                   attempts: list[dict] | None = None) -> dict:
            recorded = self._record_rejection(reason, violations or [], attempts or [])
            self.emit({"event": "rejected", "reason": reason,
                       "rules": sorted({item["rule"] for item in violations or []}),
                       "rounds": self.stats["rounds"],
                       "tool_calls": self.stats["tool_calls"],
                       "input_tokens": self.stats["input_tokens"],
                       "output_tokens": self.stats["output_tokens"]})
            return {"status": "rejected", "reason": reason, "violations": violations or [],
                    "message": recorded, "text": None, "segments": None,
                    "stats": self.stats, "history": history_note, "proposal": None}

        attempt_log: list[dict] = []
        while self.stats["rounds"] < MAX_ROUNDS:
            estimated = self._estimated_input(messages, schemas)
            if estimated > MAX_REQUEST_INPUT_TOKENS:
                self.stats["gate_reason"] = "estimated_request_input_over_200k"
                return reject("request_input_limit",
                              [_error("L01", "messages", "本轮预估输入超过单请求上限；未发送请求。")],
                              attempt_log)
            if self.stats["input_tokens"] + self.stats["output_tokens"] >= MAX_TURN_TOKENS:
                return reject("token_limit",
                              [_error("L02", "tokens", "本回合累计 token 超过上限。")],
                              attempt_log)
            self.stats["rounds"] += 1
            self.emit({"event": "round_start", "round": self.stats["rounds"],
                       "input_tokens": self.stats["input_tokens"],
                       "output_tokens": self.stats["output_tokens"]})
            try:
                response = self.client.complete(messages=messages, tools=schemas,
                                                max_tokens=DEFAULT_MAX_TOKENS)
            except Exception as exc:
                # SDK errors can carry request headers; never persist their text.
                return reject("model_error",
                              [_error("L00", "model",
                                      f"模型调用失败（{type(exc).__name__}）。")],
                              attempt_log)
            self.store.append_model_call(
                self.message["message_id"], round_no=self.stats["rounds"],
                requested_model=response.get("model") or "deepseek-flash",
                returned_model=response.get("model"),
                fingerprint=response.get("system_fingerprint"),
                input_tokens=(response.get("usage") or {}).get("prompt_tokens"),
                output_tokens=(response.get("usage") or {}).get("completion_tokens"),
                cache_hit_tokens=(response.get("usage") or {}).get(
                    "prompt_cache_hit_tokens"))
            usage = response.get("usage") or {}
            incoming = usage.get("prompt_tokens")
            outgoing = usage.get("completion_tokens")
            cached = usage.get("prompt_cache_hit_tokens")
            if (any(type(value) is not int or value < 0 for value in
                    (incoming, outgoing, cached)) or cached > incoming):
                return reject("usage_unavailable",
                              [_error("L03", "usage", "模型未返回可核算的 token 用量。")],
                              attempt_log)
            self.stats["input_tokens"] += incoming
            self.stats["output_tokens"] += outgoing
            self.stats["cache_hit_tokens"] += cached
            self.emit({"event": "round", "round": self.stats["rounds"],
                       "round_input_tokens": incoming, "round_output_tokens": outgoing,
                       "round_cache_hit_tokens": cached,
                       "input_tokens": self.stats["input_tokens"],
                       "output_tokens": self.stats["output_tokens"],
                       "cache_hit_tokens": self.stats["cache_hit_tokens"]})
            if self.stats["input_tokens"] + self.stats["output_tokens"] > MAX_TURN_TOKENS:
                return reject("token_limit",
                              [_error("L02", "tokens", "本回合累计 token 超过上限。")],
                              attempt_log)

            assistant = response.get("message") or {}
            calls = assistant.get("tool_calls") or []
            if calls:
                messages.append({"role": "assistant",
                                 "content": assistant.get("content"),
                                 "reasoning_content": assistant.get("reasoning_content"),
                                 "tool_calls": calls})
                messages.extend(self._run_tools(calls))
                continue

            body = self._answer_body(assistant.get("content"))
            violations = self._validate(body)
            if not violations:
                text, segments = render_answer(body, evidence=self._evidence())
                # The accepted draft is an attempt too: the count records how
                # many model drafts this turn took, refusals included.
                self.stats["attempts"] += 1
                recorded = self.store.append_message(
                    self.conversation["conversation_id"], role="assistant", text=text,
                    template_text=body, segments=segments,
                    attempts=self.stats["attempts"], prompt_version=PROMPT_VERSION)
                self.emit({"event": "passed", "rounds": self.stats["rounds"],
                           "tool_calls": self.stats["tool_calls"],
                           "input_tokens": self.stats["input_tokens"],
                           "output_tokens": self.stats["output_tokens"]})
                return {"status": "passed", "reason": None, "violations": [],
                        "message": recorded, "text": text, "segments": segments,
                        "stats": self.stats, "history": history_note,
                        "proposal": self._latest_proposal()}
            attempt_log.append({"draft": body, "violations": violations})
            self.stats["attempts"] += 1
            self.emit({"event": "draft_rejected", "attempt": self.stats["attempts"],
                       "rules": sorted({item["rule"] for item in violations}),
                       "violations": violations})
            if self.stats["attempts"] > MAX_REVISIONS:
                return reject("correction_limit", violations, attempt_log)
            messages.append({"role": "assistant", "content": assistant.get("content")})
            messages.append({"role": "user", "content": (
                "上次回答没有通过 Python 校验，请只输出修正后的 JSON。违规项：" +
                json.dumps(violations, ensure_ascii=False))})
        return reject("round_limit",
                      [_error("L04", "rounds", f"对话轮数达到 {MAX_ROUNDS}。")], attempt_log)

    # -- helpers -------------------------------------------------------------

    def _estimated_input(self, messages: list[dict], schemas: list[dict]) -> int:
        payload = json.dumps({"messages": messages, "tools": schemas},
                             ensure_ascii=False).encode("utf-8")
        return len(payload) + FRAMING_TOKENS

    def _run_tools(self, calls: list[dict]) -> list[dict]:
        results = []
        for call in calls:
            function = call.get("function") or {}
            name = function.get("name") or ""
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except (TypeError, ValueError):
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            if self.stats["tool_calls"] >= MAX_TOOL_CALLS:
                payload = _error("L05", "tool_calls",
                                 f"本回合最多 {MAX_TOOL_CALLS} 次工具调用，已经用完。"
                                 "请基于已有数据作答。")
            else:
                self.stats["tool_calls"] += 1
                self.stats["tools"].append(name)
                envelope, error = self.toolbox.call(name, arguments)
                payload = error if error is not None else envelope
                record = self.toolbox.records[-1]
                self.emit({"event": "tool_call", "tool": name,
                           "args": record["args"], "bytes": record["bytes"],
                           "status": payload.get("status")})
            results.append({"role": "tool", "tool_call_id": call.get("id"),
                            "content": _trim(payload)})
        return results

    def _answer_body(self, content: Any) -> str:
        """The answer text out of the model's JSON object."""
        if not isinstance(content, str):
            return ""
        try:
            parsed = json.loads(content)
        except ValueError:
            return content.strip()
        if isinstance(parsed, dict) and isinstance(parsed.get("answer"), str):
            return parsed["answer"]
        return content.strip()

    def _evidence(self) -> Evidence:
        """This conversation's evidence: its facts and the cards it has read."""
        facts = self.toolbox.facts()
        cards: dict[str, dict] = {}
        for fact in facts.values():
            card_id = fact.get("card_id")
            if not card_id:
                continue
            entry = cards.setdefault(card_id, {"card_id": card_id, "fields": {}})
            entry["fields"][fact["field"]] = fact["display"]
            entry.setdefault("as_of", fact.get("date_or_period"))
        pinned = self.conversation.get("card_id")
        if pinned and pinned in cards:
            cards[pinned]["pinned"] = True
        return Evidence(facts=facts, cards=cards,
                        latest_price_date=self._latest_price_date())

    def _latest_price_date(self) -> str | None:
        from thesis_tracker.webapp import data as store

        bounds = store.price_bounds(self.toolbox.price_db).get(self.ticker)
        return None if bounds is None else bounds["end_date"]

    def _validate(self, body: str) -> list[dict]:
        return validate_answer(body, evidence=self._evidence(),
                               user_text=self.message.get("text"))

    def _latest_proposal(self) -> dict | None:
        pending = self.store.pending_proposals(self.conversation["conversation_id"])
        return pending[-1] if pending else None

    def _record_rejection(self, reason: str, violations: list[dict],
                          attempts: list[dict]) -> dict:
        return self.store.append_message(
            self.conversation["conversation_id"], role="assistant", text=None,
            segments=None,
            rejected={"reason": reason, "violations": violations,
                      "draft": attempts[-1]["draft"] if attempts else None,
                      "attempts": attempts},
            attempts=max(self.stats["attempts"], 1), prompt_version=PROMPT_VERSION)


def _error(rule: str, location: str, message: str) -> dict:
    return {"rule": rule, "location": location, "message": message}


def _trim(payload: dict) -> str:
    """Serialize a tool payload, capped so one page cannot flood the context."""
    text = json.dumps(payload, ensure_ascii=False)
    if len(text.encode("utf-8")) <= MAX_TOOL_RESULT_BYTES:
        return text
    head = {"status": payload.get("status"), "truncated": True,
            "truncation_reason": (f"结果超过 {MAX_TOOL_RESULT_BYTES // 1024} KiB，"
                                 "已省略明细；请用更小的 fields 或分档再取。"),
            "reason": payload.get("reason"), "as_of": payload.get("as_of")}
    return json.dumps(head, ensure_ascii=False)


def run_chat_turn(*, store, conversation_id: str, message_id: str, as_of: str | None = None,
                  fact_db, price_db, card_db, client: Any, progress=None) -> dict:
    """Run one stored user message as a chat round."""
    conversation = store.get_conversation(conversation_id)
    message = store.get_message(message_id)
    return ChatRound(store=store, conversation=conversation, message=message,
                     as_of=as_of or date.today().isoformat(), fact_db=fact_db,
                     price_db=price_db, card_db=card_db, client=client,
                     progress=progress).run()
