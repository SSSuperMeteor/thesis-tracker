"""Read-only comparison of the chat layer against the real local databases.

Nothing here calls a model, writes a file or needs the network.  It runs the
tools, validators and usage arithmetic over the real archive so the numbers the
page would show can be checked by hand:

    uv run python scripts/chat_spot_check.py [TICKER]

The four things it verifies are the four claims the chat round makes: a fact the
model cites resolves to a real stored value, the renderer's text is exactly the
concatenation of its segments, C01-C03 behave on the real evidence, and the
usage arithmetic reproduces the archived card's own token counts.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from thesis_tracker.decision.core import DEFAULT_ARCHIVE, read_card
from thesis_tracker.financial.pit_store import DEFAULT_FACT_DB
from thesis_tracker.prices import DEFAULT_DB as DEFAULT_PRICE_DB
from thesis_tracker.webapp.chat.render import render_answer, render_display
from thesis_tracker.webapp.chat.store import ChatStore
from thesis_tracker.webapp.chat.tools import CARD_PRICE_FIELDS, ToolBox
from thesis_tracker.webapp.chat.usage import usage_summary
from thesis_tracker.webapp.chat.validate import validate_answer
from thesis_tracker.webapp.data import price_bounds, tickers

AS_OF_MARGIN_DAYS = 0


def _reproduce_stop_distance(box, prices) -> str | None:
    """Rebuild the card's printed stop distance from card facts alone.

    The card prints ``(入场价 − 止损) / 入场价`` with 入场价 the buy range's
    midpoint.  Reaching that number with the tools is the point: if it could not
    be composed, a chat answer about the distance would have to invent it, which
    C01 forbids.  The chain is ratio → difference → product → pct_change, every
    step recorded with its own formula.
    """
    low, high = prices.get("entry_low"), prices.get("entry_high")
    stop = prices.get("stop_loss")
    if not (low and high and stop):
        return None
    span, error = box.call("compare_facts", {"a": high["fact_id"],
                                             "b": low["fact_id"], "op": "difference"})
    if error is not None:
        return None
    # Half the span, then the lower end plus it: the midpoint.
    half, error = box.call("compare_facts", {"a": span["data"]["fact"]["fact_id"],
                                             "op": "product", "scale": 0.5})
    if error is not None:
        return None
    midpoint, error = box.call("compare_facts", {"a": low["fact_id"],
                                                 "b": half["data"]["fact"]["fact_id"],
                                                 "op": "addition"})
    if error is not None:
        return None
    # pct_change divides by its second operand, so the card's own
    # (入场价 − 止损) / 入场价 is the *negation* of this; the same magnitude comes
    # out of midpoint / stop − 1, which is what the ratio below reports.  Whether
    # the sign is positive is a wording decision, not an arithmetic one.
    # pct_change divides by its *second* operand, and the card's denominator is
    # the midpoint, so the midpoint is passed as b; the magnitude is then the
    # card's own and only the sign differs (a drop measured from the entry).
    distance, error = box.call("compare_facts", {
        "a": stop["fact_id"], "b": midpoint["data"]["fact"]["fact_id"],
        "op": "pct_change"})
    if error is not None:
        return None
    ratio, error = box.call("compare_facts", {
        "a": midpoint["data"]["fact"]["fact_id"], "b": stop["fact_id"], "op": "ratio"})
    if error is not None:
        return None
    return (f"买点中点 {midpoint['data']['fact']['display']}，"
            f"中点/止损 {ratio['data']['fact']['display']}，"
            f"（止损−中点）/中点 = {distance['data']['fact']['display']}"
            "（pct_change 以第二个操作数为分母）")


def _printed_stop_distance(card_db, card_id: str) -> str | None:
    """The stop distance the archived card itself renders, if it has one."""
    from thesis_tracker.decision.core import auto_computed

    archived = read_card(card_db, card_id)
    for item in auto_computed(archived["card"], json.loads(archived["snapshot_json"])):
        if item["label"] == "止损距离":
            return item["text"]
    return None


def main(argv: list[str]) -> int:
    ticker = (argv[1] if len(argv) > 1 else "").upper()
    symbols = tickers(DEFAULT_FACT_DB)
    if not symbols:
        print("没有本地财报库；先在终端运行采集命令。")
        return 1
    ticker = ticker or symbols[0]
    if ticker not in symbols:
        print(f"本地财报库里没有 {ticker}；可用：{', '.join(symbols)}")
        return 1
    bounds = price_bounds(DEFAULT_PRICE_DB).get(ticker)
    if not bounds:
        print(f"{ticker} 没有价格数据。")
        return 1
    as_of = bounds["end_date"]
    print(f"公司 {ticker}｜价格数据截至 {as_of}｜财报库 {len(symbols)} 家公司")

    with tempfile.TemporaryDirectory() as work:
        store = ChatStore(Path(work) / "chat.db")
        conversation = store.create_conversation(ticker)
        box = ToolBox(store=store, conversation=conversation, as_of=as_of,
                      fact_db=DEFAULT_FACT_DB, price_db=DEFAULT_PRICE_DB,
                      card_db=DEFAULT_ARCHIVE)

        price, price_error = box.call("get_price_history", {"ticker": ticker})
        assert price_error is None, price_error
        close_id = price["fact_id"]
        close_fact = store.facts(conversation["conversation_id"])[close_id]
        print(f"价格工具：最新收盘价 {close_fact['display']}（{close_id}）")

        indicators, _ = box.call("get_indicators", {"ticker": ticker})
        metrics, _ = box.call("get_fundamental_metrics", {"ticker": ticker})
        cards = box.cards()
        print(f"指标 {len(indicators['data']['latest']['values'])} 项｜"
              f"财务指标 {sum(1 for m in metrics['data']['metrics'].values() if m['status'] == 'ok')} 项可算｜"
              f"建议卡 {len(cards)} 张")
        if not cards:
            print("这家公司还没有建议卡；跳过卡相关检查。")
            return 0

        card_id = cards[0]["card_id"]
        card_envelope, card_error = box.call("get_card", {"card_id": card_id})
        assert card_error is None, card_error
        prices = {item["field"]: item for item in card_envelope["data"]["prices"]}
        assert set(prices) == set(CARD_PRICE_FIELDS), set(CARD_PRICE_FIELDS) - set(prices)
        print(f"卡 {card_id[:8]}：动作 {prices['action']['display']}｜"
              f"止损 {prices['stop_loss']['display']}｜目标 {prices['target_price']['display']}")

        # The card prints (入场价 − 止损) / 入场价 with 入场价 the buy range's
        # midpoint.  compare_facts must be able to reproduce it from the card's
        # own fields, or "how far is the stop" would be a number the model has to
        # invent rather than one it can cite.
        arithmetic = _reproduce_stop_distance(box, prices)
        if arithmetic is None:
            print("compare_facts：这张卡的价位不足以复现止损距离。")
        else:
            printed = _printed_stop_distance(DEFAULT_ARCHIVE, card_id)
            # The card prints the magnitude as a positive percentage; compare the
            # number, whatever sign this chain happens to carry.
            printed_percent = printed.split("%")[0].strip() if printed else None
            agree = printed_percent is not None and printed_percent in arithmetic
            print(f"compare_facts：买点中点相对止损 {arithmetic}"
                  + (f"｜卡上印的是 {printed}" if printed else "")
                  + (f"｜{'一致' if agree else '不一致'}"))

        # A whole round's worth of behaviour, with no model involved.
        body = (f"最新收盘价 {{fact:{close_id}}}，"
                f"卡上动作 {{card:{card_id}:action}}，止损 {{card:{card_id}:stop_loss}}。")
        facts = box.facts()
        card_fields = {fact["field"]: fact["display"] for fact in facts.values()
                       if fact.get("card_id") == card_id}
        from thesis_tracker.webapp.chat.validate import Evidence

        evidence = Evidence(facts=facts,
                            cards={card_id: {"card_id": card_id, "fields": card_fields}},
                            latest_price_date=as_of)
        violations = validate_answer(body, evidence=evidence, user_text="最新价格和止损是多少？")
        assert violations == [], violations
        text, segments = render_answer(body, evidence=evidence)
        rebuilt = "".join(render_display(item) for item in segments)
        assert rebuilt == text, (rebuilt, text)
        print(f"渲染：{text}")
        print(f"片段 {len(segments)} 段，逐段拼接与整段文本逐字节相同")

        bad = [
            ("C01 裸数字", f"收盘价是 {close_fact['value']}。", "C01"),
            ("C02 动作词", "建议买入。", "C02"),
            ("C03 未知编号", "见 {fact:tiingo|ZZZZ|2001-01-01|daily}。", "C03"),
        ]
        for label, text_value, rule in bad:
            found = validate_answer(text_value, evidence=evidence, user_text="问题")
            assert rule in {item["rule"] for item in found}, (label, found)
            print(f"{label} 被拒绝：{found[0]['rule']} {found[0]['message'][:40]}…")

        # Usage arithmetic against the archived card's own token counts.
        archived = read_card(DEFAULT_ARCHIVE, card_id)
        message = store.append_message(conversation["conversation_id"], role="user",
                                       text="问题")
        store.append_model_call(message["message_id"], round_no=1,
                                requested_model=archived.get("requested_model"),
                                returned_model=archived.get("returned_model"),
                                fingerprint=archived.get("fingerprint"),
                                input_tokens=archived.get("input_tokens"),
                                output_tokens=archived.get("output_tokens"),
                                cache_hit_tokens=archived.get("cache_hit_tokens"))
        summary = usage_summary(store, conversation_id=conversation["conversation_id"],
                                message_id=message["message_id"], ticker=ticker,
                                pricing_path=Path("data/webapp/pricing.json"))
        level = summary["levels"]["message"]
        assert level["input_tokens"] == (archived.get("input_tokens") or 0)
        assert level["output_tokens"] == (archived.get("output_tokens") or 0)
        print(f"用量：卡 {card_id[:8]} 存档输入 {archived.get('input_tokens')} 输出 "
              f"{archived.get('output_tokens')} 缓存命中 {archived.get('cache_hit_tokens')}"
              f"｜页面显示 {level['cost_text']}")
        print(json.dumps({"ticker": ticker, "as_of": as_of, "facts": len(facts),
                          "evidence_groups": sorted({fact.get("category") or "market"
                                                     for fact in facts.values()})},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
