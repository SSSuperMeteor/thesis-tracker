"""Bounded DeepSeek tool loop around the deterministic card validator."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from thesis_tracker.config import load_settings
from thesis_tracker.decision.core import (
    DEFAULT_ARCHIVE,
    _connect,
    append_card,
    build_card,
    render_card,
    validate_card,
)
from thesis_tracker.financial.tool import get_fundamental_metrics
from thesis_tracker.indicator_tool import get_indicators
from thesis_tracker.prices import get_price_history

MODEL = "deepseek-flash"
PROMPT_VERSION = "decision-agent-v1-2026-10-04"
MAX_TOOL_CALLS = 12
MAX_ROUNDS = 16
MAX_TOTAL_TOKENS = 60_000
MAX_REVISIONS = 2

SYSTEM_PROMPT = """你是 Decision Mode 研究判断模型。只使用三个本地工具的原始返回作为事实。
你的立场必须明确：给出看多/中性/看空、买入/分批/持有/减仓/回避、具体买点、止损和目标。
减仓或回避时这些多头价位字段填 null，明确给出收盘价突破或跌破的失效阈值。
事实数值只能来自工具。JSON 草稿只列 fact_id，不写事实表、数据缺口或免责声明；
Python 会按 fact_id 填值。理由和失效条件文字引用事实数字只能写 {fact:<完整 fact_id>}。
除 YYYY-MM-DD 日期、Q1-Q4/2026-Q3 财期及“3 个季度”类时间计数外，文字不写裸数字。
每条理由至少列一个 fact_id。必须有 kind=close_below 或 close_above 且 price>0 的失效条件。
不要隐瞒 unavailable、not_applicable 或 null；Python 自动把它们写进数据缺口。
工具 as_of 由程序固定，永远不要在工具参数中传 as_of。可调用目标股票与 SPY。
只输出一个 JSON 对象，不要 markdown。schema 示例：
{"ticker":"AAPL","as_of":"2026-10-04","horizon":"中期","bias":"看多", 
"action":"买入","confidence":"中","entry_range":[100,110],"stop_loss":90,
"target_price":130,"fact_ids":["真实编号"],"reasons":[{"text":"观察 {fact:真实编号}",
"fact_ids":["真实编号"]}],"invalidations":[{"kind":"close_below","price":90,
"text":"收盘价跌破止损位"}]}。
周期只能短期/中期/长期；置信度只能低/中/高，不写百分比。
价格必须符合动作的确定性规则；请先读取工具结果再判断。"""


def _tool_schema(name: str, description: str) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "additionalProperties": False,
                           "properties": {
                               "ticker": {"type": "string", "description": "Ticker symbol to inspect; use the target or SPY."},
                               "limit": {"type": "integer", "minimum": 1, "maximum": 100,
                                         "description": "Maximum rows on a page, 1 to 100."},
                               "full_history": {"type": "boolean", "description": "Return a history page instead of latest only."},
                               "end_date": {"type": "string", "format": "date",
                                            "description": "Optional last date on a history page, YYYY-MM-DD."},
                           }, "required": ["ticker"]}}}


TOOL_SCHEMAS = [
    _tool_schema("get_price_history", "Read local daily prices and original closing price. Use for the target or SPY."),
    _tool_schema("get_indicators", "Read local adjusted technical indicators, including RSI and moving averages."),
    _tool_schema("get_fundamental_metrics", "Read eight local point-in-time SEC financial metrics and their unavailable reasons."),
]

_TOOLS = {"get_price_history": get_price_history, "get_indicators": get_indicators,
          "get_fundamental_metrics": get_fundamental_metrics}


class DeepSeekClient:
    """Small adapter around the official OpenAI-compatible Chat API."""

    def __init__(self) -> None:
        from openai import OpenAI

        settings = load_settings()
        if not settings.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY is not configured")
        endpoint = urlparse(settings.deepseek_base_url)
        if (endpoint.scheme != "https" or endpoint.hostname != "api.deepseek.com" or
                endpoint.username or endpoint.password or endpoint.port not in {None, 443} or
                endpoint.path not in {"", "/"} or endpoint.query or endpoint.fragment):
            raise ValueError("DeepSeek endpoint must be https://api.deepseek.com")
        self._client = OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url,
                              max_retries=0)

    def complete(self, *, messages: list[dict], tools: list[dict], max_tokens: int) -> dict:
        response = self._client.chat.completions.create(
            model=MODEL, messages=messages, tools=tools, tool_choice="auto",
            response_format={"type": "json_object"}, max_tokens=max_tokens,
            reasoning_effort="high", extra_body={"thinking": {"type": "enabled"}},
        )
        message = response.choices[0].message
        tool_calls = None
        if message.tool_calls is not None:
            tool_calls = [{"id": item.id, "type": "function", "function": {
                "name": item.function.name, "arguments": item.function.arguments}}
                for item in message.tool_calls]
        usage = response.usage
        return {"model": response.model, "system_fingerprint": getattr(response, "system_fingerprint", None),
                "usage": {"prompt_tokens": getattr(usage, "prompt_tokens", None),
                          "completion_tokens": getattr(usage, "completion_tokens", None),
                          "prompt_cache_hit_tokens": getattr(usage, "prompt_cache_hit_tokens", None)},
                "message": {"role": "assistant", "content": message.content,
                            "reasoning_content": getattr(message, "reasoning_content", None),
                            "tool_calls": tool_calls},
                "finish_reason": response.choices[0].finish_reason}


def _audit_connection(path: Path | str) -> sqlite3.Connection:
    conn = _connect(path)
    conn.execute("""CREATE TABLE IF NOT EXISTS decision_attempts (
        attempt_id TEXT PRIMARY KEY, analysis_id TEXT NOT NULL, attempt_no INTEGER NOT NULL,
        ticker TEXT NOT NULL, as_of TEXT NOT NULL, raw_output TEXT NOT NULL,
        violations_json TEXT NOT NULL, passed INTEGER NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(analysis_id, attempt_no))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS decision_model_calls (
        model_call_id TEXT PRIMARY KEY, analysis_id TEXT NOT NULL, round_no INTEGER NOT NULL,
        requested_model TEXT NOT NULL, returned_model TEXT, fingerprint TEXT,
        input_tokens INTEGER, output_tokens INTEGER, cache_hit_tokens INTEGER,
        created_at TEXT NOT NULL, UNIQUE(analysis_id, round_no))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS decision_tool_calls (
        tool_call_id TEXT PRIMARY KEY, analysis_id TEXT NOT NULL, call_no INTEGER NOT NULL,
        tool_name TEXT NOT NULL, arguments_json TEXT NOT NULL, envelope_status TEXT NOT NULL,
        created_at TEXT NOT NULL, UNIQUE(analysis_id, call_no))""")
    for table in ("decision_attempts", "decision_model_calls", "decision_tool_calls"):
        for action in ("UPDATE", "DELETE"):
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()}
                BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable'); END""")
    conn.commit()
    return conn


def _append_attempt(path: Path | str, analysis_id: str, number: int, ticker: str, as_of: str,
                    raw: str, violations: list[dict], passed: bool) -> None:
    with _audit_connection(path) as conn:
        conn.execute("INSERT INTO decision_attempts VALUES (?,?,?,?,?,?,?,?,?)", (
            str(uuid.uuid4()), analysis_id, number, ticker, as_of, raw,
            json.dumps(violations, ensure_ascii=False), int(passed), datetime.now(timezone.utc).isoformat()))


def _append_model_call(path: Path | str, analysis_id: str, round_no: int, response: dict) -> None:
    usage = response.get("usage") or {}
    with _audit_connection(path) as conn:
        conn.execute("INSERT INTO decision_model_calls VALUES (?,?,?,?,?,?,?,?,?,?)", (
            str(uuid.uuid4()), analysis_id, round_no, MODEL, response.get("model"),
            response.get("system_fingerprint"), usage.get("prompt_tokens"),
            usage.get("completion_tokens"), usage.get("prompt_cache_hit_tokens"),
            datetime.now(timezone.utc).isoformat()))


def _append_tool_call(path: Path | str, analysis_id: str, call_no: int, record: dict) -> None:
    with _audit_connection(path) as conn:
        conn.execute("INSERT INTO decision_tool_calls VALUES (?,?,?,?,?,?,?)", (
            str(uuid.uuid4()), analysis_id, call_no, record["tool"],
            json.dumps(record["args"], ensure_ascii=False, sort_keys=True),
            record["envelope"].get("status", "error"), datetime.now(timezone.utc).isoformat()))


def _error(rule: str, location: str, message: str) -> dict:
    return {"rule": rule, "location": location, "message": message}


def _parse_draft(raw: str) -> tuple[dict | None, list[dict]]:
    try:
        draft = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None, [_error("D00", "model_output", "模型输出不是有效的 JSON 对象。")]
    if not isinstance(draft, dict):
        return None, [_error("D00", "model_output", "模型输出必须是 JSON 对象。")]
    errors = []
    forbidden = {"facts", "gaps", "disclaimer", "confidence_calibration", "creation_price"}
    for key in forbidden & draft.keys():
        errors.append(_error("D00", key, "这个字段由 Python 填写，模型不能提供。"))
    for key in ("ticker", "as_of", "horizon", "bias", "action", "confidence"):
        if not isinstance(draft.get(key), str):
            errors.append(_error("D00", key, "必须是文字字段。"))
    if not isinstance(draft.get("fact_ids"), list) or not all(isinstance(x, str) for x in draft["fact_ids"]):
        errors.append(_error("D00", "fact_ids", "必须是事实编号列表。"))
    reasons = draft.get("reasons")
    if not isinstance(reasons, list) or not all(isinstance(x, dict) and isinstance(x.get("text"), str)
                                                 and isinstance(x.get("fact_ids"), list)
                                                 and all(isinstance(v, str) for v in x["fact_ids"])
                                                 for x in reasons):
        errors.append(_error("D00", "reasons", "每条理由需要 text 和 fact_ids 列表。"))
    conditions = draft.get("invalidations")
    if not isinstance(conditions, list) or not all(isinstance(x, dict) and isinstance(x.get("kind"), str)
                                                    and isinstance(x.get("text"), str)
                                                    and (x.get("price") is None or isinstance(x.get("price"), (int, float)))
                                                    for x in conditions):
        errors.append(_error("D00", "invalidations", "每条失效条件需要 kind、price 和 text。"))
    entry = draft.get("entry_range")
    if entry is not None and (not isinstance(entry, list) or len(entry) != 2 or
                              not all(isinstance(x, (int, float)) for x in entry)):
        errors.append(_error("D00", "entry_range", "买点须是两个价位或 null。"))
    for key in ("stop_loss", "target_price"):
        if draft.get(key) is not None and not isinstance(draft[key], (int, float)):
            errors.append(_error("D00", key, "价位须是数字或 null。"))
    return (None if errors else draft), errors


def _tool_error(code: str, message: str) -> dict:
    return {"status": "error", "data": None, "source": None, "as_of": None,
            "fact_id": None, "reason": {"code": code, "message": message}}


def _dispatch(name: str, raw_arguments: str, ticker: str, as_of: str) -> tuple[dict | None, dict | None]:
    if name not in _TOOLS:
        return None, _tool_error("unknown_tool", "只允许三个已列出的本地数据工具。")
    try:
        given = json.loads(raw_arguments)
    except (json.JSONDecodeError, TypeError):
        return None, _tool_error("invalid_tool_arguments", "工具参数不是有效 JSON 对象。")
    if not isinstance(given, dict):
        return None, _tool_error("invalid_tool_arguments", "工具参数必须是 JSON 对象。")
    if "as_of" in given:
        return None, _tool_error("as_of_forbidden", "as_of 由程序固定，工具参数中不能指定。")
    if set(given) - {"ticker", "limit", "full_history", "end_date"}:
        return None, _tool_error("invalid_tool_arguments", "工具参数含未允许的字段。")
    symbol = given.get("ticker")
    if not isinstance(symbol, str) or symbol.upper() not in {ticker, "SPY"}:
        return None, _tool_error("invalid_ticker", "只能查询目标标的或 SPY。")
    symbol = symbol.upper()
    limit = given.get("limit")
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 100):
        return None, _tool_error("invalid_tool_arguments", "limit 必须是 1 到 100 的整数。")
    full_history = given.get("full_history", False)
    if type(full_history) is not bool:
        return None, _tool_error("invalid_tool_arguments", "full_history 必须为布尔值。")
    end_date = given.get("end_date")
    if end_date is not None:
        try:
            if not isinstance(end_date, str) or date.fromisoformat(end_date) > date.fromisoformat(as_of):
                raise ValueError
        except ValueError:
            return None, _tool_error("invalid_tool_arguments", "end_date 必须是不晚于 as_of 的日期。")
    key = "ticker" if name == "get_fundamental_metrics" else "symbol"
    args = {key: symbol, "as_of": as_of, "limit": limit,
            "full_history": full_history, "end_date": end_date}
    try:
        envelope = _TOOLS[name](symbol, as_of=as_of, limit=limit,
                                full_history=full_history, end_date=end_date)
    except (TypeError, ValueError, sqlite3.Error) as exc:
        return None, _tool_error("tool_failed", f"本地工具无法完成调用（{type(exc).__name__}）。")
    return {"tool": name, "args": args, "envelope": envelope}, None


def run_analysis(ticker: str, as_of: str, *, client: Any,
                 archive_path: Path | str = DEFAULT_ARCHIVE) -> dict:
    """Run one bounded analysis. Only a validated card is archived and rendered."""
    ticker = ticker.upper()
    if not re.fullmatch(r"[A-Z0-9.-]+", ticker):
        raise ValueError("invalid ticker")
    date.fromisoformat(as_of)
    analysis_id = str(uuid.uuid4())
    snapshot = {"ticker": ticker, "as_of": as_of, "calls": []}
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": f"分析 {ticker}，as_of={as_of}。先调用工具，再输出 JSON 建议卡。"}]
    stats = {"rounds": 0, "tool_calls": 0, "revisions": 0, "input_tokens": 0,
             "output_tokens": 0, "cache_hit_tokens": 0, "model_calls": [], "tools": []}
    last_violations: list[dict] = []
    _audit_connection(archive_path).close()

    def reject(reason: str, violations: list[dict] | None = None) -> dict:
        return {"status": "rejected", "reason": reason, "violations": violations or last_violations,
                "card": None, "card_id": None, "rendered": None, "snapshot": snapshot,
                "stats": stats, "analysis_id": analysis_id}

    while stats["rounds"] < MAX_ROUNDS:
        remaining = MAX_TOTAL_TOKENS - stats["input_tokens"] - stats["output_tokens"]
        if remaining <= 0:
            return reject("token_limit", [_error("L03", "tokens", "累计 token 达到 60000。")])
        # A UTF-8 byte is a conservative upper bound for one text token;
        # reserve extra space for chat framing before sending a request.
        outbound_bytes = len(json.dumps({"messages": messages, "tools": TOOL_SCHEMAS},
                                        ensure_ascii=False).encode("utf-8"))
        estimated_input = outbound_bytes + 2048
        if estimated_input >= remaining:
            return reject("token_limit", [_error("L03", "messages", "工具结果使下一轮输入超过 token 预算；未发送请求。")])
        stats["rounds"] += 1
        try:
            response = client.complete(messages=messages, tools=TOOL_SCHEMAS,
                                       max_tokens=min(8192, remaining - estimated_input))
        except Exception as exc:
            # External SDK errors may contain request headers; never persist their text.
            return reject("model_error", [_error("L00", "model", f"模型调用失败（{type(exc).__name__}）。")])
        _append_model_call(archive_path, analysis_id, stats["rounds"], response)
        usage = response.get("usage") or {}
        incoming, outgoing, cached = (usage.get("prompt_tokens"), usage.get("completion_tokens"),
                                      usage.get("prompt_cache_hit_tokens"))
        if any(type(value) is not int or value < 0 for value in (incoming, outgoing, cached)) or cached > incoming:
            return reject("usage_unavailable", [_error("L03", "usage", "模型未返回可核算的 token 用量。")])
        stats["input_tokens"] += incoming
        stats["output_tokens"] += outgoing
        stats["cache_hit_tokens"] += cached
        stats["model_calls"].append({"requested_model": MODEL, "returned_model": response.get("model"),
                                     "fingerprint": response.get("system_fingerprint"),
                                     "input_tokens": incoming, "output_tokens": outgoing,
                                     "cache_hit_tokens": cached})
        if stats["input_tokens"] + stats["output_tokens"] > MAX_TOTAL_TOKENS:
            return reject("token_limit", [_error("L03", "tokens", "累计 token 超过 60000。")])
        message = response.get("message") or {}
        assistant = {"role": "assistant", "content": message.get("content"),
                     "reasoning_content": message.get("reasoning_content"),
                     "tool_calls": message.get("tool_calls")}
        messages.append(assistant)
        calls = message.get("tool_calls")
        if calls is not None:
            if stats["tool_calls"] + len(calls) > MAX_TOOL_CALLS:
                return reject("tool_limit", [_error("L01", "tool_calls", "工具调用超过 12 次。")])
            for item in calls:
                stats["tool_calls"] += 1
                function = item.get("function") or {}
                record, error = _dispatch(function.get("name"), function.get("arguments"), ticker, as_of)
                if record is not None:
                    snapshot["calls"].append(record)
                    stats["tools"].append(record["tool"])
                    _append_tool_call(archive_path, analysis_id, stats["tool_calls"], record)
                    payload = record["envelope"]
                else:
                    payload = error
                messages.append({"role": "tool", "tool_call_id": item.get("id"),
                                 "content": json.dumps(payload, ensure_ascii=False)})
            continue
        raw = message.get("content") or ""
        draft, violations = _parse_draft(raw)
        card = None
        if draft is not None:
            try:
                card = build_card(draft, snapshot)
                violations = validate_card(card, snapshot)
            except (TypeError, ValueError, KeyError, AttributeError) as exc:
                violations = [_error("D00", "model_output", f"草稿结构无效（{type(exc).__name__}）。")]
        _append_attempt(archive_path, analysis_id, stats["revisions"] + 1, ticker, as_of,
                        raw, violations, not violations)
        if not violations and card is not None:
            meta = {"requested_model": MODEL, "returned_model": response.get("model"),
                    "fingerprint": response.get("system_fingerprint"), "prompt_version": PROMPT_VERSION,
                    "input_tokens": stats["input_tokens"], "output_tokens": stats["output_tokens"],
                    "cache_hit_tokens": stats["cache_hit_tokens"]}
            card_id = append_card(archive_path, card, snapshot, model=meta)
            return {"status": "passed", "reason": None, "violations": [], "card": card,
                    "card_id": card_id, "rendered": render_card(card, snapshot),
                    "snapshot": snapshot, "stats": stats, "analysis_id": analysis_id}
        last_violations = violations
        if stats["revisions"] >= MAX_REVISIONS:
            return reject("correction_limit")
        stats["revisions"] += 1
        details = [{"rule": item["rule"], "location": item["location"], "message": item["message"]}
                   for item in violations]
        messages.append({"role": "user", "content": "上次 JSON 草稿未通过 Python 校验，请修正后重新输出 JSON。违规项：" +
                         json.dumps(details, ensure_ascii=False)})
    return reject("round_limit", [_error("L02", "rounds", "对话轮数达到 16。")])
