import httpx
from fastapi.testclient import TestClient

from llmprof.proxy import create_app


def _mock(expected_path: str, *, json=None, content=None, status: int = 200):
    """MockTransport that builds a fresh response per call (streams can only be
    consumed once, so a shared Response object would raise StreamConsumed)."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == expected_path
        if json is not None:
            return httpx.Response(status, json=json)
        return httpx.Response(status, content=content)

    return httpx.MockTransport(handler)


def _stream_mock(expected_path: str, chunks: list[bytes]):
    """MockTransport returning a genuinely-unread async stream (so the proxy can
    aiter_raw it the way it streams from a real upstream)."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == expected_path

        async def body():
            for c in chunks:
                yield c

        return httpx.Response(200, content=body())

    return httpx.MockTransport(handler)


def test_openai_forwards_and_records_trace(tmp_path):
    app = create_app(db_path=str(tmp_path / "t.db"), upstream="http://mock-openai")
    app.state.client = httpx.AsyncClient(
        transport=_mock(
            "/v1/chat/completions",
            json={
                "id": "chatcmpl-1",
                "choices": [{"message": {"role": "assistant", "content": "Paris."}}],
                "usage": {"prompt_tokens": 23, "completion_tokens": 2, "total_tokens": 25},
            },
        )
    )
    client = TestClient(app)

    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Capital of France?"},
            ],
        },
        headers={"authorization": "Bearer sk-test"},
    )

    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "Paris."
    row = app.state.store.recent(5)[0]
    assert row["provider"] == "openai"
    assert row["prompt_tokens"] == 23
    assert row["completion_tokens"] == 2
    assert row["total_tokens"] == 25
    assert row["cost_usd"] is not None
    assert "system prompt" in row["components"]


def test_anthropic_forwards_and_records_trace(tmp_path):
    app = create_app(db_path=str(tmp_path / "t.db"), upstream="http://mock-anthropic")
    app.state.client = httpx.AsyncClient(
        transport=_mock(
            "/v1/messages",
            json={
                "id": "msg_1",
                "content": [{"type": "text", "text": "Paris."}],
                "usage": {"input_tokens": 31, "output_tokens": 3},
            },
        )
    )
    client = TestClient(app)

    resp = client.post(
        "/v1/messages",
        json={
            "model": "claude-3-5-sonnet-20241022",
            "system": "You are helpful.",
            "messages": [{"role": "user", "content": "Capital of France?"}],
            "max_tokens": 64,
        },
        headers={"x-api-key": "sk-ant-test", "anthropic-version": "2023-06-01"},
    )

    assert resp.status_code == 200
    assert resp.json()["content"][0]["text"] == "Paris."
    row = app.state.store.recent(5)[0]
    assert row["provider"] == "anthropic"
    assert row["prompt_tokens"] == 31  # from usage.input_tokens
    assert row["completion_tokens"] == 3  # from usage.output_tokens
    assert row["total_tokens"] == 34
    assert row["cost_usd"] is not None
    assert "system prompt" in row["components"]


def test_anthropic_streaming_uses_exact_usage(tmp_path):
    sse = (
        b'event: message_start\n'
        b'data: {"type":"message_start","message":'
        b'{"usage":{"input_tokens":40,"output_tokens":1}}}\n\n'
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Par"}}\n\n'
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"is."}}\n\n'
        b'event: message_delta\n'
        b'data: {"type":"message_delta","delta":{},"usage":{"output_tokens":7}}\n\n'
    )
    app = create_app(db_path=str(tmp_path / "t.db"), upstream="http://mock-anthropic")
    app.state.client = httpx.AsyncClient(transport=_stream_mock("/v1/messages", [sse]))
    client = TestClient(app)

    resp = client.post(
        "/v1/messages",
        json={
            "model": "claude-3-5-sonnet",
            "system": "Be terse.",
            "messages": [{"role": "user", "content": "Capital of France?"}],
            "stream": True,
            "max_tokens": 64,
        },
    )
    assert resp.status_code == 200
    # body forwarded verbatim (text arrives split across deltas)
    assert b"text_delta" in resp.content
    assert b'"text":"Par"' in resp.content and b'"text":"is."' in resp.content
    row = app.state.store.recent(5)[0]
    assert row["streamed"] == 1
    assert row["prompt_tokens"] == 40  # exact, from message_start
    assert row["completion_tokens"] == 7  # exact, from message_delta


def test_openai_streaming_counts_completion(tmp_path):
    sse = (
        b'data: {"choices":[{"delta":{"content":"Par"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"is."}}]}\n\n'
        b"data: [DONE]\n\n"
    )
    app = create_app(db_path=str(tmp_path / "t.db"), upstream="http://mock-openai")
    app.state.client = httpx.AsyncClient(transport=_stream_mock("/v1/chat/completions", [sse]))
    client = TestClient(app)

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}], "stream": True},
    )
    assert resp.status_code == 200
    row = app.state.store.recent(5)[0]
    assert row["streamed"] == 1
    assert row["completion_tokens"] > 0  # counted from streamed deltas


def test_openai_captures_cached_tokens(tmp_path):
    canned = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {
            "prompt_tokens": 1200, "completion_tokens": 5, "total_tokens": 1205,
            "prompt_tokens_details": {"cached_tokens": 1024},
        },
    }
    app = create_app(db_path=str(tmp_path / "c.db"), upstream="http://mock")
    app.state.client = httpx.AsyncClient(transport=_mock("/v1/chat/completions", json=canned))
    client = TestClient(app)
    client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert app.state.store.recent(1)[0]["cached_tokens"] == 1024


def test_anthropic_captures_cache_read(tmp_path):
    canned = {
        "content": [{"type": "text", "text": "ok"}],
        "usage": {"input_tokens": 1500, "output_tokens": 6, "cache_read_input_tokens": 1300},
    }
    app = create_app(db_path=str(tmp_path / "c.db"), upstream="http://mock")
    app.state.client = httpx.AsyncClient(transport=_mock("/v1/messages", json=canned))
    client = TestClient(app)
    client.post(
        "/v1/messages",
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert app.state.store.recent(1)[0]["cached_tokens"] == 1300


def test_openai_captures_called_tools(tmp_path):
    canned = {
        "choices": [{"message": {"content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "search", "arguments": "{}"}},
        ]}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105},
    }
    app = create_app(db_path=str(tmp_path / "ct.db"), upstream="http://mock")
    app.state.client = httpx.AsyncClient(transport=_mock("/v1/chat/completions", json=canned))
    client = TestClient(app)
    client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}],
              "tools": [{"type": "function", "function": {"name": "search"}}]},
    )
    assert app.state.store.get(app.state.store.recent(1)[0]["id"])["called_tools"] == ["search"]


def test_anthropic_captures_called_tools(tmp_path):
    canned = {
        "content": [
            {"type": "text", "text": "let me search"},
            {"type": "tool_use", "id": "tu1", "name": "web_search", "input": {"q": "x"}},
        ],
        "usage": {"input_tokens": 80, "output_tokens": 6},
    }
    app = create_app(db_path=str(tmp_path / "ct.db"), upstream="http://mock")
    app.state.client = httpx.AsyncClient(transport=_mock("/v1/messages", json=canned))
    client = TestClient(app)
    client.post(
        "/v1/messages",
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert app.state.store.recent(1)[0]["called_tools"] == ["web_search"]


def test_health():
    app = create_app(db_path=":memory:", upstream="http://example")
    client = TestClient(app)
    r = client.get("/llmprof/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True
