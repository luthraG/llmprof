"""Replay-based self-test for data correctness.

Feeds captured (or synthetic) request/response pairs through the real proxy
pipeline and asserts the recorded trace is right. This catches the class of bugs
the headless UI harness cannot see - token capture, cost, caching - which were
the worst ones found by hand (a streamed call's cache tokens going uncaptured
priced the call ~10x too high).

The ground truth is the upstream response's own usage block, which the proxy
sees directly. So no live API key and no provider `/usage` screen are needed:
the response is the truth, and replay asserts the proxy matches it.
"""

from __future__ import annotations

import json
import os
import tempfile

_PATHS = {"chat": "/v1/chat/completions", "responses": "/v1/responses", "messages": "/v1/messages"}


def _sse(*objs) -> str:
    return "".join("data: " + json.dumps(o) + "\n\n" for o in objs)


def trace_invariants(t: dict) -> list[str]:
    """Provider-agnostic truths that must hold for every recorded trace. These
    need no external ground truth - violating one is always a bug."""
    out = []
    prompt = t.get("prompt_tokens") or 0
    comp = t.get("completion_tokens") or 0
    total = t.get("total_tokens") or 0
    cached = t.get("cached_tokens") or 0
    cost = t.get("cost_usd")
    rec = t.get("reclaimable_usd") or 0
    if cached > prompt:
        out.append(f"cached tokens ({cached}) exceed prompt tokens ({prompt})")
    if total != prompt + comp:
        out.append(f"total ({total}) != prompt + completion ({prompt + comp})")
    if cost is not None and cost < 0:
        out.append(f"negative cost ({cost})")
    if cost is not None and rec > cost + 1e-9:
        out.append(f"reclaimable (${rec}) exceeds spend (${cost})")
    return out


def replay(fixture: dict) -> dict | None:
    """Drive one fixture through the real proxy and return the recorded trace.

    Uses an httpx MockTransport so no network is touched; everything else (SSE
    scraping, usage parsing, pricing, attribution, storage) is the production
    path, so a regression anywhere in it shows up here."""
    import httpx
    from starlette.testclient import TestClient

    from .proxy import create_app

    db = os.path.join(tempfile.mkdtemp(prefix="llmprof_replay_"), "replay.db")
    app = create_app(db_path=db, upstream="http://mock", anthropic_upstream="http://mock")
    resp = fixture["response"]
    data = resp.encode("utf-8") if isinstance(resp, str) else resp
    stream = fixture.get("stream")
    ctype = "text/event-stream" if stream else "application/json"

    def handler(request: httpx.Request) -> httpx.Response:
        if stream:
            # a streaming body: an async byte iterator, so the proxy's aiter_raw()
            # can consume it (a materialized `content=` would raise StreamConsumed)
            async def body():
                yield data
            return httpx.Response(200, content=body(), headers={"content-type": ctype})
        return httpx.Response(200, content=data, headers={"content-type": ctype})

    # match the existing test pattern: override the client, no lifespan context
    app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = TestClient(app)
    r = client.post(_PATHS[fixture["wire"]], json=fixture["request"])
    if r.status_code != 200:
        raise RuntimeError(f"replay returned HTTP {r.status_code}")
    rows = app.state.store.recent(1)
    return rows[0] if rows else None


def check_fixture(fixture: dict) -> list[str]:
    """Replay a fixture and return problems (empty == healthy): any `expected`
    field that does not match what was recorded, plus the universal invariants."""
    trace = replay(fixture)
    if trace is None:
        return ["no trace was recorded"]
    problems = []
    for key, want in (fixture.get("expected") or {}).items():
        got = trace.get(key)
        if got != want:
            problems.append(f"{key}: recorded {got}, expected {want}")
    return problems + trace_invariants(trace)


# Synthetic fixtures (no real prompts), one per wire, covering a streamed cache
# hit, a buffered partial cache, and a Responses-API stream. `expected` is read
# straight from each response's usage block - that is the ground truth.
BUILTIN: list[dict] = [
    {
        "name": "anthropic messages stream (cache hit)",
        "provider": "anthropic", "wire": "messages", "stream": True,
        "request": {"model": "claude-sonnet-4-6",
                    "system": [{"type": "text", "text": "You are a test assistant."}],
                    "messages": [{"role": "user", "content": "hello"}],
                    "tools": [{"name": "t", "description": "d", "input_schema": {}}],
                    "stream": True},
        "response": _sse(
            {"type": "message_start", "message": {"usage": {
                "input_tokens": 12, "cache_read_input_tokens": 4800,
                "cache_creation_input_tokens": 0, "output_tokens": 1}}},
            {"type": "content_block_delta", "delta": {"text": "hi"}},
            {"type": "message_delta", "usage": {"output_tokens": 40}},
        ),
        "expected": {"prompt_tokens": 4812, "completion_tokens": 40, "cached_tokens": 4800},
    },
    {
        "name": "openai chat (partial cache)",
        "provider": "openai", "wire": "chat", "stream": False,
        "request": {"model": "gpt-4o", "messages": [
            {"role": "system", "content": "You are a test."}, {"role": "user", "content": "hi"}]},
        "response": json.dumps({"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                                "usage": {"prompt_tokens": 1000, "completion_tokens": 50,
                                          "prompt_tokens_details": {"cached_tokens": 200}}}),
        "expected": {"prompt_tokens": 1000, "completion_tokens": 50, "cached_tokens": 200},
    },
    {
        "name": "openai responses stream",
        "provider": "openai", "wire": "responses", "stream": True,
        "request": {"model": "gpt-5.4", "instructions": "You are Codex.",
                    "input": [{"role": "user", "content": [{"type": "input_text", "text": "fix"}]}],
                    "tools": [], "stream": True},
        "response": _sse(
            {"type": "response.output_text.delta", "delta": "ok"},
            {"type": "response.completed", "response": {"usage": {
                "input_tokens": 8000, "output_tokens": 50,
                "input_tokens_details": {"cached_tokens": 0}}}},
        ),
        "expected": {"prompt_tokens": 8000, "completion_tokens": 50, "cached_tokens": 0},
    },
]


def run(corpus: str | None = None) -> tuple[bool, list[tuple[str, list[str]]]]:
    """Run the built-in fixtures, plus any `*.json` fixtures in `corpus` (e.g. a
    private set recorded with LLMPROF_CAPTURE). Returns (all_passed, results)."""
    from pathlib import Path

    fixtures = list(BUILTIN)
    if corpus:
        for p in sorted(Path(corpus).glob("*.json")):
            try:
                fixtures.append({**json.loads(p.read_text(encoding="utf-8")), "name": p.name})
            except (ValueError, OSError) as exc:
                fixtures.append({"name": p.name, "_load_error": str(exc)})

    results: list[tuple[str, list[str]]] = []
    for f in fixtures:
        name = f.get("name", "fixture")
        if "_load_error" in f:
            results.append((name, [f"could not load: {f['_load_error']}"]))
            continue
        try:
            problems = check_fixture(f)
        except Exception as exc:  # noqa: BLE001 - a replay crash is itself a failure to report
            problems = [f"replay error: {exc}"]
        results.append((name, problems))
    return all(not p for _, p in results), results
