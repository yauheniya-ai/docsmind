"""Token counting. Exact via tiktoken when its encoding is available, otherwise
a conservative chars/4 estimate — budgets only need to be approximately right."""

from __future__ import annotations

import json
from functools import lru_cache

from docsmind.domain.agent import Message


@lru_cache(maxsize=1)
def _encoding():
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001
        # Offline or encoding file unavailable: callers fall back to an estimate.
        return None


def count_tokens(text: str) -> int:
    if not text:
        return 0
    encoding = _encoding()
    if encoding is None:
        return len(text) // 4 + 1
    return len(encoding.encode(text, disallowed_special=()))


def message_tokens(message: Message) -> int:
    total = count_tokens(message.content) + 4
    for call in message.tool_calls:
        total += count_tokens(call.name) + count_tokens(json.dumps(call.arguments))
    return total
