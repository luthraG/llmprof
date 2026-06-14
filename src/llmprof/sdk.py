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
import time

from . import ingest, tokens
from .store import BaseStore, open_store

# active profiles, innermost last (so nesting and the decorator both work)
_stack: contextvars.ContextVar[tuple] = contextvars.ContextVar("llmprof_profiles", default=())
_default_store: BaseStore | None = None


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
        comp = ingest.ALIASES.get(component, component)
        text = ingest.text_of(content)
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
        merged = ingest.normalize_usage(usage)
        if prompt_tokens is not None:
            merged["prompt"] = prompt_tokens
        if completion_tokens is not None:
            merged["completion"] = completion_tokens
        if cached_tokens is not None:
            merged["cached"] = cached_tokens
        self._usage.update(merged)

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
        usage = {k: v for k, v in self._usage.items() if v is not None}
        trace = ingest.build_trace(
            self.model, self.provider, self._entries, self._called,
            usage=usage, session=self.session, started=self._started,
        )
        self._store.record(trace)


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
