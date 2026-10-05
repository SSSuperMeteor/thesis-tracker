"""Explicit real-data spot check for the local web app (writes nothing).

Run against an already-started ``uv run webapp``:

    uv run python scripts/webapp_spot_check.py --token <printed token> --port 8765

It compares the web responses with the read-only tools the command line uses and
with the archive's own renderer.  It never calls a model and never writes to any
database.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.request

from thesis_tracker.decision.core import (
    DEFAULT_ARCHIVE,
    PRICE_STALENESS_DAYS,
    read_card,
    render_card,
)
from thesis_tracker.decision.evidence import display_text
from thesis_tracker.financial.pit_store import DEFAULT_FACT_DB
from thesis_tracker.financial.tool import get_fundamental_metrics
from thesis_tracker.prices import DEFAULT_DB as DEFAULT_PRICE_DB
from thesis_tracker.prices import get_price_history

TICKERS = ("AAPL", "NVDA", "TSLA")
AS_OF = "2026-10-04"


def fetch(port: int, token: str, path: str) -> dict:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    request.add_header("Host", f"127.0.0.1:{port}")
    request.add_header("Cookie", f"dsh_token={token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def check(label: str, web, expected) -> bool:
    ok = web == expected
    print(f"{'OK  ' if ok else 'FAIL'} {label}\n     网页: {web!r}\n     工具: {expected!r}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", required=True)
    parser.add_argument("--as-of", default=AS_OF)
    args = parser.parse_args()
    failures = 0

    print("=" * 72)
    print("1. 总览：公司列表、财期分组、价格起止日、过期标记、卡数量")
    print("=" * 72)
    overview = fetch(args.port, args.token, "/api/overview")
    connection = sqlite3.connect(f"file:{DEFAULT_FACT_DB.resolve()}?mode=ro", uri=True)
    db_tickers = sorted({row[0] for row in connection.execute(
        "SELECT DISTINCT ticker FROM filing_snapshots")})
    quarter_pairs = {}
    for ticker, period_end, form in connection.execute(
            "SELECT ticker, period_end, form FROM filing_snapshots"):
        quarter = period_end[:4] + "Q" + str((int(period_end[5:7]) - 1) // 3 + 1)
        quarter_pairs.setdefault(ticker, set()).add(quarter)
    db_cards = {}
    cards = sqlite3.connect(f"file:{DEFAULT_ARCHIVE.resolve()}?mode=ro", uri=True)
    for ticker, count in cards.execute(
            "SELECT ticker, COUNT(*) FROM decision_cards GROUP BY ticker"):
        db_cards[ticker] = count
    cards.close()
    connection.close()

    web_tickers = sorted(overview["companies"])
    print(f"{'OK  ' if web_tickers == db_tickers else 'FAIL'} 公司列表一致")
    print(f"     网页 {len(web_tickers)} 家: {web_tickers}")
    print(f"     数据库 {len(db_tickers)} 家: {db_tickers}")
    failures += web_tickers != db_tickers

    mismatch = []
    for ticker in db_tickers:
        web = set(overview["companies"][ticker]["periods"])
        if web != quarter_pairs[ticker]:
            mismatch.append((ticker, sorted(web ^ quarter_pairs[ticker])))
    print(f"{'OK  ' if not mismatch else 'FAIL'} 每家公司覆盖到的日历季度与库内 period_end 一致")
    if mismatch:
        print(f"     差异: {mismatch}")
    failures += bool(mismatch)

    bad_cards = [(ticker, overview["companies"][ticker]["card_count"], db_cards.get(ticker, 0))
                 for ticker in db_tickers
                 if overview["companies"][ticker]["card_count"] != db_cards.get(ticker, 0)]
    print(f"{'OK  ' if not bad_cards else 'FAIL'} 建议卡数量与 cards.db 一致")
    if bad_cards:
        print(f"     差异: {bad_cards}")
    failures += bool(bad_cards)

    print()
    print("=" * 72)
    print(f"2. 公司页抽查（{', '.join(TICKERS)}）：指标最新值、价格起止日、卡数量")
    print("=" * 72)
    for ticker in TICKERS:
        page = fetch(args.port, args.token, f"/api/companies/{ticker}?as_of={args.as_of}")
        envelope = get_fundamental_metrics(ticker, as_of=args.as_of)
        tool_metrics = (envelope.get("data") or {}).get("metrics") or {}
        web_metrics = {item["name"]: item for item in page["fundamentals"]["metrics"]}
        bad = []
        for name, metric in tool_metrics.items():
            expected = (display_text(metric["value"], metric["unit"], name=name)
                        if metric["status"] == "ok" and metric["value"] is not None else None)
            if web_metrics[name]["display"] != expected:
                bad.append((name, web_metrics[name]["display"], expected))
        print(f"{'OK  ' if not bad else 'FAIL'} {ticker} 财务指标最新值逐项与 get_fundamental_metrics 一致"
              f"（{len(tool_metrics)} 项，其中 {sum(1 for m in tool_metrics.values() if m['status'] == 'ok')} 项可算）")
        if bad:
            print(f"     差异: {bad}")
        failures += bool(bad)

        price_envelope = get_price_history(ticker, as_of=args.as_of)
        if price_envelope["status"] == "ok":
            rows = price_envelope["data"]
            connection = sqlite3.connect(f"file:{DEFAULT_PRICE_DB.resolve()}?mode=ro", uri=True)
            expected_range = connection.execute(
                "SELECT MIN(date), MAX(date), COUNT(*) FROM daily_prices WHERE symbol=?",
                (ticker,)).fetchone()
            connection.close()
            ok = (page["price"]["start_date"] == expected_range[0]
                  and page["price"]["end_date"] == expected_range[1]
                  and page["price"]["rows"] == expected_range[2])
            print(f"{'OK  ' if ok else 'FAIL'} {ticker} 价格起止日与行数与 prices.db 一致"
                  f"（{page['price']['start_date']} 至 {page['price']['end_date']}，{page['price']['rows']} 行）")
            failures += not ok
            latest_expected = display_text(
                rows["latest_close"]["value"], "USD/share", name="close")
            ok = page["price"]["latest_close"] == latest_expected
            print(f"{'OK  ' if ok else 'FAIL'} {ticker} 最新收盘价与价格工具一致"
                  f"（{page['price']['latest_close']} / {latest_expected}）")
            failures += not ok
            age = page["price"]["age_days"]
            stale = age > PRICE_STALENESS_DAYS
            ok = page["price"]["stale"] == stale and page["stale_after_days"] == PRICE_STALENESS_DAYS
            print(f"{'OK  ' if ok else 'FAIL'} {ticker} 过期标记与 D03 阈值一致"
                  f"（落后 {age} 天，阈值 {PRICE_STALENESS_DAYS}，stale={page['price']['stale']}）")
            failures += not ok
        card_count = len(page["cards"])
        db_count = db_cards.get(ticker, 0)
        print(f"{'OK  ' if card_count == db_count else 'FAIL'} {ticker} 公司页建议卡数量 {card_count}，cards.db {db_count}")
        failures += card_count != db_count

    print()
    print("=" * 72)
    print("3. 卡详情与命令行渲染逐字节一致（抽查 cards.db 里最新的一张卡）")
    print("=" * 72)
    cards = []
    connection = sqlite3.connect(f"file:{DEFAULT_ARCHIVE.resolve()}?mode=ro", uri=True)
    for row in connection.execute(
            "SELECT card_id, validator_version FROM decision_cards ORDER BY created_at DESC"):
        cards.append((row[0], row[1]))
    connection.close()
    checked = 0
    for card_id, version in cards[:3]:
        detail = fetch(args.port, args.token, f"/api/cards/{card_id}")
        archived = read_card(DEFAULT_ARCHIVE, card_id)
        rendered = render_card(archived["card"], json.loads(_snapshot(card_id)),
                               version=version)
        lines = rendered.split("\n")

        def join(segments):
            return "".join(item["value"] if item["type"] == "text" else item["display"]
                           for item in segments)

        ok = lines[0] == f"{detail['ticker']}｜{detail['as_of']}｜{detail['horizon']}"
        reasons = [item[2:] for item in _section(lines, "理由：")]
        ok = ok and [join(item) for item in detail["reasons"]] == reasons
        if detail["stop_rationale"]:
            ok = ok and join(detail["stop_rationale"]) == _line(lines, "止损依据：")[5:]
        checks = _section(lines, "失效条件：")
        for index, item in enumerate(detail["invalidations"]):
            ok = ok and join(item["machine_check"]) == checks[index * 2][len("- 机器检查："):]
            ok = ok and join(item["explanation"]) == checks[index * 2 + 1][len("  说明："):]
        ok = ok and [f"- {item['label']}: {item['text']}" for item in detail["auto_computed"]] == \
            _section(lines, "自动计算（Python）：")
        table = _section(lines, "事实表：")
        ok = ok and all(row == f"- {fact['label']}: {fact['display']} ({fact['fact_id']})"
                        for row, fact in zip(table, detail["facts"], strict=True))
        ok = ok and detail["disclaimer"] == lines[-1]
        print(f"{'OK  ' if ok else 'FAIL'} 卡 {card_id[:8]}（{version}，"
              f"{detail['bias']}/{detail['action']}）片段拼接与 render_card 逐字节一致")
        if not ok:
            print(f"     网页理由: {[join(item) for item in detail['reasons']]}")
            print(f"     渲染理由: {reasons}")
        failures += not ok
        checked += 1
        if checked == 1:
            print(f"     例：{detail['ticker']} {detail['as_of']}｜"
                  f"{detail['bias']}/{detail['action']}｜置信度 {detail['confidence']}"
                  f"（{detail['confidence_calibration']}）")
            print(f"     事实 {len(detail['facts'])} 条；自动计算 "
                  f"{[item['label'] + '=' + item['text'] for item in detail['auto_computed']]}")
            if detail["price_band"]:
                print("     价位带：" + "；".join(
                    f"{mark['label']} {mark['value']} @ {mark['position']}%"
                    for mark in detail["price_band"]["markers"]))
            else:
                print("     价位带：此动作没有价位")

    print()
    print("=" * 72)
    print("4. 说明")
    print("=" * 72)
    print("没有调用 DeepSeek，也没有任何真实 API 请求；本轮只做只读核对。")
    print(f"FAIL 计数：{failures}")
    return 1 if failures else 0


def _snapshot(card_id: str) -> str:
    connection = sqlite3.connect(f"file:{DEFAULT_ARCHIVE.resolve()}?mode=ro", uri=True)
    try:
        return connection.execute(
            "SELECT snapshot_json FROM decision_cards WHERE card_id=?", (card_id,)).fetchone()[0]
    finally:
        connection.close()


def _line(lines: list[str], prefix: str) -> str:
    matches = [item for item in lines if item.startswith(prefix)]
    assert len(matches) == 1, (prefix, matches)
    return matches[0]


def _section(lines: list[str], header: str) -> list[str]:
    start = lines.index(header)
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if not lines[index].startswith(("- ", "  ")):
            end = index
            break
    return lines[start + 1:end]


if __name__ == "__main__":
    raise SystemExit(main())
