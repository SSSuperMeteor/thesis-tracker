"""Per-company chat: storage, tools, validation and the bounded model loop.

The validation rules live here rather than in the decision validator because a
chat answer is a different object with a different contract: it may only state
numbers through placeholders, it may never issue a trade instruction of its own,
and every reference must resolve inside this conversation's own evidence.
"""

from __future__ import annotations

# Bumped whenever the system prompt or the validation rules change, so an
# archived answer can be traced to the rules that accepted it.  This module must
# stay import-free: the submodules import it, so anything here would be circular.
PROMPT_VERSION = "chat-v1-2026-10-05"
