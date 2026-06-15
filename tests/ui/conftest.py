"""Fixtures for the headless dashboard QA harness.

Seeds a fixed SQLite database (no live API needed), starts the real proxy in a
subprocess against it, and hands tests a Playwright page. Everything is
deterministic: the same fixtures every run, explicit waits (no sleeps), and a
loud failure if the proxy never becomes healthy. The browser-dependent fixtures
skip cleanly when chromium is not installed, so the unit suite is unaffected.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _seed(db_path: str) -> None:
    """A small, fixed set of traces that exercises every view, including the
    exact shapes that previously broke (a streamed OpenAI call with tip-severity
    findings, a cached Anthropic call, and a multi-turn run)."""
    from llmprof.store import SQLiteStore

    st = SQLiteStore(db_path)

    # 1. Codex-style OpenAI call: 11 tools, 1 used. This is the shape whose tip
    #    findings rendered invisibly (the CSS class collision). opt-count must
    #    equal the visible findings.
    tools = [{"name": f"tool_{i}", "tokens": 200, "children": []} for i in range(11)]
    codex_tree = {"name": "context", "tokens": 8000, "children": [
        {"name": "system prompt", "tokens": 3028, "children": []},
        {"name": "tool schemas", "tokens": 2200, "children": tools},
        {"name": "user input", "tokens": 300, "children": []},
    ]}
    st.record({
        "ts": 1000.0, "provider": "openai", "model": "gpt-5.4", "endpoint": "/v1/responses",
        "status": 200, "prompt_tokens": 8000, "completion_tokens": 50, "total_tokens": 8050,
        "cost_usd": 0.02, "streamed": True, "components": {"system prompt": 3028,
        "tool schemas": 2200, "user input": 300}, "detail": codex_tree,
        "cached_tokens": 0, "cache_write_tokens": 0, "called_tools": ["tool_0"],
        "msg_fp": ["u:codex1"],
        "analysis": {"reclaimable_tokens": 0, "reclaimable_usd": 0.0, "findings": [
            {"severity": "warn", "title": "10 of 11 tools were not called on this request",
             "body": "tool_1, tool_2, ...: 2,000 tokens of schemas unused on this request.",
             "reclaimable_tokens": 0, "save_usd": None},
            {"severity": "tip", "title": "Stable prefix not yet served from cache",
             "body": "System prompt and tool schemas repeat every call. openai caches "
                     "repeating prefixes automatically once they recur.",
             "reclaimable_tokens": 0, "save_usd": None},
            {"severity": "tip", "title": "Large system prompt (3,028 tokens)",
             "body": "This fixed overhead rides on every call.",
             "reclaimable_tokens": 0, "save_usd": None},
        ]},
    })

    # 2. Cached Anthropic call with a duplicate-content finding, so the Trends
    #    reclaimable banner shows a real percent (exercises the pct <= 100 check).
    anthro_tree = {"name": "context", "tokens": 5000, "children": [
        {"name": "system prompt", "tokens": 1500, "children": []},
        {"name": "history (assistant)", "tokens": 3500, "children": []},
    ]}
    st.record({
        "ts": 1010.0, "provider": "anthropic", "model": "claude-sonnet-4-6",
        "endpoint": "/v1/messages", "status": 200, "prompt_tokens": 5000,
        "completion_tokens": 40, "total_tokens": 5040, "cost_usd": 0.05, "streamed": True,
        "components": {"system prompt": 1500, "history (assistant)": 3500}, "detail": anthro_tree,
        "cached_tokens": 4800, "cache_write_tokens": 0, "called_tools": [],
        "msg_fp": ["u:other"],
        "analysis": {"reclaimable_tokens": 400, "reclaimable_usd": 0.01, "findings": [
            {"severity": "warn", "title": "Duplicated content in the context",
             "body": "400 tokens of content appear more than once.",
             "reclaimable_tokens": 400, "save_usd": 0.01},
            {"severity": "ok", "title": "Prompt caching is active",
             "body": "4,800 tokens (96% of the prompt) were served from cache.",
             "reclaimable_tokens": 0, "save_usd": None},
        ]},
    })

    # 3. A two-turn run so the Timeline shows a real run, not the empty state.
    run_base = {"provider": "anthropic", "model": "claude-sonnet-4-6", "endpoint": "/v1/messages",
                "status": 200, "completion_tokens": 10, "cost_usd": 0.01, "streamed": True,
                "components": {"system prompt": 100}, "cached_tokens": 0, "cache_write_tokens": 0,
                "detail": {"name": "context", "tokens": 100,
                           "children": [{"name": "system prompt", "tokens": 100, "children": []}]}}
    st.record({**run_base, "ts": 1020.0, "prompt_tokens": 100, "total_tokens": 110,
               "msg_fp": ["u:q1"]})
    st.record({**run_base, "ts": 1021.0, "prompt_tokens": 200, "total_tokens": 210,
               "msg_fp": ["u:q1", "a:r1", "u:q2"]})


def _wait_healthy(base: str, proc: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"proxy exited early with code {proc.returncode}")
        try:
            with urllib.request.urlopen(base + "/llmprof/health", timeout=1) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.2)
    raise RuntimeError("proxy did not become healthy in time")


@pytest.fixture(scope="session")
def dashboard(tmp_path_factory):
    """The real proxy, serving a seeded DB, on a free port. Yields its base URL."""
    home = tmp_path_factory.mktemp("llmprof_ui")
    _seed(str(home / "llmprof.db"))
    port = _free_port()
    runner = ("import uvicorn; from llmprof.proxy import create_app; "
              f"uvicorn.run(create_app(), host='127.0.0.1', port={port}, log_level='warning')")
    env = {**os.environ, "LLMPROF_HOME": str(home)}
    proc = subprocess.Popen([sys.executable, "-c", runner], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_healthy(base, proc)
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session")
def _browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed (pip install -e '.[ui]')")
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - any launch failure -> skip, not fail
            pytest.skip(f"chromium not available (run: playwright install chromium): {exc}")
        yield browser
        browser.close()


@pytest.fixture
def page_and_errors(_browser):
    """A fresh page plus the list of console/page errors collected while it is open."""
    ctx = _browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    yield page, errors
    ctx.close()
