"""Replay harness tests: data-correctness regression guards.

Drives synthetic request/response fixtures through the real proxy pipeline and
checks the recorded trace. The streaming-cache fixture is the guard for the
~10x cost bug (a streamed response whose cache tokens were not captured).

Like the UI harness, this proves it can fail: the invariants flag a hand-built
bad trace, and a fixture with the usage stripped out is caught.
"""

import json

import httpx
from fastapi.testclient import TestClient

from llmprof import selftest
from llmprof.proxy import create_app


def test_builtin_fixtures_replay_clean():
    for fixture in selftest.BUILTIN:
        assert selftest.check_fixture(fixture) == [], fixture["name"]


def test_streaming_response_cache_tokens_are_captured():
    """The 4x-cost regression guard: a streamed response's cache-read tokens must
    be captured. If the SSE scraper regresses (as the gzip bug did), this drops
    to 0 and the assertion fails."""
    anth = next(f for f in selftest.BUILTIN if f["wire"] == "messages")
    trace = selftest.replay(anth)
    assert trace["cached_tokens"] == 4800
    # and the call is cheap because almost all input was cached, not fresh
    assert trace["cost_usd"] < 0.05


def test_invariants_have_teeth():
    # cached must not exceed prompt
    assert selftest.trace_invariants(
        {"prompt_tokens": 100, "cached_tokens": 500, "total_tokens": 110,
         "completion_tokens": 10, "cost_usd": 0.01, "reclaimable_usd": 0.0})
    # reclaimable must not exceed spend
    assert any("reclaimable" in v for v in selftest.trace_invariants(
        {"prompt_tokens": 100, "cached_tokens": 10, "total_tokens": 110,
         "completion_tokens": 10, "cost_usd": 0.01, "reclaimable_usd": 0.5}))
    # a healthy trace is clean
    assert selftest.trace_invariants(
        {"prompt_tokens": 100, "cached_tokens": 10, "total_tokens": 110,
         "completion_tokens": 10, "cost_usd": 0.01, "reclaimable_usd": 0.0}) == []


def test_replay_catches_uncaptured_usage():
    """If the proxy fails to capture a streamed response's usage (the bug class),
    the recorded tokens diverge from the fixture's expected values and the replay
    reports it - it does not silently pass."""
    anth = next(f for f in selftest.BUILTIN if f["wire"] == "messages")
    broken = {**anth, "response": 'data: {"type":"content_block_delta","delta":{"text":"hi"}}\n\n'}
    problems = selftest.check_fixture(broken)
    assert any("cached_tokens" in p for p in problems), problems


def test_capture_mode_writes_a_replayable_fixture(tmp_path, monkeypatch):
    """LLMPROF_CAPTURE dumps the (request, response) pair, and what it writes can
    be replayed straight back through the checker."""
    monkeypatch.setenv("LLMPROF_CAPTURE", str(tmp_path))
    resp = {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 800, "completion_tokens": 20}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=resp)

    app = create_app(db_path=str(tmp_path / "c.db"), upstream="http://mock")
    app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = TestClient(app)
    client.post("/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]})

    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    fixture = json.loads(files[0].read_text())
    assert fixture["wire"] == "chat" and fixture["request"]["model"] == "gpt-4o"
    # the captured fixture replays cleanly (invariants hold)
    assert selftest.check_fixture(fixture) == []


def test_run_aggregates_and_reports():
    ok, results = selftest.run()
    assert ok is True
    assert len(results) == len(selftest.BUILTIN)
    assert all(not problems for _, problems in results)
