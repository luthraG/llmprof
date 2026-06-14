"""Python SDK: profile an LLM call from your own code, no proxy required.

    import llmprof

    with llmprof.profile(model="gpt-4o") as p:
        p.add("system prompt", system_text)
        p.add("rag_chunk", doc, name="kb#42")
        p.add("tool", search_schema, name="search")
        resp = client.chat.completions.create(...)
        p.usage(resp.usage)          # exact prompt/completion tokens + cost
    # the trace shows up in the dashboard with precise component labels

Use this when the proxy's heuristics are not enough and you want to label
components yourself (which chunk is RAG, which block is history). It records
straight into the same local SQLite the dashboard reads. Token counts come from
tiktoken for the text you add; pass the provider usage object (or explicit
numbers) for exact totals and cost.

A decorator wraps a whole call site; inside it, llmprof.add()/usage() target the
active profile:

    @llmprof.profiled(model="gpt-4o")
    def answer(q):
        llmprof.add("system prompt", SYSTEM)
        ...
"""

from __future__ import annotations

import contextvars
import functools
import json
import time

from . import analyze, pricing, tokens
from .store import BaseStore, open_store

# active profiles, innermost last (so nesting and the decorator both work)
_stack: contextvars.ContextVar[tuple] = contextvars.ContextVar("llmprof_profiles", default=())
_default_store: BaseStore | None = None

# friendly aliases -> the canonical component buckets the dashboard colors
_ALIASES = {
    "system": "system prompt", "system_prompt": "system prompt",
    "user": "user input", "input": "user input",
    "history": "history (assistant)", "assistant": "history (assistant)",
    "tool": "tool schemas", "tools": "tool schemas", "tool_schema": "tool schemas",
    "rag": "rag chunks", "rag_chunk": "rag chunks", "retrieved": "rag chunks",
    "tool_result": "tool results", "tool_results": "tool results",
}
# components whose entries become named drill-down children in the flame graph
_NAMED_PARENTS = {"tool schemas", "rag chunks"}


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


def _get_store(store, db_path) -> BaseStore:
    global _default_store
    if store is not None:
        return store
    if db_path is not None:
        return open_store(db_path)
    if _default_store is None:
        _default_store = open_store()
    return _default_store


class Profile:
    """One profiled request. Tag components with add(), give exact totals with
    usage(), and the trace is recorded when the context manager exits."""

    def __init__(self, model: str = "gpt-4o", provider: str = "openai", *,
                 store: BaseStore | None = None, db_path: str | None = None,
                 session: str | None = None):
        self.model = model
        self.provider = provider
        self.session = session
        self._store = _get_store(store, db_path)
        self._entries: list[tuple] = []   # (component, name, tokens, text)
        self._called: list[str] = []
        self._usage = {"prompt": None, "completion": None, "cached": None}
        self._started = time.time()
        self._recorded = False

    def add(self, component: str, content, *, name: str | None = None,
            label: str | None = None, called: bool = False) -> int:
        """Tag a component. Returns the token count of the content added."""
        comp = _ALIASES.get(component, component)
        text = _text_of(content)
        toks = tokens.count_tokens(text, self.model)
        item = name or label
        self._entries.append((comp, item, toks, text))
        if called and item:
            self._called.append(item)
        return toks

    def called(self, *names: str) -> None:
        """Record which tools the model actually invoked (drives unused-tool waste)."""
        self._called.extend(n for n in names if n)

    def usage(self, usage=None, *, prompt_tokens: int | None = None,
              completion_tokens: int | None = None, cached_tokens: int | None = None) -> None:
        """Set exact token usage from a provider usage object/dict, or explicitly."""
        if usage is not None:
            def g(key):
                return usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
            if prompt_tokens is None:
                prompt_tokens = g("prompt_tokens") or g("input_tokens")
            if completion_tokens is None:
                completion_tokens = g("completion_tokens") or g("output_tokens")
            if cached_tokens is None:
                cached_tokens = g("cache_read_input_tokens")
                det = g("prompt_tokens_details")
                if cached_tokens is None and isinstance(det, dict):
                    cached_tokens = det.get("cached_tokens")
        if prompt_tokens is not None:
            self._usage["prompt"] = prompt_tokens
        if completion_tokens is not None:
            self._usage["completion"] = completion_tokens
        if cached_tokens is not None:
            self._usage["cached"] = cached_tokens

    def _tree(self):
        order: list[str] = []
        nodes: dict[str, dict] = {}
        for comp, name, toks, _ in self._entries:
            if comp not in nodes:
                nodes[comp] = {"name": comp, "tokens": 0, "children": []}
                order.append(comp)
            nodes[comp]["tokens"] += toks
            if name and comp in _NAMED_PARENTS:
                nodes[comp]["children"].append({"name": name, "tokens": toks, "children": []})
        children = [nodes[c] for c in order]
        total = sum(n["tokens"] for n in children)
        tree = {"name": "context", "tokens": total, "children": children}
        return tree, {c: nodes[c]["tokens"] for c in order}

    def __enter__(self) -> Profile:
        _stack.set(_stack.get() + (self,))
        return self

    def __exit__(self, *exc) -> bool:
        stack = _stack.get()
        if stack and stack[-1] is self:
            _stack.set(stack[:-1])
        self.record()
        return False

    def record(self) -> None:
        """Persist the trace. Called automatically on context-manager exit;
        idempotent so calling it twice is safe."""
        if self._recorded:
            return
        self._recorded = True
        tree, components = self._tree()
        prompt = self._usage["prompt"] if self._usage["prompt"] is not None else tree["tokens"]
        completion = self._usage["completion"] or 0
        cached = self._usage["cached"]
        rate = pricing.rates(self.model)
        ana = analyze.analyze(
            tree, [e[3] for e in self._entries], input_per_1k=rate[0] if rate else None,
            cached_tokens=cached, called_tools=self._called or None, model=self.model,
        )
        self._store.record({
            "ts": self._started, "provider": self.provider, "model": self.model,
            "endpoint": "sdk", "status": 200,
            "prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": (prompt or 0) + (completion or 0),
            "cost_usd": pricing.cost(self.model, prompt or 0, completion or 0),
            "streamed": False, "components": components, "detail": tree,
            "cached_tokens": cached, "called_tools": self._called,
            "session_hint": self.session,
            "analysis": ana, "reclaimable_usd": ana["reclaimable_usd"],
        })


def profile(model: str = "gpt-4o", provider: str = "openai", **kwargs) -> Profile:
    """Start a profile as a context manager. See the module docstring."""
    return Profile(model=model, provider=provider, **kwargs)


def current() -> Profile | None:
    """The innermost active profile, or None."""
    stack = _stack.get()
    return stack[-1] if stack else None


def add(component: str, content, **kwargs) -> int:
    """add() on the active profile (use inside profile()/@profiled)."""
    p = current()
    if p is None:
        raise RuntimeError("llmprof.add() called outside a profile()/@profiled context")
    return p.add(component, content, **kwargs)


def usage(*args, **kwargs) -> None:
    """usage() on the active profile (use inside profile()/@profiled)."""
    p = current()
    if p is None:
        raise RuntimeError("llmprof.usage() called outside a profile()/@profiled context")
    p.usage(*args, **kwargs)


def profiled(model: str = "gpt-4o", provider: str = "openai", **kwargs):
    """Decorator that profiles a whole function. Inside it, use llmprof.add()
    and llmprof.usage() to tag components on the active profile."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kw):
            with profile(model=model, provider=provider, **kwargs):
                return fn(*args, **kw)
        return wrapper
    return decorator
