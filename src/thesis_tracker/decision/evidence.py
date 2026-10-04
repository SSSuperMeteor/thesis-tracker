"""Compact, deterministic views over the existing read-only tool envelopes."""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from thesis_tracker.decision.core import capture_snapshot, data_gaps, fact_index
from thesis_tracker.financial.tool import get_fundamental_metrics
from thesis_tracker.indicator_tool import get_indicators
from thesis_tracker.prices import get_price_history

MAX_ENVELOPE_BYTES = 32 * 1024
RESOLUTIONS = ("daily_10", "weekly_3m", "monthly_2y", "quarterly_5y")

# The only fields each history-capable tool actually serves.  The model-facing
# tool schema, the catalog text and ``history_view`` all read this mapping, so
# the advertised enum cannot drift away from what dispatch accepts.
TOOL_HISTORY_FIELDS = {
    "get_price_history": ("close", "adjusted_close"),
    "get_indicators": ("rsi_14", "macd_histogram", "volume_ratio_20"),
}

# Full history-field vocabulary, derived from the per-tool mapping above.
HISTORY_FIELDS = tuple(field for fields in TOOL_HISTORY_FIELDS.values() for field in fields)


def history_field_help() -> str:
    """Describe the per-tool history fields for model-visible catalog text."""
    return "；".join(f"{tool} 支持字段: {', '.join(fields)}"
                     for tool, fields in TOOL_HISTORY_FIELDS.items())


def display_value(value: object, unit: str) -> str | None:
    """One display rule shared by model evidence and card rendering."""
    if value is None:
        return None
    number = Decimal(str(value))
    places = 2 if unit in {"USD/share", "percent", "percentage points"} else 0 if unit == "shares" else 4
    quantized = number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return f"{quantized:.{places}f}"


def _months_before(day: date, months: int) -> date:
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    month += 1
    for target in range(day.day, 27, -1):
        try:
            return date(year, month, target)
        except ValueError:
            pass
    return date(year, month, min(day.day, 28))


def bucket_dates(days: list[str], resolution: str, end: str) -> list[str]:
    """Use observed trading dates only; the last date in each calendar bucket wins."""
    if resolution not in RESOLUTIONS:
        raise ValueError("unknown resolution")
    cutoff = date.fromisoformat(end)
    observed = sorted({day for day in days if day <= end})
    if resolution == "daily_10":
        return observed[-10:]
    if resolution == "weekly_3m":
        start = _months_before(cutoff, 3)
    elif resolution == "monthly_2y":
        start = _months_before(cutoff, 24)
    else:
        start = _months_before(cutoff, 60)
    last: dict[object, str] = {}
    for day in observed:
        if day >= start.isoformat():
            if resolution == "weekly_3m":
                key = date.fromisoformat(day).isocalendar()[:2]
            elif resolution == "monthly_2y":
                key = day[:7]
            else:
                key = (day[:4], (int(day[5:7]) - 1) // 3)
            last[key] = day
    return list(last.values())[-30:]


def fit_envelope(envelope: dict, *, max_bytes: int = MAX_ENVELOPE_BYTES) -> dict:
    """Bound a model-facing envelope and announce every omitted row."""
    result = json.loads(json.dumps(envelope, ensure_ascii=False))
    result.setdefault("truncated", False)
    result.setdefault("truncation_reason", None)
    def encoded() -> int:
        return len(json.dumps(result, ensure_ascii=False).encode())
    rows = (result.get("data") or {}).get("rows")
    while encoded() > max_bytes and rows:
        rows.pop(0)
        result["truncated"] = True
        result["truncation_reason"] = "返回内容超过字节上限，较早的历史行已省略；请缩小字段或窗口。"
    if encoded() > max_bytes:
        return {"status": "error", "data": None, "truncated": True,
                "truncation_reason": "单条证据超过字节上限，无法安全展示；请缩小请求。",
                "reason": {"code": "envelope_limit", "message": "单条证据超过字节上限。"}}
    return result


def _indicator_definition(name: str) -> str:
    if name.startswith("sma_"):
        return "最近指定交易日复权收盘价的算术平均。"
    if name.startswith("ema_"):
        return "以前段简单均值为种子的复权收盘价指数移动平均。"
    if name == "rsi_14":
        return "按 Wilder 平滑的复权收盘价相对强弱指数。"
    if name.startswith("macd_"):
        return "由快慢指数移动平均及信号线得到的 MACD 分量。"
    if name.startswith("bollinger_"):
        return "由复权收盘价均值和总体标准差得到的布林带位置。"
    if name == "atr_14":
        return "按 Wilder 平滑的复权真实波幅。"
    if name in {"avg_volume_20", "volume_ratio_20"}:
        return "复权成交量相对最近交易日均量的水平或比值。"
    if name.startswith(("range_high_", "range_low_")):
        return "指定交易日窗口内复权最高价或最低价。"
    if name.startswith(("distance_to_high_", "distance_to_low_")):
        return "复权收盘价相对指定窗口高点或低点的百分比距离。"
    if name.startswith("relative_spy_"):
        return "标的与 SPY 对齐交易日后累计涨幅的百分点差。"
    raise ValueError("unknown indicator definition")


def _price_rows(snapshot: dict) -> list[dict]:
    rows = {}
    for call in snapshot["calls"]:
        if call["tool"] in {"get_price_history", "get_price_history_page"} and (
            call["args"].get("symbol") == snapshot["ticker"]
        ) and call["envelope"]["status"] == "ok":
            for row in call["envelope"]["data"]["rows"]:
                rows[row["date"]] = row
    return [rows[key] for key in sorted(rows)]


def recompute_derived(snapshot: dict) -> list[dict]:
    """Rebuild every derived value from source rows; stored copies have no authority."""
    rows = _price_rows(snapshot)
    if not rows:
        return []
    ticker = snapshot["ticker"]
    latest = rows[-1]
    current_close = Decimal(str(latest["close"]["value"]))
    facts = []

    def add(name: str, value: object, unit: str, day: str, formula: str, sources: list[str]) -> None:
        payload = [ticker, name, str(value), day, formula, sources]
        digest = hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()[:20]
        facts.append({"fact_id": f"derived|{ticker}|{name}|{day}|{digest}", "name": name,
                      "value": str(value), "display": display_value(value, unit),
                      "unit": unit, "date_or_period": day, "ticker": ticker,
                      "source": {"formula": formula, "source_fact_ids": sources}})

    for row in rows:
        adjusted = row.get("adjusted_close")
        if adjusted and adjusted["value"] is not None:
            add("adjusted_close", adjusted["value"], "USD/share", row["date"],
                "source row adjusted_close", [row["fact_id"]])
    end = date.fromisoformat(latest["date"])
    extrema = {}
    for label, start in (("52w", end - timedelta(days=364)),
                         ("3y", _months_before(end, 36)), ("5y", _months_before(end, 60))):
        subset = [row for row in rows if row["date"] >= start.isoformat()]
        if not subset or date.fromisoformat(subset[0]["date"]) > start + timedelta(days=7):
            continue
        for direction, field, picker in (("high", "high", max), ("low", "low", min)):
            chosen = picker(subset, key=lambda row: Decimal(str(row[field]["value"])))
            value = chosen[field]["value"]
            name = f"{direction}_{label}"
            formula = f"{direction}({field}) over [{start.isoformat()},{latest['date']}]"
            add(name, value, "USD/share", chosen["date"], formula, [chosen["fact_id"]])
            extrema[name] = Decimal(str(value))
    for label, months in (("1m", 1), ("3m", 3), ("6m", 6), ("1y", 12)):
        cutoff = _months_before(end, months).isoformat()
        previous = next((row for row in reversed(rows) if row["date"] <= cutoff), None)
        if previous is None or date.fromisoformat(previous["date"]) < date.fromisoformat(cutoff) - timedelta(days=7):
            continue
        prior_close = Decimal(str(previous["close"]["value"]))
        if prior_close == 0:
            continue
        value = (current_close / prior_close - 1) * 100
        add(f"return_{label}", value, "percent", latest["date"],
            f"(close[{latest['date']}]/close[{previous['date']}]-1)*100",
            [latest["fact_id"], previous["fact_id"]])
    if "high_52w" in extrema and "low_52w" in extrema and extrema["high_52w"] > extrema["low_52w"]:
        value = (current_close - extrema["low_52w"]) / (
            extrema["high_52w"] - extrema["low_52w"]) * 100
        add("position_52w", value, "percent", latest["date"],
            "(close-low_52w)/(high_52w-low_52w)*100",
            [latest["fact_id"], *[item["source"]["source_fact_ids"][0] for item in facts
                                  if item["name"] in {"high_52w", "low_52w"}]])
    return facts


def _shown(item: dict) -> dict:
    return {"fact_id": item["fact_id"], "name": item["name"],
            "display": display_value(item["value"], item["unit"]), "unit": item["unit"],
            "date_or_period": item["date_or_period"]}


def _record(snapshot: dict, tool: str, args: dict, envelope: dict) -> None:
    snapshot["calls"].append({"tool": tool, "args": args, "envelope": envelope})


def prepare_evidence(ticker: str, as_of: str) -> tuple[dict, dict, str]:
    """Prefetch the three defaults, then source rows needed for landmarks and YoY."""
    snapshot = capture_snapshot(ticker, as_of)
    price = snapshot["calls"][0]["envelope"]
    if price["status"] == "ok":
        cursor = price["data"]["next_end_date"]
        earliest = _months_before(date.fromisoformat(price["data"]["data_end_date"]), 60).isoformat()
        while cursor and cursor >= earliest:
            envelope = get_price_history(ticker, as_of=as_of, full_history=True, end_date=cursor)
            _record(snapshot, "get_price_history_page", {"symbol": ticker, "as_of": as_of,
                    "full_history": True, "end_date": cursor}, envelope)
            if envelope["status"] != "ok":
                break
            cursor = envelope["data"]["next_end_date"]
    financial = get_fundamental_metrics(ticker, as_of=as_of, full_history=True)
    _record(snapshot, "get_fundamental_metrics_history", {"ticker": ticker, "as_of": as_of,
            "full_history": True}, financial)
    snapshot["derived_facts"] = recompute_derived(snapshot)
    snapshot["evidence_windows"] = [{"tool": "base_pack", "resolution": "latest_and_landmarks",
        "window_start": _price_rows(snapshot)[0]["date"] if _price_rows(snapshot) else None,
        "window_end": _price_rows(snapshot)[-1]["date"] if _price_rows(snapshot) else None,
        "requested_rows": None, "displayed_rows": None, "fields": None}]
    index, conflicts = fact_index(snapshot)
    if conflicts:
        raise ValueError("conflicting tool facts")
    latest_indicator = snapshot["calls"][1]["envelope"].get("data") or {}
    ids = [price.get("fact_id")]
    ids += [metric.get("fact_id") for metric in (latest_indicator.get("latest") or {}).get("values", {}).values()]
    fundamental = snapshot["calls"][2]["envelope"].get("data") or {}
    ids += [metric.get("fact_id") for metric in fundamental.get("metrics", {}).values()]
    for name in ("adjusted_close", "high_52w", "low_52w", "high_3y", "low_3y",
                 "high_5y", "low_5y", "position_52w", "return_1m", "return_3m", "return_6m", "return_1y"):
        matches = [item for item in snapshot["derived_facts"] if item["name"] == name]
        if matches:
            ids.append(matches[-1]["fact_id"])
    # Same fiscal quarter one year earlier is selected from the tool's reported periods.
    for name, current in fundamental.get("metrics", {}).items():
        if current.get("status") != "ok":
            continue
        current_end = date.fromisoformat(current["period_end"])
        candidates = [row["metrics"][name] for row in (financial.get("data") or {}).get("rows", [])
                      if name in row["metrics"] and row["metrics"][name].get("status") == "ok"
                      and row["metrics"][name].get("fiscal_period", "")[-2:] ==
                      current.get("fiscal_period", "")[-2:]
                      and 300 <= (current_end - date.fromisoformat(row["metrics"][name]["period_end"])).days <= 430]
        if candidates:
            ids.append(max(candidates, key=lambda item: item["period_end"])["fact_id"])
    shown = [_shown(index[fact_id]) for fact_id in dict.fromkeys(ids) if fact_id in index]
    base = {"ticker": ticker, "as_of": as_of, "facts": shown,
            "truncated": False, "truncation_reason": None,
            "gaps": data_gaps(snapshot)}
    if len(json.dumps(base, ensure_ascii=False).encode()) > MAX_ENVELOPE_BYTES:
        base = {"ticker": ticker, "as_of": as_of, "status": "error", "facts": [],
                "truncated": True, "truncation_reason": "基础包超过字节上限，本次分析未发送模型请求。"}
    snapshot["evidence_windows"][0]["displayed_rows"] = len(base["facts"])
    # Names and descriptions are metadata only; no observed values enter the catalog.
    indicators = (latest_indicator.get("latest") or {}).get("values", {})
    names = [f"{key}: {_indicator_definition(key)}" for key in indicators]
    metrics = [f"{key}: {item['definition']}" for key, item in fundamental.get("metrics", {}).items()]
    catalog = ("工具: get_price_history(close 为原始收盘价，adjusted_close 为复权收盘价); "
               "get_indicators(RSI/MACD/volume); get_fundamental_metrics(SEC ratios). "
               "价格/指标历史分辨率与约数: daily_10 4/6 KiB; weekly_3m 6/8 KiB; "
               "monthly_2y 9/14 KiB; quarterly_5y 8/12 KiB。财务工具只展示最新值，"
               "同比事实已在基础包；其它指标只支持最新值。" + history_field_help() +
               "。价格与指标定义: " + "; ".join(names) + "。财务定义: " + "; ".join(metrics))
    return snapshot, base, catalog


def compact_tool_response(record: dict) -> dict:
    """Project a default tool call to its identified latest facts."""
    envelope = record["envelope"]
    data = envelope.get("data") or {}
    tool = record["tool"]
    shown = []
    gaps = []
    if tool == "get_price_history" and envelope["status"] == "ok":
        close = data.get("latest_close")
        if close and close.get("value") is not None:
            shown.append(_shown({"fact_id": envelope["fact_id"], "name": "close",
                                "value": close["value"], "unit": close["unit"],
                                "date_or_period": data["data_end_date"]}))
    elif tool == "get_indicators" and envelope["status"] == "ok":
        for name, item in (data.get("latest") or {}).get("values", {}).items():
            if item.get("value") is not None:
                shown.append(_shown({"fact_id": item["fact_id"], "name": name,
                                    "value": item["value"], "unit": item["unit"],
                                    "date_or_period": item.get("date")}))
            else:
                gaps.append({"name": name, "reason": item.get("reason")})
    elif tool == "get_fundamental_metrics":
        for name, item in data.get("metrics", {}).items():
            if item.get("status") == "ok":
                shown.append(_shown({"fact_id": item["fact_id"], "name": name,
                                    "value": item["value"], "unit": item["unit"],
                                    "date_or_period": item["period_end"]}))
            else:
                gaps.append({"name": name, "reason": item.get("reason")})
    return fit_envelope({"status": envelope["status"], "as_of": envelope["as_of"],
                         "reason": envelope.get("reason"),
                         "data": {"facts": shown, "gaps": gaps}})


def history_view(snapshot: dict, tool: str, resolution: str, fields: list[str]) -> dict:
    if resolution not in RESOLUTIONS or tool not in {"get_price_history", "get_indicators"}:
        raise ValueError("unsupported history request")
    allowed = set(TOOL_HISTORY_FIELDS[tool])
    if not fields or set(fields) - allowed:
        raise ValueError("unsupported fields")
    prices = _price_rows(snapshot)
    if not prices:
        return {"status": "unavailable", "data": None, "truncated": False,
                "truncation_reason": None,
                "reason": {"code": "no_data", "message": "没有可用的本地价格历史。"}}
    days = bucket_dates([row["date"] for row in prices], resolution, prices[-1]["date"])
    index, _ = fact_index(snapshot)
    rows = []
    by_day = {row["date"]: row for row in prices}
    for day in days:
        row = by_day[day]
        facts = []
        gaps = []
        if tool == "get_price_history":
            if "close" in fields:
                facts.append(_shown(index[row["fact_id"]]))
            if "adjusted_close" in fields:
                match = next((item for item in snapshot["derived_facts"]
                              if item["name"] == "adjusted_close" and item["date_or_period"] == day), None)
                if match:
                    facts.append(_shown(match))
        else:
            envelope = get_indicators(snapshot["ticker"], as_of=snapshot["as_of"],
                                      full_history=True, limit=1, end_date=day)
            selected = (envelope.get("data") or {}).get("rows", [])
            if selected and selected[0]["date"] == day:
                compact = {**envelope, "data": {**envelope["data"], "rows": [{**selected[0],
                           "values": {key: selected[0]["values"][key] for key in fields}}]}}
                _record(snapshot, "get_indicators_history", {"symbol": snapshot["ticker"],
                        "as_of": snapshot["as_of"], "full_history": True, "limit": 1,
                        "end_date": day}, compact)
                for field in fields:
                    item = selected[0]["values"][field]
                    if item.get("value") is not None:
                        facts.append(_shown({"fact_id": item["fact_id"], "name": field,
                                            "value": item["value"], "unit": item["unit"],
                                            "date_or_period": day}))
                    else:
                        gaps.append({"name": field, "reason": item.get("reason")})
        rows.append({"date": day, "facts": facts, "gaps": gaps})
    result = fit_envelope({"status": "ok", "data": {"resolution": resolution,
                          "fields": fields, "rows": rows}})
    snapshot["evidence_windows"].append({"tool": tool, "resolution": resolution,
                                          "window_start": days[0] if days else None,
                                          "window_end": days[-1] if days else None,
                                          "requested_rows": len(days),
                                          "displayed_rows": len((result.get("data") or {}).get("rows", [])),
                                          "fields": fields})
    return result
