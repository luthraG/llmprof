"""Build a trace from labeled components.

Shared by the Python SDK (in-process) and the proxy's ingest endpoint (used by
the JS/TS SDK and any non-Python client). Keeping this in one place means every
SDK produces identical breakdowns, waste findings, and pricing - the language
binding is just a way to collect labeled components and hand them here.
"""

from __future__ import annotations

import json
import time

from . import analyze, pricing, tokens

# friendly label aliases -> the canonical component buckets the dashboard colors
ALIASES = {
    "system": "system prompt", "system_prompt": "system prompt",
    "user": "user input", "input": "user input",
    "history": "history (assistant)", "assistant": "history (assistant)",
    "tool": "tool schemas", "tools": "tool schemas", "tool_schema": "tool schemas",
    "rag": "rag chunks", "rag_chunk": "rag chunks", "retrieved": "rag chunks",
    "tool_result": "tool results", "tool_results": "tool results",
}
# components whose entries become named drill-down children in the flame graph
NAMED_PARENTS = {"tool schemas", "rag chunks"}


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


def normalize_usage(usage) -> dict:
    """Pull (prompt, completion, cached) tokens from an OpenAI or Anthropic usage
    object/dict. Returns a dict with any of those keys that were present."""
    if not usage:
        return {}

    def g(key):
        return usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)

    out = {}
    prompt = g("prompt_tokens")
    if prompt is None:
        prompt = g("input_tokens")
    completion = g("completion_tokens")
    if completion is None:
        completion = g("output_tokens")
    cached = g("cache_read_input_tokens")
    det = g("prompt_tokens_details")
    if cached is None and isinstance(det, dict):
        cached = det.get("cached_tokens")
    if prompt is not None:
        out["prompt"] = prompt
    if completion is not None:
        out["completion"] = completion
    if cached is not None:
        out["cached"] = cached
    return out


def normalize_items(items, model: str = "gpt-4o"):
    """Turn raw component dicts (component, name?, text?/tokens?, called?) into
    resolved entries [(component, name, tokens, text)] and a list of called tools.
    Token counts use tiktoken when only text is given."""
    entries: list[tuple] = []
    called: list[str] = []
    for it in items or []:
        comp = ALIASES.get(it.get("component", ""), it.get("component") or "unlabeled")
        text = text_of(it.get("text", "") if it.get("text") is not None else it.get("content", ""))
        toks = it.get("tokens")
        toks = int(toks) if toks is not None else tokens.count_tokens(text, model)
        name = it.get("name") or it.get("label")
        entries.append((comp, name, toks, text))
        if it.get("called") and name:
            called.append(name)
    return entries, called


def build_tree(entries):
    """entries: [(component, name, tokens, text)] -> (tree, components_map)."""
    order: list[str] = []
    nodes: dict[str, dict] = {}
    for comp, name, toks, _ in entries:
        if comp not in nodes:
            nodes[comp] = {"name": comp, "tokens": 0, "children": []}
            order.append(comp)
        nodes[comp]["tokens"] += toks
        if name and comp in NAMED_PARENTS:
            nodes[comp]["children"].append({"name": name, "tokens": toks, "children": []})
    children = [nodes[c] for c in order]
    total = sum(n["tokens"] for n in children)
    tree = {"name": "context", "tokens": total, "children": children}
    return tree, {c: nodes[c]["tokens"] for c in order}


def build_trace(model: str, provider: str, entries, called, usage: dict | None = None,
                session: str | None = None, started: float | None = None) -> dict:
    """Assemble a store-ready trace dict from resolved entries. `usage` is the
    normalized dict from normalize_usage(); missing prompt tokens fall back to
    the summed component tokens."""
    usage = usage or {}
    tree, components = build_tree(entries)
    prompt = usage.get("prompt")
    prompt = prompt if prompt is not None else tree["tokens"]
    completion = usage.get("completion") or 0
    cached = usage.get("cached") or 0
    fresh = max((prompt or 0) - cached, 0)
    eff = pricing.effective_input_per_1k(model, provider, fresh, cached, 0)
    ana = analyze.analyze(
        tree, [e[3] for e in entries], input_per_1k=eff,
        cached_tokens=cached, called_tools=called or None, model=model,
        prompt_tokens=prompt, provider=provider,
    )
    return {
        "ts": started if started is not None else time.time(),
        "provider": provider, "model": model, "endpoint": "sdk", "status": 200,
        "prompt_tokens": prompt, "completion_tokens": completion,
        "total_tokens": (prompt or 0) + (completion or 0),
        "cost_usd": pricing.cost_cached(model, provider, fresh, cached, 0, completion),
        "streamed": False, "components": components, "detail": tree,
        "cached_tokens": cached, "cache_write_tokens": 0, "called_tools": called,
        "session_hint": session, "analysis": ana, "reclaimable_usd": ana["reclaimable_usd"],
    }
