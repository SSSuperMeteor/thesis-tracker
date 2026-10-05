"""Turning a chat proposal into a real analysis, and reporting back.

The confirmation path is deliberately the *same* one the "生成建议卡" button
uses: the same job kind, the same parameter validation, the same worker.  Chat
does not get a second way to spend money, and the model never gets to run an
analysis by asking for one.

The date is the newest stored price day rather than today, because an analysis
dated after the stored prices is rejected by D03; offering a confirmation that
cannot succeed would be a trap.
"""

from __future__ import annotations

from typing import Any

from thesis_tracker.decision.core import read_card
from thesis_tracker.decision.evidence import HORIZON_LABELS, display_text
from thesis_tracker.webapp import data as store_data
from thesis_tracker.webapp.chat.store import ChatStore
from thesis_tracker.webapp.chat.usage import analysis_average
from thesis_tracker.webapp.jobs import KIND_ANALYZE, JobStore

CONFIRMED = "confirmed"
CONFLICT = "conflict"
DISMISSED = "dismissed"
MISSING = "missing"
UNKNOWN_TICKER = "unknown_ticker"


def confirm_proposal(chat: ChatStore, jobs: JobStore, proposal_id: str, *, ticker: str,
                     known_tickers, price_db, horizon: str | None = None) -> dict:
    """Queue the analysis a pending proposal asked for, at most once."""
    try:
        proposal = chat.get_proposal(proposal_id)
    except KeyError:
        return {"status": MISSING, "message": "没有这个提议。", "job": None,
                "estimate": analysis_average(jobs)}
    if proposal["status"] == "dismissed":
        return {"status": DISMISSED, "message": "这个提议已经被忽略，不会再生成。",
                "job": None, "estimate": analysis_average(jobs)}
    if proposal["status"] == "confirmed":
        return {"status": CONFLICT, "message": "这个提议已经确认过了，没有重复创建任务。",
                "job": None, "estimate": analysis_average(jobs)}
    symbol = str(ticker).upper()
    if symbol not in {str(item).upper() for item in known_tickers}:
        return {"status": UNKNOWN_TICKER,
                "message": "数据库里没有这家公司，无法分析。", "job": None,
                "estimate": analysis_average(jobs)}
    requested = horizon or proposal["horizon"]
    if requested not in HORIZON_LABELS:
        return {"status": MISSING, "message": "周期只能是 short、mid 或 long。",
                "job": None, "estimate": analysis_average(jobs)}
    as_of = _analysis_date(price_db, symbol)
    parameters = {"ticker": symbol, "horizon": requested, "as_of": as_of}
    job = jobs.create(KIND_ANALYZE, parameters)
    if chat.confirm_proposal(proposal_id, job_id=job["job_id"]) is None:
        # Another request won the race; the job it created is the real one, so
        # this one must not stay queued and spend money twice.
        jobs.finish(job["job_id"], status="failed",
                    error="该提议已被另一个请求确认，这个重复任务没有运行。")
        return {"status": CONFLICT, "message": "这个提议已经确认过了，没有重复创建任务。",
                "job": None, "estimate": analysis_average(jobs)}
    return {"status": CONFIRMED,
            "message": (f"已创建分析任务：{HORIZON_LABELS[requested]}，分析截至 {as_of}。"
                        "任务页可以看到进度。"),
            "job": job, "estimate": analysis_average(jobs)}


def _analysis_date(price_db, ticker: str) -> str:
    """The newest stored price day, so the analysis can actually pass D03."""
    from datetime import date

    bounds = store_data.price_bounds(price_db).get(ticker.upper())
    return bounds["end_date"] if bounds else date.today().isoformat()


def card_summary(card_db, card_id: str) -> dict:
    """A new card's headline fields, read from the archive.  No model call."""
    archived = read_card(card_db, card_id)
    card = archived["card"]
    bounds = card.get("entry_range") if isinstance(card.get("entry_range"), list) else None
    entry_text = None
    if bounds and len(bounds) == 2 and all(item is not None for item in bounds):
        entry_text = (f"{display_text(bounds[0], 'USD/share', name='close')} – "
                      f"{display_text(bounds[1], 'USD/share', name='close')}")
    return {
        "card_id": card_id,
        "card_id_short": card_id[:8],
        "ticker": card["ticker"],
        "as_of": card["as_of"],
        "action": card.get("action"),
        "tendency": card.get("bias"),
        "horizon": card.get("horizon"),
        "confidence": card.get("confidence"),
        "created_at": archived.get("created_at"),
        "entry_text": entry_text,
        "stop_text": display_text(card.get("stop_loss"), "USD/share", name="close"),
        "target_text": display_text(card.get("target_price"), "USD/share", name="close"),
        "disclaimer": card.get("disclaimer"),
    }


def note_finished_analysis(chat: ChatStore, jobs: JobStore, conversation_id: str, *,
                           card_db) -> dict | None:
    """Append one system message for a finished proposal; ``None`` if not yet.

    Announcing once is what keeps the conversation honest: the message exists
    only after the job it refers to reached a terminal state, and a proposal
    that already has a card never produces a second message.
    """
    for proposal in chat.proposals_for(conversation_id):
        if proposal["status"] != "confirmed" or not proposal["job_id"]:
            continue
        if proposal["card_id"] is not None:
            continue
        job = jobs.get(proposal["job_id"])
        if job is None or job["status"] not in {"succeeded", "failed", "interrupted"}:
            continue
        return _announce(chat, conversation_id, proposal, job, card_db=card_db)
    return None


def _announce(chat: ChatStore, conversation_id: str, proposal: dict, job: dict, *,
              card_db) -> dict:
    if job["status"] == "succeeded" and (job.get("result") or {}).get("card_id"):
        card_id = job["result"]["card_id"]
        summary = card_summary(card_db, card_id)
        chat.attach_card(proposal["proposal_id"], card_id)
        text = summary_text(summary)
        segments: list[dict[str, Any]] = [
            {"type": "text", "value": "建议卡已生成。"},
            {"type": "card_link", "card_id": card_id,
             "card_id_short": card_id[:8], "ticker": summary["ticker"],
             "action": summary["action"], "tendency": summary["tendency"],
             "horizon": summary["horizon"], "created_at": summary["created_at"],
             "entry_text": summary["entry_text"], "stop_text": summary["stop_text"],
             "target_text": summary["target_text"], "as_of": summary["as_of"]},
        ]
    else:
        reason = job.get("error") or "任务没有成功"
        text = f"这次没有生成建议卡：{reason}。可以在任务页看到具体进度。"
        segments = [{"type": "text", "value": text}]
    return chat.append_message(conversation_id, role="system", text=text,
                               segments=segments,
                               proposal_id=proposal["proposal_id"])


def summary_text(summary: dict) -> str:
    """The one-line summary a finished card gets, from its own fields."""
    parts = [f"建议卡已生成：{summary['horizon']}｜{summary['tendency']}｜"
             f"{summary['action']}。"]
    if summary["entry_text"]:
        parts.append(f"买点 {summary['entry_text']}。")
    if summary["stop_text"]:
        parts.append(f"止损 {summary['stop_text']}。")
    if summary["target_text"]:
        parts.append(f"目标 {summary['target_text']}。")
    return "".join(parts)
