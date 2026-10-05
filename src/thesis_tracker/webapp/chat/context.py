"""Assemble one chat round's context from the conversation.

Three things go to the model, and nothing else does:

* the system prompt (with the version the message record stores);
* a brief with the company code and the newest stored price day — no other
  company data is pushed at the model, it has to ask;
* the pinned card, when the conversation was opened from one.

History is sent in *template* form: a previous answer's ``{fact:...}``
placeholders are restored instead of the digits they rendered to.  That is what
keeps the model writing placeholders instead of drifting into typing numbers.
"""

from __future__ import annotations

from thesis_tracker.webapp.chat import PROMPT_VERSION

# How many past messages are sent.  Older ones stay archived and visible; they
# are simply no longer part of the prompt.
HISTORY_WINDOW = 12
HISTORY_NOTE = "较早的消息不再发给模型"

SYSTEM_PROMPT = """你是 thesis-tracker 本机工作台里针对一家公司的研究助手。
本次对话只讨论 {ticker}。你只能使用工具返回的数据和本对话读过的建议卡。

写作规则（Python 会在展示前机械校验，违规就退回重写）：
1. 任何数字都必须写成占位符：工具事实用 {{fact:<完整编号>}}，卡上的字段用
   {{card:<卡编号>:<字段>}}。日期（YYYY-MM-DD）、财期（Q1-Q4、2026-Q3）和
   “3 个季度”这类时间计数可以照写。用户自己刚说过的数字可以重复。
2. 不要在正文里直接写“买入”“分批”“持有”“减仓”“回避”“观望”这类动作词，
   也不要写它们的同义词（分步、加仓、卖出、清仓、离场）。要引用卡上的动作，
   就写 {{card:<卡编号>:action}}。你自己不做交易指令。
3. 只能引用本对话证据里出现过的编号：本对话工具返回过的事实、compare_facts 算出的
   派生事实、以及本对话读过的卡。引用别的对话或别的公司会被拒绝。
4. 需要算术（例如距离止损还有多远）就用 compare_facts，不要自己心算。
5. 不知道、算不出、数据过期，就直说，并说明缺什么。不要猜数字。
6. 用户问“现在能不能买、该怎么操作”时：如果本对话或公司已有建议卡，引用那张卡的
   结论和价位（用占位符）；如果你认为需要新的判断，调用 request_new_card 提出生成
   新卡，等用户确认。绝不要说卡已经生成。
7. 不要给新的买点、止损或目标价；那只能由建议卡给出。
8. 用中文，平实、简短。不要用 markdown 标题。回答正文放在 JSON 的 answer 字段里。
9. 工具里的 as_of 由程序注入，你不要传。ticker 只能用 {ticker} 或 SPY。

只输出一个 JSON 对象：{{"answer": "正文"}}。
系统提示词版本 {prompt_version}。"""


def system_prompt(ticker: str) -> str:
    return SYSTEM_PROMPT.replace("{ticker}", ticker.upper()).replace(
        "{prompt_version}", PROMPT_VERSION)


def company_brief(*, ticker: str, price_db) -> dict:
    """The only company data pushed at the model: code and newest price day."""
    from thesis_tracker.webapp import data as store

    bounds = store.price_bounds(price_db).get(ticker.upper())
    return {"company": {"ticker": ticker.upper(),
                        "latest_price_date": None if bounds is None else bounds["end_date"]}}


def pinned_card(*, card_db, card_id: str | None) -> dict | None:
    """The card a conversation was opened from, in the model's own terms."""
    if not card_id:
        return None
    from thesis_tracker.decision.core import read_card
    from thesis_tracker.webapp.chat.tools import CARD_PRICE_FIELDS, card_price_facts

    try:
        archived = read_card(card_db, card_id)
    except KeyError:
        return None
    card = archived["card"]
    fields = {fact["field"]: fact["display"] for fact in card_price_facts(
        card, card_id, created_at=archived.get("created_at"))}
    return {"card_id": card_id, "as_of": card["as_of"],
            "created_at": archived.get("created_at"),
            "action": card.get("action"), "tendency": card.get("bias"),
            "horizon": card.get("horizon"), "confidence": card.get("confidence"),
            "fields": {key: fields[key] for key in CARD_PRICE_FIELDS if key in fields},
            "note": "这是历史建议卡，是 AI 判断，不是事实；引用请用占位符。"}


def restore_template(message: dict) -> str:
    """A stored answer as the model should see it again: placeholders, not digits."""
    text = message.get("template_text")
    if text:
        return text
    # Messages with no template stored (a user's, or a system note) are sent as
    # they are.
    return message.get("text") or ""


def history_messages(store, conversation_id: str, *, window: int = HISTORY_WINDOW
                     ) -> tuple[list[dict], dict]:
    """Past messages for the prompt, plus what the page needs to explain them."""
    every = store.messages(conversation_id)
    # The current question is the last user message and is sent separately.
    past = every[:-1] if every and every[-1]["role"] == "user" else every
    chosen = past[-window:] if window else []
    messages = [{"role": item["role"], "content": restore_template(item)}
                for item in chosen if item["role"] in {"user", "assistant"}]
    return messages, {"total": len(past), "sent": len(messages),
                      "truncated": len(past) > len(messages), "note": HISTORY_NOTE}
