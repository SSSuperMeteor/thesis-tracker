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
from thesis_tracker.decision.evidence import (
    RESOLUTIONS,
    TOOL_HISTORY_FIELDS,
    compact_tool_response,
    history_view,
    prepare_evidence,
)
from thesis_tracker.financial.tool import get_fundamental_metrics
from thesis_tracker.indicator_tool import get_indicators
from thesis_tracker.prices import get_price_history

MODEL = "deepseek-flash"
PROMPT_VERSION = "decision-agent-v3-tool-contract-2026-10-04"
MAX_TOOL_CALLS = 12
MAX_ROUNDS = 16
MAX_TOTAL_TOKENS = 1_500_000
SOFT_TOTAL_TOKENS = 1_000_000
MAX_REQUEST_INPUT_TOKENS = 200_000
MAX_REVISIONS = 2
# Tiered history is only served for the target ticker; SPY exists on the default
# latest page only.  Every model-visible text that names SPY states this limit
# (the Chinese system prompt words the same restriction inline).
SPY_NOTE_EN = "SPY is only available on the default latest page; tiered history requires the target ticker."

SYSTEM_PROMPT = """你是 Decision Mode 研究判断模型。只使用基础包或工具返回中有 fact_id 的事实。
你的立场必须明确：给出看多/中性/看空、买入/分批/持有/减仓/回避、具体买点、止损和目标。
减仓或回避时这些多头价位字段填 null，明确给出收盘价突破或跌破的失效阈值。
事实数值只能来自工具。JSON 草稿只列 fact_id，不写事实表、数据缺口或免责声明；
Python 会按 fact_id 填值。理由和失效条件文字引用事实数字只能写 {fact:<完整 fact_id>}。
除 YYYY-MM-DD 日期、Q1-Q4/2026-Q3 财期及“3 个季度”类时间计数外，文字不写裸数字。
每条理由至少列一个 fact_id。必须有 kind=close_below 或 close_above 且 price>0 的失效条件。
不要隐瞒 unavailable、not_applicable 或 null；Python 自动把它们写进数据缺口。
工具 as_of 由程序固定，永远不要在工具参数中传 as_of。可调用目标股票与 SPY（SPY 仅可用于默认最新页；分档历史只支持目标标的）。
基础包已提供最新值和关键地标；历史按 resolution 与 fields 请求，只引用所见 fact_id。
先选周期：短期优先 weekly_3m，中期优先 monthly_2y，长期优先 quarterly_5y；可按需加档。
只输出一个 JSON 对象，不要 markdown。schema 示例：
{"ticker":"目标标的","as_of":"分析日期","horizon":"中期","bias":"看多",
"action":"买入","confidence":"中","entry_range":null,"stop_loss":null,
"target_price":null,"fact_ids":["真实编号"],"reasons":[{"text":"观察 {fact:真实编号}",
"fact_ids":["真实编号"]}],"invalidations":[{"kind":"close_below","price":null,
"text":"收盘价跌破止损位"}]}。
周期只能短期/中期/长期；置信度只能低/中/高，不写百分比。
价格必须符合动作的确定性规则；请先读取工具结果再判断。"""


def _tool_schema(name: str, description: str, *, history: bool = True) -> dict:
    properties = {"ticker": {"type": "string",
                             "description": f"Ticker symbol to inspect. {SPY_NOTE_EN}"}}
    if history:
        fields = list(TOOL_HISTORY_FIELDS[name])
        properties.update({
            "resolution": {"type": "string", "enum": list(RESOLUTIONS),
                           "description": "Tiered history window; required for historical series."},
            "fields": {"type": "array", "items": {"type": "string", "enum": fields},
                       "maxItems": len(fields),
                       "description": f"Only requested core historical fields; {name} supports: {', '.join(fields)}."},
        })
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "additionalProperties": False,
                           "properties": properties, "required": ["ticker"]}}}


TOOL_SCHEMAS = [
    _tool_schema("get_price_history", "Read local latest price or tiered price history with resolution and fields."),
    _tool_schema("get_indicators", "Read local latest adjusted indicators or tiered core indicator history."),
    _tool_schema("get_fundamental_metrics", "Read eight local point-in-time SEC financial metrics and their unavailable reasons.", history=False),
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
    forbidden = {"facts", "gaps", "disclaimer", "confidence_calibration", "creation_price", "evidence_windows"}
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
            "fact_id": None, "reason": {"code": code, "message": message},
            "truncated": False, "truncation_reason": None}


def _dispatch(name: str, raw_arguments: str, ticker: str, as_of: str,
              snapshot: dict) -> tuple[dict | None, dict | None]:
    if name not in _TOOLS:
        return None, _tool_error("unknown_tool",
                                 "只允许 get_price_history、get_indicators、get_fundamental_metrics 三个本地数据工具。")
    try:
        given = json.loads(raw_arguments)
    except (json.JSONDecodeError, TypeError):
        return None, _tool_error("invalid_tool_arguments",
                                 '工具参数不是有效 JSON 对象；请传例如 {"ticker": "AAPL"}。')
    if not isinstance(given, dict):
        return None, _tool_error("invalid_tool_arguments",
                                 '工具参数必须是 JSON 对象；请传例如 {"ticker": "AAPL"}。')
    if "as_of" in given:
        return None, _tool_error("as_of_forbidden", "as_of 由程序固定，工具参数中不能指定；请去掉 as_of。")
    unknown = sorted(set(given) - {"ticker", "limit", "full_history", "end_date", "resolution", "fields"})
    if unknown:
        return None, _tool_error("invalid_tool_arguments",
                                 f"工具参数含未允许的字段：{', '.join(unknown)}；"
                                 "允许的字段：ticker, resolution, fields。")
    symbol = given.get("ticker")
    if not isinstance(symbol, str) or symbol.upper() not in {ticker, "SPY"}:
        return None, _tool_error("invalid_ticker", f"只能查询目标标的 {ticker} 或 SPY。")
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
            return None, _tool_error("invalid_tool_arguments",
                                     f"end_date 必须是不晚于 as_of（{as_of}）的日期。")
    resolution = given.get("resolution")
    fields = given.get("fields")
    if resolution is None and fields is not None:
        return None, _tool_error("invalid_tool_arguments",
                                 "fields 需要同时指定 resolution；请补上 resolution，或去掉 fields。")
    if resolution is not None:
        if name == "get_fundamental_metrics":
            return None, _tool_error("invalid_tool_arguments",
                                     "get_fundamental_metrics 不支持分档历史参数 resolution 和 fields，"
                                     "它只返回最新一期及可得同比。")
        if symbol != ticker:
            return None, _tool_error("invalid_tool_arguments",
                                     f"分档历史只支持目标标的 {ticker}；SPY 仅可用于默认最新页。")
        if end_date is not None or full_history or limit is not None:
            return None, _tool_error("invalid_tool_arguments",
                                     "分档历史只接受 ticker、resolution 和 fields；"
                                     "请去掉 limit、full_history、end_date。")
        allowed = list(TOOL_HISTORY_FIELDS[name])
        if not isinstance(resolution, str) or resolution not in RESOLUTIONS:
            return None, _tool_error("invalid_tool_arguments",
                                     f"resolution 无效；可选值：{', '.join(RESOLUTIONS)}。")
        if not isinstance(fields, list) or not fields or not all(isinstance(item, str) for item in fields):
            if fields is None:
                return None, _tool_error("invalid_tool_arguments",
                                         f"resolution 需要同时指定 fields；{name} 支持：{', '.join(allowed)}。")
            return None, _tool_error("invalid_tool_arguments",
                                     f"fields 必须是非空字符串数组；{name} 支持：{', '.join(allowed)}。")
        unsupported = [item for item in fields if item not in allowed]
        if unsupported:
            return None, _tool_error("invalid_tool_arguments",
                                     f"字段 {', '.join(unsupported)} 不适用于 {name}；"
                                     f"{name} 支持：{', '.join(allowed)}。")
        try:
            envelope = history_view(snapshot, name, resolution, fields)
        except ValueError:
            return None, _tool_error("invalid_tool_arguments",
                                     f"history 请求无效；{name} 支持 resolution "
                                     f"{', '.join(RESOLUTIONS)} 与字段 {', '.join(allowed)}。")
        return {"tool": name, "args": {"ticker": symbol, "as_of": as_of,
                "resolution": resolution, "fields": fields}, "envelope": envelope,
                "model_envelope": envelope, "view_only": True}, None
    if full_history:
        if name in TOOL_HISTORY_FIELDS:
            hint = (f"请指定 resolution（可选值：{', '.join(RESOLUTIONS)}）和 fields"
                    f"（{name} 支持：{', '.join(TOOL_HISTORY_FIELDS[name])}）请求分档历史。")
        else:
            hint = "该工具不支持分档历史；它只返回最新一期及可得同比。"
        return None, _tool_error("resolution_required", f"原始历史页不供模型读取；{hint}")
    key = "ticker" if name == "get_fundamental_metrics" else "symbol"
    args = {key: symbol, "as_of": as_of, "limit": limit,
            "full_history": full_history, "end_date": end_date}
    try:
        envelope = _TOOLS[name](symbol, as_of=as_of, limit=limit,
                                full_history=full_history, end_date=end_date)
    except (TypeError, ValueError, sqlite3.Error) as exc:
        return None, _tool_error("tool_failed", f"本地工具无法完成调用（{type(exc).__name__}）。")
    record = {"tool": name, "args": args, "envelope": envelope}
    try:
        record["model_envelope"] = compact_tool_response(record)
    except ValueError:
        return None, _tool_error("envelope_limit", "工具证据超过字节上限，请缩小请求。")
    return record, None


def run_analysis(ticker: str, as_of: str, *, client: Any,
                 archive_path: Path | str = DEFAULT_ARCHIVE) -> dict:
    """Run one bounded analysis. Only a validated card is archived and rendered."""
    ticker = ticker.upper()
    if not re.fullmatch(r"[A-Z0-9.-]+", ticker):
        raise ValueError("invalid ticker")
    date.fromisoformat(as_of)
    analysis_id = str(uuid.uuid4())
    snapshot, base, catalog = prepare_evidence(ticker, as_of)
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": json.dumps({"task": f"分析 {ticker}，as_of={as_of}。按需取历史，然后输出 JSON 建议卡。",
                                "base_pack": base, "catalog": catalog}, ensure_ascii=False)}]
    stats = {"rounds": 0, "tool_calls": 0, "revisions": 0, "input_tokens": 0,
             "output_tokens": 0, "cache_hit_tokens": 0, "model_calls": [], "tools": [],
             "tool_details": [], "prefetch_calls": len(snapshot["calls"]),
             "base_pack_bytes": len(json.dumps(base, ensure_ascii=False).encode()),
             "catalog_bytes": len(catalog.encode()), "gate_reason": None}
    last_violations: list[dict] = []
    _audit_connection(archive_path).close()

    def reject(reason: str, violations: list[dict] | None = None) -> dict:
        return {"status": "rejected", "reason": reason, "violations": violations or last_violations,
                "card": None, "card_id": None, "rendered": None, "snapshot": snapshot,
                "stats": stats, "analysis_id": analysis_id}

    if base.get("truncated"):
        return reject("base_envelope_limit", [_error("L03", "base_pack", base["truncation_reason"])])

    while stats["rounds"] < MAX_ROUNDS:
        remaining = MAX_TOTAL_TOKENS - stats["input_tokens"] - stats["output_tokens"]
        if remaining <= 0:
            return reject("token_limit", [_error("L03", "tokens", "累计 token 达到硬上限。")])
        # A UTF-8 byte is a conservative upper bound for one text token;
        # reserve extra space for chat framing before sending a request.
        outbound_bytes = len(json.dumps({"messages": messages, "tools": TOOL_SCHEMAS},
                                        ensure_ascii=False).encode("utf-8"))
        estimated_input = outbound_bytes + 2048
        if estimated_input > MAX_REQUEST_INPUT_TOKENS:
            stats["gate_reason"] = "estimated_request_input_over_200k"
            return reject("request_input_limit", [_error("L03", "messages", "本轮预估输入超过单请求上限；未发送请求。")])
        if estimated_input >= remaining:
            stats["gate_reason"] = "estimated_total_over_1_5m"
            return reject("token_limit", [_error("L03", "messages", "本轮预估输入超过累计硬上限；未发送请求。")])
        stats["rounds"] += 1
        try:
            response = client.complete(messages=messages,
                                       tools=TOOL_SCHEMAS if stats["input_tokens"] + stats["output_tokens"] <= SOFT_TOTAL_TOKENS else [],
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
            return reject("token_limit", [_error("L03", "tokens", "累计 token 超过硬上限。")])
        message = response.get("message") or {}
        assistant = {"role": "assistant", "content": message.get("content"),
                     "reasoning_content": message.get("reasoning_content"),
                     "tool_calls": message.get("tool_calls")}
        messages.append(assistant)
        calls = message.get("tool_calls")
        if calls is not None:
            if stats["input_tokens"] + stats["output_tokens"] > SOFT_TOTAL_TOKENS:
                return reject("token_soft_limit", [_error("L03", "tool_calls", "累计 token 超过软上限，不能再调用工具。")])
            if stats["tool_calls"] + len(calls) > MAX_TOOL_CALLS:
                return reject("tool_limit", [_error("L01", "tool_calls", "工具调用超过 12 次。")])
            for item in calls:
                stats["tool_calls"] += 1
                function = item.get("function") or {}
                record, error = _dispatch(function.get("name"), function.get("arguments"), ticker, as_of, snapshot)
                if record is not None:
                    if not record.get("view_only"):
                        snapshot["calls"].append({key: record[key] for key in ("tool", "args", "envelope")})
                        data_end = (record["envelope"].get("data") or {}).get("data_end_date")
                        facts = (record["model_envelope"].get("data") or {}).get("facts", [])
                        snapshot["evidence_windows"].append({
                            "tool": record["tool"], "resolution": "latest",
                            "window_start": data_end, "window_end": data_end,
                            "requested_rows": None, "displayed_rows": len(facts),
                            "fields": [fact["name"] for fact in facts]})
                    stats["tools"].append(record["tool"])
                    payload = record["model_envelope"]
                    audit_record = record
                else:
                    payload = error
                    try:
                        attempted = json.loads(function.get("arguments") or "{}")
                    except (json.JSONDecodeError, TypeError):
                        attempted = {"parse_error": True}
                    if not isinstance(attempted, dict):
                        attempted = {"parse_error": True}
                    audit_record = {"tool": str(function.get("name")), "args": attempted,
                                    "envelope": error}
                _append_tool_call(archive_path, analysis_id, stats["tool_calls"], audit_record)
                size = len(json.dumps(payload, ensure_ascii=False).encode())
                stats["tool_details"].append({"tool": function.get("name"),
                                               "args": audit_record["args"],
                                               "bytes": size, "status": payload["status"]})
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
