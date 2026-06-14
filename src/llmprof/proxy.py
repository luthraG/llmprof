"""OpenAI/Anthropic-compatible profiling proxy.

Point your client's base_url at this proxy. It forwards requests to the real
provider unchanged (your API key passes straight through), and on the way it
attributes the prompt tokens by component, prices the call, and records a trace
locally. Streaming is supported (chunks are forwarded as they arrive).

The request endpoint decides the request format we parse:
  /v1/chat/completions -> OpenAI    /v1/messages -> Anthropic
Anything else is proxied verbatim without attribution.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from . import analyze, pricing, tokens
from .store import Store

DEFAULT_UPSTREAM = os.environ.get("LLMPROF_UPSTREAM", "https://api.openai.com")
_SKIP_HEADERS = {"host", "content-length", "connection", "accept-encoding"}
_UI_DIR = Path(__file__).parent / "ui"
_UI_HTML = (_UI_DIR / "index.html").read_text(encoding="utf-8")
_UI_CSS = (_UI_DIR / "app.css").read_text(encoding="utf-8")
_UI_JS = (_UI_DIR / "app.js").read_text(encoding="utf-8")


def _forward_headers(request: Request) -> dict[str, str]:
    return {k: v for k, v in request.headers.items() if k.lower() not in _SKIP_HEADERS}


def create_app(db_path: str | None = None, upstream: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await app.state.client.aclose()

    app = FastAPI(title="llmprof", version="0.0.1", lifespan=lifespan)
    app.state.upstream = (upstream or DEFAULT_UPSTREAM).rstrip("/")
    app.state.client = httpx.AsyncClient(timeout=httpx.Timeout(600.0))
    app.state.store = Store(db_path)

    @app.get("/llmprof/health")
    async def health() -> dict:
        return {"ok": True, "upstream": app.state.upstream}

    @app.get("/", response_class=HTMLResponse)
    @app.get("/llmprof", response_class=HTMLResponse)
    async def dashboard() -> str:
        return _UI_HTML

    @app.get("/llmprof/app.css")
    async def app_css() -> Response:
        return Response(_UI_CSS, media_type="text/css")

    @app.get("/llmprof/app.js")
    async def app_js() -> Response:
        return Response(_UI_JS, media_type="application/javascript")

    @app.get("/llmprof/api/traces")
    async def api_traces(limit: int = 100) -> dict:
        return {"traces": app.state.store.recent(limit), "upstream": app.state.upstream}

    @app.get("/llmprof/api/summary")
    async def api_summary() -> dict:
        return {
            "days": app.state.store.daily_summary(),
            "models": app.state.store.model_summary(),
            "routes": app.state.store.routes(),
            "reclaimable": app.state.store.reclaimable_summary(),
        }

    @app.get("/llmprof/api/sessions")
    async def api_sessions() -> dict:
        return {"sessions": app.state.store.sessions()}

    @app.get("/llmprof/api/sessions/{session_id}")
    async def api_session(session_id: str) -> dict:
        turns = app.state.store.session(session_id)
        if not turns:
            raise HTTPException(status_code=404, detail="session not found")
        return {"session_id": session_id, "turns": turns}

    @app.get("/llmprof/api/traces/{trace_id}")
    async def api_trace(trace_id: int) -> dict:
        trace = app.state.store.get(trace_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="trace not found")
        rate = pricing.rates(trace.get("model"))
        trace["input_per_1k"] = rate[0] if rate else None
        trace["output_per_1k"] = rate[1] if rate else None
        trace["context_window"] = pricing.context_window(trace.get("model"))
        return trace

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        return await _handle(app, request, "/v1/chat/completions", "openai")

    @app.post("/v1/messages")
    async def messages(request: Request):
        return await _handle(app, request, "/v1/messages", "anthropic")

    @app.api_route(
        "/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
    )
    async def passthrough(path: str, request: Request):
        return await _proxy_raw(app, request, "/" + path)

    return app


# --------------------------------------------------------------------------- #
# Provider-aware attribution + usage extraction
# --------------------------------------------------------------------------- #
def _attribute(payload: dict, provider: str) -> dict:
    model = payload.get("model", "")
    if provider == "anthropic":
        return tokens.attribute_anthropic(
            payload.get("system"), payload.get("messages"), payload.get("tools"),
            model or "claude-3-5-sonnet",
        )
    return tokens.attribute_openai(
        payload.get("messages"), payload.get("tools") or payload.get("functions"),
        model or "gpt-4o",
    )


def _usage_from_body(data: bytes, provider: str) -> tuple[int | None, int | None, int | None]:
    """Returns (prompt_tokens, completion_tokens, cached_tokens)."""
    try:
        usage = json.loads(data).get("usage") or {}
    except (json.JSONDecodeError, AttributeError):
        return (None, None, None)
    if provider == "anthropic":
        return (usage.get("input_tokens"), usage.get("output_tokens"),
                usage.get("cache_read_input_tokens"))
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    return (usage.get("prompt_tokens"), usage.get("completion_tokens"), cached)


def _scrape_openai(chunk: bytes, state: dict) -> None:
    for obj in _sse_objects(chunk):
        try:
            delta = (obj.get("choices") or [{}])[0].get("delta", {}) or {}
            if delta.get("content"):
                state["text"].append(delta["content"])
            for tc in delta.get("tool_calls") or []:
                fn = (tc.get("function") or {}).get("name")
                if fn and fn not in state.setdefault("tools", []):
                    state["tools"].append(fn)
            usage = obj.get("usage")  # present only with stream_options include_usage
            if usage:
                state["input"] = usage.get("prompt_tokens", state["input"])
                state["output"] = usage.get("completion_tokens", state["output"])
                cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
                if cached is not None:
                    state["cached"] = cached
        except (KeyError, IndexError, AttributeError):
            pass


def _scrape_anthropic(chunk: bytes, state: dict) -> None:
    for obj in _sse_objects(chunk):
        t = obj.get("type")
        if t == "message_start":
            usage = (obj.get("message") or {}).get("usage") or {}
            state["input"] = usage.get("input_tokens", state["input"])
            state["output"] = usage.get("output_tokens", state["output"])
            if usage.get("cache_read_input_tokens") is not None:
                state["cached"] = usage["cache_read_input_tokens"]
        elif t == "content_block_start":
            block = obj.get("content_block") or {}
            if block.get("type") == "tool_use" and block.get("name"):
                if block["name"] not in state.setdefault("tools", []):
                    state["tools"].append(block["name"])
        elif t == "content_block_delta":
            text = (obj.get("delta") or {}).get("text")
            if text:
                state["text"].append(text)
        elif t == "message_delta":
            usage = obj.get("usage") or {}
            if "output_tokens" in usage:
                state["output"] = usage["output_tokens"]


def _called_tools_from_body(data: bytes, provider: str) -> list[str]:
    """Tool names the model actually invoked in a (buffered) response."""
    try:
        obj = json.loads(data)
    except (json.JSONDecodeError, AttributeError):
        return []
    names: list[str] = []
    if provider == "anthropic":
        for block in obj.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name"):
                names.append(block["name"])
    else:
        for choice in obj.get("choices") or []:
            for tc in (choice.get("message") or {}).get("tool_calls") or []:
                fn = (tc.get("function") or {}).get("name")
                if fn:
                    names.append(fn)
    return list(dict.fromkeys(names))  # unique, order-preserving


def _sse_objects(chunk: bytes):
    """Yield parsed JSON objects from the data: lines of an SSE chunk."""
    try:
        lines = chunk.decode("utf-8", "ignore").splitlines()
    except UnicodeDecodeError:
        return
    for line in lines:
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            continue


# --------------------------------------------------------------------------- #
# Request handling
# --------------------------------------------------------------------------- #
async def _handle(app: FastAPI, request: Request, endpoint: str, provider: str):
    body = await request.body()
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        payload = {}

    model = payload.get("model", "")
    headers = _forward_headers(request)
    url = app.state.upstream + endpoint
    started = time.time()
    # optional explicit run id; overrides the prefix-chain heuristic when set
    session_hint = request.headers.get("x-llmprof-session")

    if payload.get("stream"):
        req = app.state.client.build_request("POST", url, content=body, headers=headers)
        upstream = await app.state.client.send(req, stream=True)
        status = upstream.status_code
        scrape = _scrape_anthropic if provider == "anthropic" else _scrape_openai
        state = {"text": [], "input": None, "output": None, "cached": None, "tools": []}

        async def gen():
            async for chunk in upstream.aiter_raw():
                yield chunk
                scrape(chunk, state)
            await upstream.aclose()
            # tokenization runs in a threadpool after the stream, off the loop
            await run_in_threadpool(
                _record_blocking, app, provider, model, payload,
                state["input"], state["output"], state["cached"], state["tools"],
                "".join(state["text"]), status, started, True, session_hint,
            )

        return StreamingResponse(
            gen(),
            status_code=status,
            media_type=upstream.headers.get("content-type", "text/event-stream"),
        )

    upstream = await app.state.client.post(url, content=body, headers=headers)
    data = upstream.content
    usage_in, usage_out, cached = _usage_from_body(data, provider)
    called = _called_tools_from_body(data, provider)
    # forward immediately; attribute + record in the background so the proxy adds
    # essentially no latency to the proxied call.
    record = BackgroundTask(
        _record_blocking, app, provider, model, payload,
        usage_in, usage_out, cached, called, None, upstream.status_code, started, False,
        session_hint,
    )
    return Response(
        content=data,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
        background=record,
    )


def _record_blocking(app, provider, model, payload, usage_in, usage_out, cached,
                     called_tools, completion_text, status, started, streamed,
                     session_hint=None):
    """All the CPU work (tokenization + attribution) and the DB write. Runs in a
    threadpool / background task so it never blocks the proxied request."""
    breakdown = _attribute(payload, provider)
    msg_fp = tokens.message_fingerprint(payload, provider)
    route = tokens.route_label(payload, provider)
    prompt_tokens = usage_in if usage_in is not None else breakdown["total"]
    completion_tokens = (
        usage_out
        if usage_out is not None
        else tokens.count_tokens(completion_text or "", model)
    )
    total = (prompt_tokens or 0) + (completion_tokens or 0)
    rate = pricing.rates(model)
    analysis = analyze.analyze(
        breakdown["tree"], tokens.content_blocks(payload, provider),
        input_per_1k=rate[0] if rate else None, cached_tokens=cached,
        called_tools=called_tools, model=model or "gpt-4o",
    )
    app.state.store.record(
        {
            "ts": started,
            "provider": provider,
            "model": model,
            "endpoint": "/v1/messages" if provider == "anthropic" else "/v1/chat/completions",
            "status": status,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total,
            "cost_usd": pricing.cost(model, prompt_tokens or 0, completion_tokens or 0),
            "streamed": streamed,
            "components": breakdown["components"],
            "detail": breakdown["tree"],
            "cached_tokens": cached,
            "called_tools": called_tools,
            "msg_fp": msg_fp,
            "session_hint": session_hint,
            "route": route,
            "analysis": analysis,
            "reclaimable_usd": analysis["reclaimable_usd"],
        }
    )


async def _proxy_raw(app: FastAPI, request: Request, path: str):
    url = app.state.upstream + path
    body = await request.body()
    upstream = await app.state.client.request(
        request.method,
        url,
        content=body,
        headers=_forward_headers(request),
        params=request.query_params,
    )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )


# module-level app for `uvicorn llmprof.proxy:app` and Docker
app = create_app()
