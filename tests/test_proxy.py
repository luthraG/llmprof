import httpx
from fastapi.testclient import TestClient

from llmprof.proxy import create_app


def _mock_openai(handler_response: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json=handler_response)

    return httpx.MockTransport(handler)


def test_proxy_forwards_and_records_trace(tmp_path):
    canned = {
        "id": "chatcmpl-1",
        "choices": [{"message": {"role": "assistant", "content": "Paris."}}],
        "usage": {"prompt_tokens": 23, "completion_tokens": 2, "total_tokens": 25},
    }
    app = create_app(db_path=str(tmp_path / "t.db"), upstream="http://mock-openai")
    app.state.client = httpx.AsyncClient(transport=_mock_openai(canned))
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

    rows = app.state.store.recent(5)
    assert len(rows) == 1
    row = rows[0]
    assert row["model"] == "gpt-4o"
    assert row["prompt_tokens"] == 23  # taken from upstream usage when present
    assert row["completion_tokens"] == 2
    assert row["total_tokens"] == 25
    assert row["cost_usd"] is not None
    assert "system prompt" in row["components"]


def test_health():
    app = create_app(db_path=":memory:", upstream="http://example")
    client = TestClient(app)
    r = client.get("/llmprof/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True
