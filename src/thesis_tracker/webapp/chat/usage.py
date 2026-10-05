"""The four levels of usage the chat page shows.

The question "what has this cost me" has four honest answers, so all four are
computed and each is labelled with its own scope:

* this message;
* this conversation;
* every conversation about this company;
* today, by the machine's own calendar day.

The day boundary is the local one, because that is the day the person reading
the number is having.  Sums come from the stored per-call rows, never from a
running counter that a restart could lose.
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from pathlib import Path

from thesis_tracker.webapp import display
from thesis_tracker.webapp.chat.pricing import (
    MISSING_PRICING_NOTE,
    Pricing,
    cost_label,
    format_cost,
    load_pricing,
    price_note,
)

SCOPES = (("message", "本条消息"), ("conversation", "本对话"),
          ("company", "该公司全部对话"), ("today", "今天"))


def start_of_local_day(now: datetime | None = None) -> str:
    """The UTC ISO instant the current local day began, for a stored comparison."""
    zone = display.local_zone()
    moment = now or datetime.now(timezone.utc)
    local = moment.astimezone(zone)
    midnight = datetime.combine(local.date(), time.min, tzinfo=zone)
    return midnight.astimezone(timezone.utc).isoformat()


def _level(raw: dict, *, pricing: Pricing | None, label: str) -> dict:
    """One scope's totals, with money only when a complete price file exists."""
    amount = None
    if pricing is not None:
        amount = pricing.estimate(input_tokens=raw["input_tokens"],
                                  output_tokens=raw["output_tokens"],
                                  cache_hit_tokens=raw["cache_hit_tokens"])
    return {"scope": label, "input_tokens": raw["input_tokens"],
            "output_tokens": raw["output_tokens"],
            "cache_hit_tokens": raw["cache_hit_tokens"],
            "rounds": raw.get("rounds", 0),
            "cost_usd": amount,
            "cost_text": cost_label(amount, pricing)}


def summary_line(levels: dict) -> str:
    """One quiet sentence: this conversation and today, tokens and (if priced) money."""
    priced = False
    parts = []
    for key in ("conversation", "today"):
        level = levels[key]
        text = (f"{level['scope']} 输入 {level['input_tokens']:,} "
                f"输出 {level['output_tokens']:,} token")
        if level["cost_usd"] is not None:
            priced = True
            text += f"，{format_cost(level['cost_usd'])}"
        parts.append(text)
    note = "（费用按高峰价上界估算）" if priced else f"（{MISSING_PRICING_NOTE}）"
    return "｜".join(parts) + note


def usage_summary(store, *, conversation_id: str, message_id: str | None = None,
                  ticker: str | None = None, pricing_path: Path | str | None = None,
                  now: datetime | None = None) -> dict:
    """The four usage levels plus the price basis they were computed from."""
    pricing = load_pricing(pricing_path) if pricing_path is not None else load_pricing()
    # Guard the scope boundary: a company total must never be assembled from a
    # conversation about a different company.
    ticker = store.get_conversation(conversation_id)["ticker"] if ticker is None else ticker
    message_raw = (store.message_usage(message_id) if message_id
                   else {"input_tokens": 0, "output_tokens": 0,
                         "cache_hit_tokens": 0, "rounds": 0})
    labels = dict(SCOPES)
    levels = {
        "message": _level(message_raw, pricing=pricing, label=labels["message"]),
        "conversation": _level(store.conversation_usage(conversation_id),
                               pricing=pricing, label=labels["conversation"]),
        "company": _level(store.ticker_usage(ticker), pricing=pricing,
                          label=labels["company"]),
        "today": _level(store.usage_since(start_of_local_day(now)), pricing=pricing,
                        label=labels["today"]),
    }
    return {"levels": levels, "summary_line": summary_line(levels),
            "pricing_available": pricing is not None,
            "price_note": price_note(pricing),
            "price_as_of": None if pricing is None else pricing.as_of_date,
            "price_source_url": None if pricing is None else pricing.source_url,
            "caveat": None if pricing is None else "按高峰价上界估算，实际可能更低"}


def analysis_average(job_store, *, window: int = 5) -> dict:
    """Mean tokens of recent analyses, for the pre-confirmation estimate."""
    finished = [job for job in job_store.list(kind="analyze")
                if job["status"] == "succeeded" and isinstance(job.get("result"), dict)
                and isinstance(job["result"].get("usage"), dict)]
    recent = finished[:window]
    if not recent:
        return {"analyses": 0, "input_tokens": None, "output_tokens": None}
    inputs = [job["result"]["usage"].get("input_tokens") or 0 for job in recent]
    outputs = [job["result"]["usage"].get("output_tokens") or 0 for job in recent]
    return {"analyses": len(recent), "input_tokens": round(sum(inputs) / len(recent)),
            "output_tokens": round(sum(outputs) / len(recent))}
