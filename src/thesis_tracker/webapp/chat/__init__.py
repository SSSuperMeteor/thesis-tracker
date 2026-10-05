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
# v2: the prompt's placeholders were written with doubled braces for str.format
# but filled with replace(), so the model copied "{{fact:...}}"; also the scale,
# card-rationale and new-card-price rules, after the first real-model run.
PROMPT_VERSION = "chat-v2-2026-10-05"
