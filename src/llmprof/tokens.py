"""Tokenization and per-component attribution of an LLM request.

This is the heart of the profiler: instead of one total, we break a request's
prompt tokens into the components that make it up (system prompt, tool schemas,
RAG/history, current input) so you can see what is actually eating the context
window. Counts are tiktoken-based and approximate for non-OpenAI models.
"""

from __future__ import annotations

import json
from functools import lru_cache

import tiktoken

# rough per-message overhead OpenAI adds for chat formatting (role markers etc.)
_PER_MESSAGE_OVERHEAD = 4
_REPLY_PRIMING = 3

_ROLE_LABELS = {
    "system": "system prompt",
    "user": "user input",
    "assistant": "history (assistant)",
    "tool": "tool results",
    "function": "tool results",
}


@lru_cache(maxsize=32)
def _encoding(model: str):
    try:
        return tiktoken.encoding_for_model(model)
    except Exception:
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, model: str = "gpt-4o") -> int:
    if not text:
        return 0
    return len(_encoding(model).encode(text))


def _message_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # multimodal content blocks
        return " ".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return ""


def attribute(messages: list[dict] | None, tools=None, model: str = "gpt-4o") -> dict:
    """Break a chat request into token components.

    Returns {"components": {label: tokens}, "total": int, "model": str,
    "approximate": bool}. Components sum (with priming) to total.
    """
    enc = _encoding(model)
    components: dict[str, int] = {}

    def add(label: str, toks: int) -> None:
        components[label] = components.get(label, 0) + toks

    for message in messages or []:
        role = message.get("role", "user")
        toks = len(enc.encode(_message_text(message))) + _PER_MESSAGE_OVERHEAD
        add(_ROLE_LABELS.get(role, role), toks)

    if tools:
        # tool/function schemas are sent verbatim and are a common source of bloat
        add("tool schemas", len(enc.encode(json.dumps(tools, ensure_ascii=False))))

    total = sum(components.values()) + _REPLY_PRIMING
    return {
        "components": components,
        "total": total,
        "model": model,
        "approximate": True,
    }
