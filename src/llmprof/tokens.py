"""Tokenization and per-component attribution of an LLM request.

This is the heart of the profiler: instead of one total, we break a request's
prompt tokens into the components that make it up (system prompt, tool schemas,
RAG/history, current input) so you can see what is actually eating the context
window. Each attribute_* function returns both a flat `components` map (for list
views) and a `tree` (for the flame graph, with per-tool drill-down). Counts are
tiktoken-based and approximate for non-OpenAI models.
"""

from __future__ import annotations

import hashlib
import json
import re
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


# --------------------------------------------------------------------------- #
# Conversation fingerprinting (groups calls of one agent run into a session)
# --------------------------------------------------------------------------- #
def _fp(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:12]


def message_fingerprint(payload: dict | None, provider: str) -> list[str]:
    """Ordered per-message fingerprints for a request.

    Turn N+1 of a conversation sends turn N's messages plus a few new ones, so
    turn N's fingerprint is a prefix of turn N+1's. The store uses that prefix
    relationship to chain consecutive calls into one session (context creep),
    with no code change from the caller.
    """
    payload = payload or {}
    fps: list[str] = []
    if provider == "anthropic":
        system = payload.get("system")
        if system:
            text = system if isinstance(system, str) else " ".join(
                b.get("text", "") for b in system if isinstance(b, dict)
            )
            fps.append("s:" + _fp(text))
        for message in payload.get("messages") or []:
            role = message.get("role", "user")
            text = " ".join(t for _, t in _anthropic_message_parts(message))
            fps.append(role[:1] + ":" + _fp(text))
    else:
        for message in payload.get("messages") or []:
            role = message.get("role", "user")
            fps.append(role[:1] + ":" + _fp(_openai_message_text(message)))
    return fps


def responses_to_chat(payload: dict | None) -> dict:
    """Adapt an OpenAI Responses API request ({instructions, input, tools}) into
    the chat-completions shape ({messages, tools}) so the same attribution,
    fingerprint, and route logic applies. Codex and newer tools use this API."""
    payload = payload or {}
    messages: list[dict] = []
    instructions = payload.get("instructions")
    if instructions:
        messages.append({"role": "system", "content": instructions})

    inp = payload.get("input")
    if isinstance(inp, str):
        messages.append({"role": "user", "content": inp})
    elif isinstance(inp, list):
        for item in inp:
            if not isinstance(item, dict):
                continue
            itype = item.get("type")
            if itype == "function_call":
                messages.append({"role": "assistant", "content": item.get("arguments", "")})
                continue
            if itype == "function_call_output":
                messages.append({"role": "tool", "content": str(item.get("output", ""))})
                continue
            role = item.get("role", "user")
            content = item.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
            else:
                text = ""
            messages.append({"role": role, "content": text})

    tools = []
    for tool in payload.get("tools") or []:
        if isinstance(tool, dict) and tool.get("type") == "function" and "function" not in tool:
            # Responses tools are flat: {type, name, description, parameters}
            tools.append({"type": "function", "function": {
                "name": tool.get("name"), "description": tool.get("description", ""),
                "parameters": tool.get("parameters", {})}})
        else:
            tools.append(tool)
    return {"model": payload.get("model"), "messages": messages, "tools": tools}


def content_blocks(payload: dict | None, provider: str) -> list[str]:
    """Flat list of the request's content strings (messages + tool schemas), for
    duplicate detection. Each message and each tool schema is one block."""
    payload = payload or {}
    blocks: list[str] = []
    if provider == "anthropic":
        system = payload.get("system")
        if system:
            blocks.append(system if isinstance(system, str) else " ".join(
                b.get("text", "") for b in system if isinstance(b, dict)))
        for message in payload.get("messages") or []:
            blocks.append(" ".join(t for _, t in _anthropic_message_parts(message)))
    else:
        for message in payload.get("messages") or []:
            blocks.append(_openai_message_text(message))
    for tool in payload.get("tools") or payload.get("functions") or []:
        blocks.append(_json(tool))
    return [b for b in blocks if b]


def _is_header_like(text: str) -> bool:
    """True for a line that is metadata, not prose: a single header-style token
    then a colon (e.g. `x-anthropic-billing-header: cc_version=2.1.177.288`).
    Agents like Claude Code prepend such blocks, and their volatile version
    strings would otherwise fragment one template into a row per release."""
    head = text.strip()[:80]
    token = head.split(":", 1)[0] if ":" in head else ""
    return bool(token) and " " not in token and re.match(r"^[A-Za-z][\w.-]*$", token) is not None


def _route_snippet(candidates: list[str]) -> str:
    """First substantive (non header-like) system text from the candidates."""
    for text in candidates:
        flat = " ".join((text or "").split())
        if flat and not _is_header_like(flat):
            return flat[:60]
    # everything was header-like (or empty): fall back to the first non-empty
    for text in candidates:
        flat = " ".join((text or "").split())
        if flat:
            return flat[:60]
    return ""


def route_label(payload: dict | None, provider: str) -> str:
    """A short, human-readable signature of a call's reusable template: the start
    of the system prompt plus how many tools it ships. Calls that share this are
    the same 'route', so the leaderboard can total cost per prompt template.

    Leading metadata blocks (billing/version headers some agents prepend) are
    skipped so the label is readable and groups across releases."""
    payload = payload or {}
    if provider == "anthropic":
        system = payload.get("system")
        candidates = ([system] if isinstance(system, str)
                      else [b.get("text", "") for b in (system or []) if isinstance(b, dict)])
        tools = payload.get("tools") or []
    else:
        candidates = []
        for message in payload.get("messages") or []:
            if message.get("role") == "system":
                candidates.append(_openai_message_text(message))
                break
        tools = payload.get("tools") or payload.get("functions") or []
    snippet = _route_snippet(candidates) or "(no system prompt)"
    return snippet + (f"  +{len(tools)} tools" if tools else "")


# Back-compat alias (OpenAI was the first provider supported).
attribute = attribute_openai
