"""Tokenization and per-component attribution of an LLM request.

This is the heart of the profiler: instead of one total, we break a request's
prompt tokens into the components that make it up (system prompt, tool schemas,
RAG/history, current input) so you can see what is actually eating the context
window. Each attribute_* function returns both a flat `components` map (for list
views) and a `tree` (for the flame graph, with per-tool drill-down). Counts are
tiktoken-based and approximate for non-OpenAI models.
"""

from __future__ import annotations

import json
from functools import lru_cache

import tiktoken

# rough per-message overhead OpenAI adds for chat formatting (role markers etc.)
_PER_MESSAGE_OVERHEAD = 4
_REPLY_PRIMING = 3

_OPENAI_ROLE_LABELS = {
    "system": "system prompt",
    "user": "user input",
    "assistant": "history (assistant)",
    "tool": "tool results",
    "function": "tool results",
}

_ANTHROPIC_ROLE_LABELS = {
    "user": "user input",
    "assistant": "history (assistant)",
}


@lru_cache(maxsize=32)
def _encoding(model: str):
    try:
        return tiktoken.encoding_for_model(model)
    except Exception:
        # cl100k_base is a reasonable approximation for non-OpenAI models
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, model: str = "gpt-4o") -> int:
    if not text:
        return 0
    return len(_encoding(model).encode(text))


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _leaf(name: str, tokens: int) -> dict:
    return {"name": name, "tokens": tokens, "children": []}


def _assemble(nodes: list[dict], priming: int, model: str) -> dict:
    """Turn top-level component nodes into the standard attribute() result."""
    components = {n["name"]: n["tokens"] for n in nodes}
    prompt_total = sum(components.values())
    tree = {"name": "context", "tokens": prompt_total, "children": nodes}
    return {
        "components": components,
        "tree": tree,
        "total": prompt_total + priming,
        "model": model,
        "approximate": True,
    }


# --------------------------------------------------------------------------- #
# OpenAI chat completions
# --------------------------------------------------------------------------- #
def _openai_message_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # multimodal content blocks
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def attribute_openai(messages: list[dict] | None, tools=None, model: str = "gpt-4o") -> dict:
    """Break an OpenAI chat request into token components with a flame tree."""
    enc = _encoding(model)
    merged: dict[str, int] = {}
    order: list[str] = []

    def add(label: str, toks: int) -> None:
        if label not in merged:
            merged[label] = 0
            order.append(label)
        merged[label] += toks

    for message in messages or []:
        role = message.get("role", "user")
        toks = len(enc.encode(_openai_message_text(message))) + _PER_MESSAGE_OVERHEAD
        add(_OPENAI_ROLE_LABELS.get(role, role), toks)

    nodes = [_leaf(label, merged[label]) for label in order]

    if tools:
        children = []
        for tool in tools:
            t = tool if isinstance(tool, dict) else {}
            fn = t.get("function", t)
            name = fn.get("name") or t.get("name") or "tool"
            children.append(_leaf(name, len(enc.encode(_json(tool)))))
        nodes.append(
            {
                "name": "tool schemas",
                "tokens": sum(c["tokens"] for c in children),
                "children": children,
            }
        )

    return _assemble(nodes, _REPLY_PRIMING, model)


# --------------------------------------------------------------------------- #
# Anthropic messages
# --------------------------------------------------------------------------- #
def _anthropic_message_parts(message: dict) -> list[tuple[str, str]]:
    """Yield (component_label, text) for each block in an Anthropic message."""
    role = message.get("role", "user")
    role_label = _ANTHROPIC_ROLE_LABELS.get(role, role)
    content = message.get("content")
    parts: list[tuple[str, str]] = []

    if isinstance(content, str):
        parts.append((role_label, content))
        return parts

    for block in content or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append((role_label, block.get("text", "")))
        elif btype == "tool_use":
            parts.append(("tool calls", _json(block.get("input", {}))))
        elif btype == "tool_result":
            inner = block.get("content")
            if isinstance(inner, str):
                text = inner
            else:
                text = " ".join(b.get("text", "") for b in (inner or []) if isinstance(b, dict))
            parts.append(("tool results", text))
    return parts


def attribute_anthropic(
    system=None, messages: list[dict] | None = None, tools=None,
    model: str = "claude-3-5-sonnet",
) -> dict:
    """Break an Anthropic messages request into token components with a tree."""
    enc = _encoding(model)
    merged: dict[str, int] = {}
    order: list[str] = []

    def add(label: str, text: str) -> None:
        if not text:
            return
        if label not in merged:
            merged[label] = 0
            order.append(label)
        merged[label] += len(enc.encode(text))

    if system:
        sys_text = system if isinstance(system, str) else " ".join(
            b.get("text", "") for b in system if isinstance(b, dict)
        )
        add("system prompt", sys_text)

    for message in messages or []:
        for label, text in _anthropic_message_parts(message):
            add(label, text)

    nodes = [_leaf(label, merged[label]) for label in order]

    if tools:
        children = []
        for tool in tools:
            name = tool.get("name", "tool") if isinstance(tool, dict) else "tool"
            children.append(_leaf(name, len(enc.encode(_json(tool)))))
        nodes.append(
            {
                "name": "tool schemas",
                "tokens": sum(c["tokens"] for c in children),
                "children": children,
            }
        )

    return _assemble(nodes, 0, model)


# Back-compat alias (OpenAI was the first provider supported).
attribute = attribute_openai
