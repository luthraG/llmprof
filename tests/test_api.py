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
    assert "/llmprof/app.css" in r.text and "/llmprof/app.js" in r.text


def test_static_assets_served(tmp_path):
    client = _app_with_one_trace(tmp_path)
    css = client.get("/llmprof/app.css")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert ".dd-menu" in css.text  # the new dropdown styles are present
    js = client.get("/llmprof/app.js")
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]
    # the flame graph and breakdown degrade to a note instead of a blank panel
    assert "empty-note" in js.text
    assert ".empty-note" in css.text
    # the trends view is throttled so the 4s poll does not flash the panel
    assert "trendsSig" in js.text


def test_empty_breakdown_served(tmp_path):
    """A request with no messages/tools yields a childless tree; the API must
    still serve it (the UI then shows the empty-state note instead of a blank)."""
    app = create_app(db_path=str(tmp_path / "e.db"), upstream="http://mock")
    app.state.client = httpx.AsyncClient(
        transport=_mock(
            {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 2, "total_tokens": 2},
            }
        )
    )
    client = TestClient(app)
    client.post("/v1/chat/completions", json={"model": "gpt-4o", "messages": []})
    tid = client.get("/llmprof/api/traces").json()["traces"][0]["id"]
    detail = client.get(f"/llmprof/api/traces/{tid}").json()
    assert detail["detail"]["children"] == []


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
    # the detail carries the model's input rate so the UI can price each frame
    assert detail["input_per_1k"] == 0.0025  # gpt-4o
    assert detail["context_window"] == 128000  # gpt-4o
    # the waste detector ran at record time and is served with the detail
    assert detail["analysis"] and "findings" in detail["analysis"]
    assert "reclaimable_usd" in detail["analysis"]


def test_api_summary(tmp_path):
    client = _app_with_one_trace(tmp_path)
    s = client.get("/llmprof/api/summary").json()
    assert "days" in s and "models" in s and "routes" in s
    assert len(s["days"]) >= 1
    day = s["days"][-1]
    assert day["calls"] == 1 and day["tokens"] > 0
    assert any(m["model"] == "gpt-4o" for m in s["models"])
    assert s["routes"] and "+1 tools" in s["routes"][0]["route"]
    assert "reclaimable" in s and s["reclaimable"]["calls"] == 1
    assert "monthly_reclaimable_usd" in s["reclaimable"]


def test_api_routes_leaderboard(tmp_path):
    """Calls sharing a system prompt + tool set group into one route; the
    leaderboard totals cost per template and ranks most expensive first."""
    client = _client(tmp_path, "r.db")
    template = {"model": "gpt-4o", "messages": [
        {"role": "system", "content": "You are a pricing bot"},
        {"role": "user", "content": "quote"}]}
    client.post("/v1/chat/completions", json=template)
    client.post("/v1/chat/completions", json=template)
    client.post("/v1/chat/completions", json={
        "model": "gpt-4o", "messages": [{"role": "user", "content": "no system here"}]})
    routes = client.get("/llmprof/api/summary").json()["routes"]
    assert len(routes) == 2
    pricing_route = next(r for r in routes if "pricing bot" in r["route"])
    assert pricing_route["calls"] == 2


def test_api_trace_404(tmp_path):
    client = _app_with_one_trace(tmp_path)
    assert client.get("/llmprof/api/traces/999999").status_code == 404


def _client(tmp_path, name="s.db"):
    app = create_app(db_path=str(tmp_path / name), upstream="http://mock")
    app.state.client = httpx.AsyncClient(
        transport=_mock(
            {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 5, "total_tokens": 35},
            }
        )
    )
    return TestClient(app)


def test_api_sessions_chains_turns(tmp_path):
    """Consecutive calls that extend the previous one group into one run; an
    unrelated call starts its own. Only multi-turn runs are listed."""
    client = _client(tmp_path)
    msgs = [{"role": "system", "content": "agent"}, {"role": "user", "content": "turn 1"}]
    for i in range(3):
        client.post("/v1/chat/completions", json={"model": "gpt-4o", "messages": msgs})
        msgs = msgs + [{"role": "assistant", "content": f"a{i}"},
                       {"role": "user", "content": f"u{i}"}]
    # an unrelated single call
    client.post("/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "unrelated"}]})

    sessions = client.get("/llmprof/api/sessions").json()["sessions"]
    assert len(sessions) == 1  # the single call is not a multi-turn run
    run = sessions[0]
    assert run["turns"] == 3 and run["model"] == "gpt-4o"

    turns = client.get(f"/llmprof/api/sessions/{run['session_id']}").json()["turns"]
    assert [t["turn"] for t in turns] == [1, 2, 3]
    assert turns[0]["components"]  # each turn carries its component breakdown


def test_api_session_header_override(tmp_path):
    """The x-llmprof-session header forces grouping even without a prefix match."""
    client = _client(tmp_path, "h.db")
    headers = {"x-llmprof-session": "run-xyz"}
    client.post("/v1/chat/completions", headers=headers,
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "alpha"}]})
    client.post("/v1/chat/completions", headers=headers,
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "different"}]})
    turns = client.get("/llmprof/api/sessions/run-xyz").json()["turns"]
    assert [t["turn"] for t in turns] == [1, 2]


def test_api_session_404(tmp_path):
    client = _app_with_one_trace(tmp_path)
    assert client.get("/llmprof/api/sessions/nope").status_code == 404


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
