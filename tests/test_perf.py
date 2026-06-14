"""Performance and load tests.

These guard against pathological regressions (attribution latency) and prove the
proxy holds up under concurrency while still recording every trace. Thresholds
are deliberately generous so the suite is not flaky on slow CI runners.
"""

from __future__ import annotations

import asyncio
import time

import httpx
from httpx import ASGITransport

from llmprof import tokens
from llmprof.proxy import create_app
from llmprof.store import Store


def _big_payload(n_messages: int = 40) -> dict:
    messages = [{"role": "system", "content": "You are a helpful assistant. " * 50}]
    for i in range(n_messages):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"turn {i}: " + ("lorem ipsum " * 40)})
    tools = [
        {
            "type": "function",
            "function": {
                "name": f"tool_{i}",
                "description": "does a thing " * 20,
                "parameters": {
                    "type": "object",
                    "properties": {f"p{j}": {"type": "string"} for j in range(8)},
                },
            },
        }
        for i in range(10)
    ]
    return {"model": "gpt-4o", "messages": messages, "tools": tools}


def test_attribution_latency():
    payload = _big_payload()
    # warm the tiktoken cache so we measure steady-state encoding cost
    warm = tokens.attribute_openai(payload["messages"], payload["tools"], "gpt-4o")
    assert warm["total"] > 1000  # this really is a large request

    n = 50
    t0 = time.perf_counter()
    for _ in range(n):
        result = tokens.attribute_openai(payload["messages"], payload["tools"], "gpt-4o")
    per_call = (time.perf_counter() - t0) / n

    assert result["total"] > 0
    # generous bound: a large (~{warm['total']} token) request must attribute well
    # under half a second; in practice it is a few milliseconds.
    assert per_call < 0.5, f"attribution too slow: {per_call * 1000:.1f} ms/call"
    print(f"\nattribution: {per_call * 1000:.2f} ms/call for a ~{warm['total']}-token request")


def test_traces_read_perf(tmp_path):
    """The dashboard polls recent() frequently; it must stay fast as rows grow."""
    st = Store(str(tmp_path / "big.db"))
    detail = {"name": "context", "tokens": 100,
              "children": [{"name": "system prompt", "tokens": 80, "children": []}]}
    for _ in range(500):
        st.record({
            "provider": "openai", "model": "gpt-4o", "prompt_tokens": 100,
            "completion_tokens": 10, "total_tokens": 110, "cost_usd": 0.001,
            "components": {"system prompt": 80}, "detail": detail,
        })
    t0 = time.perf_counter()
    rows = st.recent(100)
    dt = time.perf_counter() - t0
    assert len(rows) == 100
    assert dt < 0.5, f"recent(100) too slow over 500 rows: {dt * 1000:.1f} ms"
    print(f"\ntraces read: recent(100) over 500 rows in {dt * 1000:.1f} ms")


def test_session_grouping_and_read_perf(tmp_path):
    """Prefix-chaining must group long runs correctly and stay fast: 20 runs of
    8 growing turns each, then the sessions/session reads the timeline polls."""
    st = Store(str(tmp_path / "sess.db"))
    comp = {"system prompt": 80, "history (assistant)": 40}
    detail = {"name": "context", "tokens": 120,
              "children": [{"name": "system prompt", "tokens": 80, "children": []}]}
    t0 = time.perf_counter()
    for r in range(20):
        fp = [f"s:sys{r}", f"u:u{r}0"]
        for turn in range(8):
            st.record({
                "provider": "openai", "model": "gpt-4o", "prompt_tokens": 120,
                "completion_tokens": 10, "total_tokens": 130, "cost_usd": 0.001,
                "components": comp, "detail": detail, "msg_fp": list(fp),
            })
            fp = fp + [f"a:a{r}_{turn}", f"u:u{r}_{turn}"]
    write_dt = time.perf_counter() - t0

    runs = st.sessions(limit=100)
    assert len(runs) == 20, f"expected 20 distinct runs, got {len(runs)}"
    assert all(x["turns"] == 8 for x in runs), "every run should chain to 8 turns"

    t1 = time.perf_counter()
    turns = st.session(runs[0]["session_id"])
    st.sessions()
    read_dt = time.perf_counter() - t1
    assert [t["turn"] for t in turns] == list(range(1, 9))
    assert read_dt < 0.5, f"session reads too slow: {read_dt * 1000:.1f} ms"
    print(f"\nsessions: 160 prefix-resolved writes in {write_dt * 1000:.0f} ms, "
          f"reads in {read_dt * 1000:.1f} ms")


def test_proxy_handles_concurrent_load(tmp_path):
    asyncio.run(_run_load(tmp_path, requests=100))


def test_proxy_handles_concurrent_streaming(tmp_path):
    asyncio.run(_run_stream_load(tmp_path, requests=60))


async def _run_stream_load(tmp_path, requests: int) -> None:
    sse = b'data: {"choices":[{"delta":{"content":"hello there"}}]}\n\ndata: [DONE]\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        async def body():
            yield sse

        return httpx.Response(200, content=body())

    app = create_app(db_path=str(tmp_path / "sl.db"), upstream="http://mock")
    app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    payload = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://app") as client:
        t0 = time.perf_counter()
        responses = await asyncio.gather(
            *[client.post("/v1/chat/completions", json=payload) for _ in range(requests)]
        )
        elapsed = time.perf_counter() - t0
        assert all(r.status_code == 200 for r in responses)
        for _ in range(60):
            if len(app.state.store.recent(requests + 10)) >= requests:
                break
            await asyncio.sleep(0.05)

    rows = app.state.store.recent(requests + 10)
    assert len(rows) == requests, f"recorded {len(rows)}/{requests} streamed traces"
    assert all(r["streamed"] == 1 for r in rows)
    print(f"\nstreaming load: {requests} concurrent streams in {elapsed * 1000:.0f} ms")


async def _run_load(tmp_path, requests: int) -> None:
    canned = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 3, "total_tokens": 23},
    }

    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=canned)

    app = create_app(db_path=str(tmp_path / "load.db"), upstream="http://mock")
    app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))

    payload = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hello"}]}
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://app") as client:
        t0 = time.perf_counter()
        responses = await asyncio.gather(
            *[client.post("/v1/chat/completions", json=payload) for _ in range(requests)]
        )
        elapsed = time.perf_counter() - t0

        assert all(r.status_code == 200 for r in responses)

        # recording is async/background; wait briefly for all traces to land
        for _ in range(60):
            if len(app.state.store.recent(requests + 10)) >= requests:
                break
            await asyncio.sleep(0.05)

    rows = app.state.store.recent(requests + 10)
    assert len(rows) == requests, f"recorded {len(rows)}/{requests} traces"
    assert all(r["prompt_tokens"] == 20 for r in rows)

    rps = requests / elapsed
    print(f"\nload: {requests} concurrent calls in {elapsed * 1000:.0f} ms ({rps:.0f} req/s)")
    await app.state.client.aclose()
