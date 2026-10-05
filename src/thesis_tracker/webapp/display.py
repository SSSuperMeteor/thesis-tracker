"""Shared display strings for the local web app.

Every string a page shows is produced here or in :mod:`webapp.service`; the
frontend only renders them.  This module owns the two rules that must be
identical everywhere:

* timestamp formatting — one function, one format, the machine's own timezone;
* job parameter labels and horizon names — one mapping, so a page never shows a
  raw ``as_of=`` or ``horizon=short``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone, tzinfo

# The timestamp format every page uses: minutes, no seconds, no zone suffix.
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M"

# Resolved once per process from the machine running the software.  Tests
# replace it with a fixed zone so assertions never depend on the host clock.
LOCAL_TIMEZONE: tzinfo | None = None

# Job parameter keys, in the order the task page lists them.
PARAMETER_LABELS = {
    "ticker": "公司",
    "horizon": "周期",
    "as_of": "分析截至",
    "conversation_id": "对话",
}
# Identifiers that mean nothing to a reader are never listed.
HIDDEN_PARAMETERS = frozenset({"message_id"})
# Identifiers worth naming are shown by their first eight characters.
SHORT_PARAMETERS = frozenset({"conversation_id"})

# Horizon keys are the command line's short/mid/long; the Chinese names already
# live in decision.evidence and are re-exported here so the backend has exactly
# one mapping.  Imported lazily to keep this module dependency-free.
def horizon_label(key):
    """Chinese name for a horizon key, or the key itself when unknown."""
    from thesis_tracker.decision.evidence import HORIZON_LABELS

    return HORIZON_LABELS.get(key, key)


def local_zone() -> tzinfo:
    """The timezone of the machine running the web app."""
    if LOCAL_TIMEZONE is not None:
        return LOCAL_TIMEZONE
    return datetime.now(timezone.utc).astimezone().tzinfo


def format_timestamp(value: str | None, *, zone: tzinfo | None = None) -> str | None:
    """Format a stored ISO-8601 timestamp as local ``YYYY-MM-DD HH:MM``.

    Stored timestamps are UTC (``+00:00``); they are converted to the machine's
    own timezone, which is what a person reading their own machine expects.
    A value that cannot be parsed is returned unchanged rather than hidden.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return str(value)
    if moment.tzinfo is None:
        # The archive always writes an offset; treating a naive value as UTC is
        # the only interpretation that cannot silently shift it.
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(zone or local_zone()).strftime(TIMESTAMP_FORMAT)


def format_date(value: str | None) -> str | None:
    """Pass a plain ``YYYY-MM-DD`` date through; it has no time to convert."""
    return value


def parameter_labels(parameters: dict) -> list[dict]:
    """Job parameters as labelled Chinese pairs, in a stable order."""
    ordered = [key for key in PARAMETER_LABELS if key in parameters]
    ordered += [key for key in sorted(parameters)
                if key not in PARAMETER_LABELS and key not in HIDDEN_PARAMETERS]
    items = []
    for key in ordered:
        value = parameters[key]
        if key == "horizon":
            value = horizon_label(value)
        elif key in SHORT_PARAMETERS and value is not None:
            value = str(value)[:8]
        items.append({"key": key, "label": PARAMETER_LABELS.get(key, key),
                      "value": "" if value is None else str(value)})
    return items


def parameter_summary(parameters: dict) -> str:
    """One line such as ``公司 NVDA｜周期 短期｜分析截至 2026-10-04``."""
    return "｜".join(f"{item['label']} {item['value']}"
                     for item in parameter_labels(parameters))


# Version strings are parsed, never guessed: a card's stored
# ``decision-agent-v5-entry-stop-rules-2026-10-04`` is prompt generation 5 and
# ``decision-validator-3`` is rule generation 3.
PROMPT_VERSION = re.compile(r"-v(\d+)")
VALIDATOR_VERSION = re.compile(r"-(\d+)$")


def _generation(value: str | None, pattern: re.Pattern[str]) -> str | None:
    if not value:
        return None
    match = pattern.search(str(value))
    return None if match is None else match.group(1)


def version_mark(prompt_version: str | None, validator_version: str | None) -> str:
    """Short label such as ``提示词 v5 / 校验 3``.

    Anything that cannot be parsed is shown in full rather than abbreviated, so
    a future version scheme degrades to readable text instead of a wrong number.
    """
    prompt = _generation(prompt_version, PROMPT_VERSION)
    validator = _generation(validator_version, VALIDATOR_VERSION)
    if prompt is None and validator is None:
        return " / ".join(item for item in (prompt_version, validator_version) if item) or "—"
    if prompt is None:
        return f"校验 {validator}（提示词 {prompt_version or '—'}）"
    if validator is None:
        return f"提示词 v{prompt}（校验 {validator_version or '—'}）"
    return f"提示词 v{prompt} / 校验 {validator}"


def is_current_rules(prompt_version: str | None, validator_version: str | None, *,
                     current_validator: str, current_prompt: str) -> bool:
    """True when the card was produced by the rules the code currently ships."""
    return (validator_version == current_validator and prompt_version == current_prompt)
