import httpx
from fastapi.testclient import TestClient

from llmprof.proxy import create_app


def _mock(json_resp):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=json_resp)

    return httpx.MockTransport(handler)


def _app_with_one_trace(tmp_path):
    app = create_app(db_path=str(tmp_path / "api.db"), upstream="http://mock")
    app.state.client = httpx.AsyncClient(
        transport=_mock(
            {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 4, "total_tokens": 54},
            }
        )
    )
    client = TestClient(app)
    client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "system prompt here"},
                {"role": "user", "content": "hi"},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "search", "description": "d", "parameters": {}},
                }
            ],
        },
    )
    return client


def test_dashboard_served(tmp_path):
    client = _app_with_one_trace(tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "llmpro" in r.text


def test_api_traces_list_and_detail(tmp_path):
    client = _app_with_one_trace(tmp_path)

    listing = client.get("/llmprof/api/traces").json()
    assert listing["upstream"] == "http://mock"
    assert len(listing["traces"]) == 1
    tid = listing["traces"][0]["id"]

    detail = client.get(f"/llmprof/api/traces/{tid}").json()
    assert detail["detail"]["name"] == "context"
    names = [c["name"] for c in detail["detail"]["children"]]
    assert "system prompt" in names
    assert "tool schemas" in names
    ts = next(c for c in detail["detail"]["children"] if c["name"] == "tool schemas")
    assert any(c["name"] == "search" for c in ts["children"])


def test_api_trace_404(tmp_path):
    client = _app_with_one_trace(tmp_path)
    assert client.get("/llmprof/api/traces/999999").status_code == 404


def test_passthrough_proxies_other_paths(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": ["gpt-4o", "gpt-4o-mini"]})

    app = create_app(db_path=str(tmp_path / "p.db"), upstream="http://mock")
    app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = TestClient(app)
    r = client.get("/v1/models")
    assert r.status_code == 200
    assert r.json()["data"] == ["gpt-4o", "gpt-4o-mini"]


def test_lifespan_runs(tmp_path):
    app = create_app(db_path=str(tmp_path / "l.db"), upstream="http://mock")
    # entering the context manager runs startup + shutdown (closes the client)
    with TestClient(app) as client:
        assert client.get("/llmprof/health").json()["ok"] is True
