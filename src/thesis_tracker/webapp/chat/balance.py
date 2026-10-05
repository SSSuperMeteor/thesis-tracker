"""Read-only DeepSeek account balance.

Only the documented endpoint is used, and only from the server:

    GET https://api.deepseek.com/user/balance
    -> {"is_available": bool,
        "balance_infos": [{"currency", "total_balance", "granted_balance",
                           "topped_up_balance"}]}

The key is read from the environment here and never leaves this module: what
goes back to the page is the numbers and a currency, never a header or a key.
A failure is reported as a failure; it never blocks a chat turn.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

BALANCE_URL = "https://api.deepseek.com/user/balance"
TIMEOUT_SECONDS = 10
MISSING_KEY = "未配置 API key，无法查询余额"


def api_key() -> str | None:
    from thesis_tracker.config import load_settings

    return load_settings().deepseek_api_key


def fetch_balance(*, timeout: int = TIMEOUT_SECONDS) -> dict:
    """The account balances, or a report of why they could not be read."""
    key = api_key()
    if not key:
        return {"is_available": None, "balances": [], "error": MISSING_KEY}
    request = urllib.request.Request(
        BALANCE_URL, method="GET",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload: Any = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        # Deliberately not the exception text: an SDK error can quote headers.
        return {"is_available": None, "balances": [],
                "error": f"余额查询失败（{type(exc).__name__}）"}
    return parse_balance(payload)


def parse_balance(payload: Any) -> dict:
    """Pick the documented fields out of the response, ignoring anything else."""
    if not isinstance(payload, dict):
        return {"is_available": None, "balances": [], "error": "余额返回格式无法识别"}
    balances = []
    for item in payload.get("balance_infos") or []:
        if not isinstance(item, dict):
            continue
        balances.append({"currency": item.get("currency"),
                         "total_balance": item.get("total_balance"),
                         "granted_balance": item.get("granted_balance"),
                         "topped_up_balance": item.get("topped_up_balance")})
    return {"is_available": bool(payload.get("is_available")), "balances": balances,
            "error": None}
