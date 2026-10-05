"""Token usage and cost, from an editable local price file.

Prices are never hardcoded in Python.  They live in ``data/webapp/pricing.json``
(source URL and the date they were read included), so a price change is a file
edit rather than a code change.  When that file is missing or incomplete the
app shows tokens and no money at all — a wrong amount is worse than none.

The seeded numbers are DeepSeek's published peak rates for ``deepseek-flash``
(https://api-docs.deepseek.com/quick_start/pricing/, read 2026-10-05): cache-hit
input $0.006, cache-miss input $0.30, output $1.20 per million tokens.  Off-peak
is half of peak, so the peak figures are the conservative estimate and the label
says so.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PRICING_PATH = Path("data/webapp/pricing.json")
# A committed copy of the same numbers, so a fresh clone can still show amounts
# before anyone has edited the local file.  The local file wins when it exists.
TEMPLATE_PRICING_PATH = Path(__file__).with_name("pricing.default.json")

PRICING_FIELDS = ("input_per_million", "output_per_million", "cache_hit_per_million")
# Shown next to every amount: the estimate is an upper bound, not a bill.
COST_CAVEAT = "按高峰价上界估算，实际可能更低"
MISSING_PRICING_NOTE = "未找到价格配置，只显示 token"


@dataclass(frozen=True)
class Pricing:
    """Per-million-token prices, plus where they came from."""

    input_per_million: float
    output_per_million: float
    cache_hit_per_million: float
    source_url: str | None = None
    as_of_date: str | None = None
    off_peak_input_per_million: float | None = None
    off_peak_output_per_million: float | None = None
    off_peak_cache_hit_per_million: float | None = None

    def estimate(self, *, input_tokens: int, output_tokens: int,
                 cache_hit_tokens: int = 0) -> float:
        """Cost in USD, counting cache hits at the cache-hit rate.

        A cache hit is part of the input tokens, so the miss count is the
        remainder; charging it twice would overstate the bill.
        """
        hits = max(0, min(cache_hit_tokens, input_tokens))
        misses = max(0, input_tokens - hits)
        return (misses * self.input_per_million
                + hits * self.cache_hit_per_million
                + output_tokens * self.output_per_million) / 1_000_000


def load_pricing(path: Path | str | None = None) -> Pricing | None:
    """Read the local price file, falling back to the committed template.

    Pass an explicit path (as the tests and the CLI do) to bypass the fallback.
    """
    if path is None:
        file = DEFAULT_PRICING_PATH
        if not file.exists():
            file = TEMPLATE_PRICING_PATH
    else:
        file = Path(path)
    if not file.exists():
        return None
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    numbers = {}
    for field in PRICING_FIELDS:
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        numbers[field] = float(value)
    return Pricing(
        input_per_million=numbers["input_per_million"],
        output_per_million=numbers["output_per_million"],
        cache_hit_per_million=numbers["cache_hit_per_million"],
        source_url=payload.get("source_url"),
        as_of_date=payload.get("as_of_date"),
        off_peak_input_per_million=payload.get("off_peak_input_per_million"),
        off_peak_output_per_million=payload.get("off_peak_output_per_million"),
        off_peak_cache_hit_per_million=payload.get("off_peak_cache_hit_per_million"))


def format_cost(amount: float | None) -> str | None:
    """An amount a reader can act on: ``约 $0.012（按高峰价上界估算…）``."""
    if amount is None:
        return None
    return f"约 ${amount:.3f}"


def cost_label(amount: float | None, pricing: Pricing | None) -> str | None:
    """The full amount sentence, or a note that only tokens are available."""
    if amount is None:
        return MISSING_PRICING_NOTE
    text = f"{format_cost(amount)}（{COST_CAVEAT}"
    if pricing is not None and pricing.as_of_date:
        text += f"，价格取自 {pricing.as_of_date}"
    return text + "）"


def price_note(pricing: Pricing | None) -> str:
    """One line explaining where the prices came from, or why there are none."""
    if pricing is None:
        return MISSING_PRICING_NOTE
    parts = [f"输入 ${pricing.input_per_million:g} / 缓存命中 "
             f"${pricing.cache_hit_per_million:g} / 输出 "
             f"${pricing.output_per_million:g}（每百万 token）"]
    if pricing.as_of_date:
        parts.append(f"取自 {pricing.as_of_date}")
    if pricing.off_peak_input_per_million is not None:
        parts.append("低峰价为高峰价的一半")
    return "；".join(parts)
