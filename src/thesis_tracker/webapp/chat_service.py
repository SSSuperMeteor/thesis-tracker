"""View models for the chat pages.

The frontend renders strings produced here.  It never parses a placeholder, never
formats a number and never converts a timestamp, for the same reason the card
pages do not: there is exactly one implementation of each display rule and it is
on this side.
"""

from __future__ import annotations

from pathlib import Path

from thesis_tracker.decision.evidence import HORIZON_LABELS
from thesis_tracker.webapp import display
from thesis_tracker.webapp.chat import PROMPT_VERSION
from thesis_tracker.webapp.chat.context import HISTORY_NOTE, HISTORY_WINDOW
from thesis_tracker.webapp.chat.pricing import cost_label, load_pricing
from thesis_tracker.webapp.chat.proposals import card_summary
from thesis_tracker.webapp.chat.render import render_display
from thesis_tracker.webapp.chat.tools import CARD_PRICE_FIELDS
from thesis_tracker.webapp.chat.usage import analysis_average, usage_summary

MAX_MESSAGE_CHARS = 4000

PROPOSAL_STATUS_LABELS = {"pending": "待确认", "confirmed": "已确认", "dismissed": "已忽略"}
ROLE_LABELS = {"user": "我", "assistant": "助手", "system": "系统"}


def conversation_view(store, conversation: dict, *, job_store=None,
                      pricing_path: Path | str | None = None,
                      include_messages: bool = False) -> dict:
    """One conversation: its title, its usage and, on request, its messages."""
    conversation_id = conversation["conversation_id"]
    usage = usage_summary(store, conversation_id=conversation_id,
                          ticker=conversation["ticker"],
                          pricing_path=pricing_path)
    pricing = _pricing(pricing_path)
    view = {
        "conversation_id": conversation_id,
        "ticker": conversation["ticker"],
        "title": store.conversation_title(conversation_id) or "新对话",
        "created_at": display.format_timestamp(conversation["created_at"]),
        "last_activity_at": display.format_timestamp(conversation["last_activity_at"]),
        "archived": bool(conversation["archived"]),
        "pinned_card_id": conversation["card_id"],
        "pinned_card_id_short": (conversation["card_id"] or "")[:8] or None,
        "usage": usage,
    }
    pending = store.pending_proposals(conversation_id)
    view["proposals"] = [proposal_view(item, store=store, job_store=job_store,
                                       pricing_path=pricing_path)
                         for item in store.proposals_for(conversation_id)]
    view["pending_proposal"] = (proposal_view(pending[-1], store=store,
                                              job_store=job_store,
                                              pricing_path=pricing_path)
                                if pending else None)
    if include_messages:
        messages = store.messages(conversation_id)
        view["messages"] = [message_view(item, store=store, pricing=pricing)
                            for item in messages]
        view["history"] = history_note(len(messages))
        view["startup"] = startup_view(store, conversation, job_store=job_store,
                                       pricing_path=pricing_path)
    return view


def history_note(total: int) -> dict:
    """How much of this conversation the model still receives."""
    sent = min(total, HISTORY_WINDOW)
    return {"total": total, "sent": sent, "window": HISTORY_WINDOW,
            "truncated": total > HISTORY_WINDOW, "note": HISTORY_NOTE}


def conversation_list(store, ticker: str, *, job_store=None,
                      pricing_path: Path | str | None = None) -> dict:
    """The company page's 对话 section: one row per conversation."""
    conversations = store.conversations_for(ticker)
    return {"ticker": ticker.upper(),
            "conversations": [conversation_view(store, item, job_store=job_store,
                                                pricing_path=pricing_path)
                              for item in conversations],
            "count": len(conversations)}


def startup_view(store, conversation: dict, *, job_store=None,
                 pricing_path: Path | str | None = None) -> dict:
    """Everything the chat page needs before the first message exists."""
    pricing = usage_summary(store, conversation_id=conversation["conversation_id"],
                            ticker=conversation["ticker"],
                            pricing_path=pricing_path)
    return {
        "ticker": conversation["ticker"],
        "prompt_version": PROMPT_VERSION,
        "history_window": HISTORY_WINDOW,
        "history_note": HISTORY_NOTE,
        "max_message_chars": MAX_MESSAGE_CHARS,
        "billing_note": "每条消息会调用 DeepSeek 并按量计费",
        "price_note": pricing["price_note"],
        "price_as_of": pricing["price_as_of"],
        "price_source_url": pricing["price_source_url"],
        "caveat": pricing["caveat"],
        "analysis_average": analysis_average(job_store) if job_store is not None else None,
        "hint_chips": HINT_CHIPS,
        "data_note": ("只能引用本对话工具返回过的事实、算出的派生事实，"
                      "以及本对话读过的建议卡。"),
    }


# The empty state is an action entry, so the first click is a real question.
HINT_CHIPS = (
    "现在最新的价格和指标是多少？",
    "距离止损还有多远？",
    "最近一期财报的毛利率趋势怎么样？",
    "现在能不能买？",
)


def message_view(message: dict, *, store, pricing=None) -> dict:
    """One message as the page shows it, including a refusal.

    ``pricing`` is resolved once per page rather than per message: the price file
    is a file read, and a conversation has one row per message.
    """
    message_id = message["message_id"]
    if pricing is None:
        pricing = _pricing(None)
    # A turn's cost and its tool calls hang off the user message that asked the
    # question, so an answer reports its own question's spend and lookups.
    billing_id = message.get("reply_to") or message_id
    level = message_usage(store.message_usage(billing_id), pricing=pricing)
    tool_calls = store.tool_calls(billing_id)
    view = {
        "message_id": message_id,
        "reply_to": message.get("reply_to"),
        "role": message["role"],
        "role_label": ROLE_LABELS.get(message["role"], message["role"]),
        "created_at": display.format_timestamp(message["created_at"]),
        "text": message["text"],
        "segments": segments_view(message["segments"]),
        "rejected": rejected_view(message["rejected"]),
        "attempts": message["attempts"],
        "prompt_version": message["prompt_version"],
        "proposal_id": message["proposal_id"],
        "is_answer": message["role"] == "assistant" and message["text"] is not None,
        "tool_summary": tool_summary(tool_calls),
        "tool_calls": [tool_call_view(item) for item in tool_calls],
        "usage_line": usage_line(level),
        "usage": {"levels": {"message": level}},
    }
    if message["role"] == "system":
        view["card_link"] = segments_card_link(message["segments"])
    return view


def segments_view(segments: list | None) -> list | None:
    """Segments with their visible text resolved by the renderer."""
    if segments is None:
        return None
    return [{**item, "visible": render_display(item)} for item in segments]


def segments_card_link(segments: list | None) -> dict | None:
    for item in segments or []:
        if item.get("type") == "card_link":
            return item
    return None


def rejected_view(rejected: dict | None) -> dict | None:
    """A refusal: what was wrong, and nothing that looks like an answer."""
    if rejected is None:
        return None
    attempts = rejected.get("attempts") or []
    return {
        "reason": rejected.get("reason"),
        "headline": "这条回答没有通过检查",
        "violations": rejected.get("violations") or [],
        "rules": sorted({item.get("rule") for item in (rejected.get("violations") or [])
                         if item.get("rule")}),
        "attempt_count": len(attempts),
        "attempts": [{"index": index + 1,
                      "violations": attempt.get("violations") or []}
                     for index, attempt in enumerate(attempts)],
        "has_draft": any(attempt.get("draft") for attempt in attempts)
        or bool(rejected.get("draft")),
    }


def tool_summary(calls: list[dict]) -> dict | None:
    """``查了 N 次数据``, expandable to the individual calls."""
    if not calls:
        return None
    return {"count": len(calls), "label": f"查了 {len(calls)} 次数据",
            "bytes": sum(item["bytes"] for item in calls)}


def tool_call_view(call: dict) -> dict:
    envelope = call["envelope"]
    status = envelope.get("status")
    return {
        "call_no": call["call_no"],
        "tool": call["tool"],
        "args_text": ", ".join(f"{key}={value}" for key, value in call["args"].items())
        or "（无参数）",
        "bytes_text": f"{call['bytes'] / 1024:.1f} KiB",
        "status": status,
        "status_label": "成功" if status == "ok" else "失败",
        "reason": (envelope.get("reason") or {}).get("message"),
    }


def _pricing(pricing_path: Path | str | None):
    return load_pricing(pricing_path) if pricing_path is not None else load_pricing()


def message_usage(raw: dict, *, pricing) -> dict:
    """One turn's own tokens, with money only when prices are configured."""
    amount = None
    if pricing is not None:
        amount = pricing.estimate(input_tokens=raw["input_tokens"],
                                  output_tokens=raw["output_tokens"],
                                  cache_hit_tokens=raw["cache_hit_tokens"])
    return {"scope": "本条消息", "input_tokens": raw["input_tokens"],
            "output_tokens": raw["output_tokens"],
            "cache_hit_tokens": raw["cache_hit_tokens"], "rounds": raw["rounds"],
            "cost_usd": amount, "cost_text": cost_label(amount, pricing)}


def usage_line(level: dict) -> str | None:
    """``本条 输入 1,234 输出 56 token（约 $0.001…）``, or None with no usage."""
    if not level["input_tokens"] and not level["output_tokens"]:
        return None
    return (f"本条 输入 {level['input_tokens']:,} 输出 {level['output_tokens']:,} token"
            f"（{level['cost_text']}）")


def proposal_view(proposal: dict, *, store, job_store=None,
                  pricing_path: Path | str | None = None) -> dict:
    """A pending suggestion to run an analysis, with what it would cost."""
    average = analysis_average(job_store) if job_store is not None else {
        "analyses": 0, "input_tokens": None, "output_tokens": None}
    if average["analyses"]:
        estimate = (f"将调用 DeepSeek 并按量计费，最近 {average['analyses']} 次分析平均用了 "
                    f"{average['input_tokens']:,} 输入 / {average['output_tokens']:,} 输出 token")
    else:
        estimate = "将调用 DeepSeek 并按量计费；还没有可参考的历史用量"
    job = job_store.get(proposal["job_id"]) if (job_store is not None
                                               and proposal["job_id"]) else None
    card_id = proposal["card_id"]
    return {
        "proposal_id": proposal["proposal_id"],
        "conversation_id": proposal["conversation_id"],
        "message_id": proposal["message_id"],
        "horizon": proposal["horizon"],
        "horizon_label": HORIZON_LABELS.get(proposal["horizon"], proposal["horizon"]),
        "reason": proposal["reason"],
        "status": proposal["status"],
        "status_label": PROPOSAL_STATUS_LABELS.get(proposal["status"],
                                                   proposal["status"]),
        "job_id": proposal["job_id"],
        "job_status": None if job is None else job["status"],
        "job_status_label": None if job is None else job["status_label"],
        "card_id": card_id,
        "card_id_short": (card_id or "")[:8] or None,
        "created_at": display.format_timestamp(proposal["created_at"]),
        "estimate": estimate,
        "confirmable": proposal["status"] == "pending",
        "dismissable": proposal["status"] == "pending",
        "pinned_fields": list(CARD_PRICE_FIELDS),
    }


def card_summary_view(card_db, card_id: str) -> dict:
    """A generated card's headline, for the system message's link."""
    return card_summary(card_db, card_id)
