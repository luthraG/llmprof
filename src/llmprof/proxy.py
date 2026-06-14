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

import hashlib
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from . import analyze, ingest, pricing, tokens
from .store import open_store

DEFAULT_OPENAI_UPSTREAM = os.environ.get("LLMPROF_UPSTREAM", "https://api.openai.com")
DEFAULT_ANTHROPIC_UPSTREAM = os.environ.get("LLMPROF_ANTHROPIC_UPSTREAM", "https://api.anthropic.com")
_SKIP_HEADERS = {"host", "content-length", "connection", "accept-encoding"}
_UI_DIR = Path(__file__).parent / "ui"
_UI_HTML = (_UI_DIR / "index.html").read_text(encoding="utf-8")
_UI_CSS = (_UI_DIR / "app.css").read_text(encoding="utf-8")
_UI_JS = (_UI_DIR / "app.js").read_text(encoding="utf-8")


def _ver(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


# bust the browser cache when the assets change, so a restarted proxy never
# serves the dashboard against a stale app.js/app.css the browser cached.
_UI_HTML = (
    _UI_HTML.replace("/llmprof/app.css", f"/llmprof/app.css?v={_ver(_UI_CSS)}")
    .replace("/llmprof/app.js", f"/llmprof/app.js?v={_ver(_UI_JS)}")
)


def _forward_headers(request: Request) -> dict[str, str]:
    return {k: v for k, v in request.headers.items() if k.lower() not in _SKIP_HEADERS}


def _dbg(msg: str) -> None:
    if os.environ.get("LLMPROF_DEBUG"):
        sys.stderr.write(f"llmprof: {msg}\n")
        sys.stderr.flush()


# any request path ending in one of these is captured, no matter how the client
# built the URL (e.g. a doubled /v1, a custom prefix). Maps suffix -> (endpoint,
# provider, wire) so tools like Codex are profiled regardless of base-URL quirks.
_CAPTURE_SUFFIXES = [
    ("/chat/completions", ("/v1/chat/completions", "openai", "chat")),
    ("/responses", ("/v1/responses", "openai", "responses")),
    ("/messages", ("/v1/messages", "anthropic", "messages")),
]


def create_app(db_path: str | None = None, upstream: str | None = None,
               anthropic_upstream: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await app.state.client.aclose()

    app = FastAPI(title="llmprof", version="0.0.1", lifespan=lifespan)
    # one proxy serves both providers: route each request to the right upstream.
    # `--upstream` overrides the OpenAI-compatible endpoint (OpenAI, Groq, ...).
    app.state.upstreams = {
        "openai": (upstream or DEFAULT_OPENAI_UPSTREAM).rstrip("/"),
        "anthropic": (anthropic_upstream or DEFAULT_ANTHROPIC_UPSTREAM).rstrip("/"),
    }
    app.state.upstream = app.state.upstreams["openai"]  # back-compat
    app.state.client = httpx.AsyncClient(timeout=httpx.Timeout(600.0))
    app.state.store = open_store(db_path)

    @app.get("/llmprof/health")
    async def health() -> dict:
        return {"ok": True, "upstreams": app.state.upstreams}

    @app.get("/")
    @app.get("/llmprof")
    async def dashboard() -> Response:
        # always revalidate the HTML so the browser picks up new asset versions
        return HTMLResponse(_UI_HTML, headers={"cache-control": "no-cache"})

    @app.get("/llmprof/app.css")
    async def app_css() -> Response:
        return Response(_UI_CSS, media_type="text/css")

    @app.get("/llmprof/app.js")
    async def app_js() -> Response:
        return Response(_UI_JS, media_type="application/javascript")

    @app.get("/llmprof/api/traces")
    async def api_traces(limit: int = 100) -> dict:
        return {"traces": app.state.store.recent(limit),
                "upstream": app.state.upstreams["openai"], "upstreams": app.state.upstreams}

    @app.get("/llmprof/api/summary")
    async def api_summary() -> dict:
        return {
            "days": app.state.store.daily_summary(),
            "models": app.state.store.model_summary(),
            "routes": app.state.store.routes(),
            "reclaimable": app.state.store.reclaimable_summary(),
        }

    @app.post("/llmprof/api/ingest")
    async def api_ingest(request: Request) -> dict:
        """Record a trace from labeled components. Used by the JS/TS SDK and any
        non-Python client; the heavy lifting (tokenizing, attribution, waste
        analysis, pricing) happens here so every SDK produces identical results."""
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="invalid JSON body") from exc
        model = body.get("model") or "gpt-4o"
        entries, called = ingest.normalize_items(body.get("components"), model)
        trace = ingest.build_trace(
            model, body.get("provider") or "openai", entries, called,
            usage=ingest.normalize_usage(body.get("usage")),
            session=body.get("session"), started=body.get("ts"),
        )
        await run_in_threadpool(app.state.store.record, trace)
        return {"ok": True, "reclaimable_usd": trace["reclaimable_usd"]}

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
        return await _handle(app, request, "/v1/chat/completions", "openai", "chat")

    @app.post("/v1/messages")
    async def messages(request: Request):
        return await _handle(app, request, "/v1/messages", "anthropic", "messages")

    @app.post("/v1/responses")
    async def responses(request: Request):
        return await _handle(app, request, "/v1/responses", "openai", "responses")

    @app.api_route(
        "/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
    )
    async def passthrough(path: str, request: Request):
        full = "/" + path
        # capture any POST whose path ends in a known suffix, even if the client
        # built a non-canonical URL (Codex, odd base_urls). Explicit routes above
        # already handle the canonical paths.
        if request.method == "POST":
            for suffix, (endpoint, provider, wire) in _CAPTURE_SUFFIXES:
                if full.endswith(suffix):
                    _dbg(f"capture {wire} via {full}")
                    return await _handle(app, request, endpoint, provider, wire)
        _dbg(f"passthrough {request.method} {full}")
        return await _proxy_raw(app, request, full)

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


def _usage_fields(usage: dict, wire: str) -> dict:
    """Normalize a provider usage object to {fresh, read, write, output,
    prompt_total} so cost can be priced cache-aware. Any value may be None.
    `wire` is one of: chat, messages (Anthropic), responses (OpenAI Responses)."""
    if wire == "messages":
        fresh = usage.get("input_tokens")
        read = usage.get("cache_read_input_tokens")
        write = usage.get("cache_creation_input_tokens")
        out = usage.get("output_tokens")
        total = None
        if fresh is not None or read is not None or write is not None:
            total = (fresh or 0) + (read or 0) + (write or 0)
        return {"fresh": fresh, "read": read, "write": write, "output": out, "prompt_total": total}
    if wire == "responses":
        # Responses API: input_tokens is the full prompt; cached is a subset
        total = usage.get("input_tokens")
        read = (usage.get("input_tokens_details") or {}).get("cached_tokens")
        fresh = (total - (read or 0)) if total is not None else None
        return {"fresh": fresh, "read": read, "write": 0,
                "output": usage.get("output_tokens"), "prompt_total": total}
    # chat completions: prompt_tokens is the full prompt; cached is a subset
    pt = usage.get("prompt_tokens")
    read = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    fresh = (pt - (read or 0)) if pt is not None else None
    return {"fresh": fresh, "read": read, "write": 0,
            "output": usage.get("completion_tokens"), "prompt_total": pt}


def _usage_from_body(data: bytes, wire: str) -> dict:
    """Cache-aware usage from a buffered response (see _usage_fields)."""
    try:
        usage = json.loads(data).get("usage") or {}
    except (json.JSONDecodeError, AttributeError):
        return {}
    return _usage_fields(usage, wire)


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
                _merge_usage(state, _usage_fields(usage, "chat"))
        except (KeyError, IndexError, AttributeError):
            pass


def _scrape_responses(chunk: bytes, state: dict) -> None:
    """Scrape an OpenAI Responses API SSE stream (used by Codex and others)."""
    for obj in _sse_objects(chunk):
        t = obj.get("type")
        if t == "response.output_text.delta":
            delta = obj.get("delta")
            if isinstance(delta, str):
                state["text"].append(delta)
        elif t == "response.output_item.added":
            item = obj.get("item") or {}
            if item.get("type") == "function_call" and item.get("name"):
                if item["name"] not in state.setdefault("tools", []):
                    state["tools"].append(item["name"])
        elif t in ("response.completed", "response.incomplete", "response.failed"):
            usage = (obj.get("response") or {}).get("usage") or {}
            _merge_usage(state, _usage_fields(usage, "responses"))


def _merge_usage(state: dict, fields: dict) -> None:
    for k in ("fresh", "read", "write", "output", "prompt_total"):
        if fields.get(k) is not None:
            state[k] = fields[k]


def _scrape_anthropic(chunk: bytes, state: dict) -> None:
    for obj in _sse_objects(chunk):
        t = obj.get("type")
        if t == "message_start":
            usage = (obj.get("message") or {}).get("usage") or {}
            _merge_usage(state, _usage_fields(usage, "messages"))
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


def _called_tools_from_body(data: bytes, wire: str) -> list[str]:
    """Tool names the model actually invoked in a (buffered) response."""
    try:
        obj = json.loads(data)
    except (json.JSONDecodeError, AttributeError):
        return []
    names: list[str] = []
    if wire == "messages":
        for block in obj.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name"):
                names.append(block["name"])
    elif wire == "responses":
        for item in obj.get("output") or []:
            if isinstance(item, dict) and item.get("type") == "function_call" and item.get("name"):
                names.append(item["name"])
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
_SCRAPERS = {"chat": _scrape_openai, "messages": _scrape_anthropic, "responses": _scrape_responses}


async def _handle(app: FastAPI, request: Request, endpoint: str, provider: str, wire: str):
    body = await request.body()
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        payload = {}

    model = payload.get("model", "")
    _dbg(f"handle {wire} {endpoint} model={model} stream={bool(payload.get('stream'))}")
    headers = _forward_headers(request)
    url = app.state.upstreams[provider] + endpoint   # route to the right provider
    started = time.time()
    # the Responses API has a different request shape; adapt it to chat shape so
    # all the attribution/fingerprint/route logic applies unchanged.
    attr_payload = tokens.responses_to_chat(payload) if wire == "responses" else payload
    # optional explicit run id; overrides the prefix-chain heuristic when set
    session_hint = request.headers.get("x-llmprof-session")

    if payload.get("stream"):
        req = app.state.client.build_request("POST", url, content=body, headers=headers)
        upstream = await app.state.client.send(req, stream=True)
        status = upstream.status_code
        scrape = _SCRAPERS[wire]
        state = {"text": [], "fresh": None, "read": None, "write": None,
                 "output": None, "prompt_total": None, "tools": []}

        async def gen():
            async for chunk in upstream.aiter_raw():
                yield chunk
                scrape(chunk, state)
            await upstream.aclose()
            usage = {k: state.get(k) for k in ("fresh", "read", "write", "output", "prompt_total")}
            # tokenization runs in a threadpool after the stream, off the loop
            await run_in_threadpool(
                _record_blocking, app, provider, endpoint, model, attr_payload, usage,
                state["tools"], "".join(state["text"]), status, started, True, session_hint,
            )

        return StreamingResponse(
            gen(),
            status_code=status,
            media_type=upstream.headers.get("content-type", "text/event-stream"),
        )

    upstream = await app.state.client.post(url, content=body, headers=headers)
    data = upstream.content
    usage = _usage_from_body(data, wire)
    called = _called_tools_from_body(data, wire)
    # forward immediately; attribute + record in the background so the proxy adds
    # essentially no latency to the proxied call.
    record = BackgroundTask(
        _record_blocking, app, provider, endpoint, model, attr_payload, usage,
        called, None, upstream.status_code, started, False, session_hint,
    )
    return Response(
        content=data,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
        background=record,
    )


def _record_blocking(app, provider, endpoint, model, payload, usage,
                     called_tools, completion_text, status, started, streamed,
                     session_hint=None):
    """All the CPU work (tokenization + attribution) and the DB write. Runs in a
    threadpool / background task so it never blocks the proxied request. `payload`
    is already in chat shape (Responses requests are adapted before this)."""
    breakdown = _attribute(payload, provider)
    msg_fp = tokens.message_fingerprint(payload, provider)
    route = tokens.route_label(payload, provider)
    usage = usage or {}

    prompt_total = usage.get("prompt_total")
    if prompt_total is None:
        prompt_total = breakdown["total"]
        fresh, read, write = prompt_total, 0, 0
    else:
        read = usage.get("read") or 0
        write = usage.get("write") or 0
        fresh = usage.get("fresh")
        if fresh is None:
            fresh = max(prompt_total - read - write, 0)
    completion_tokens = usage.get("output")
    if completion_tokens is None:
        completion_tokens = tokens.count_tokens(completion_text or "", model)
    total = (prompt_total or 0) + (completion_tokens or 0)
    rate = pricing.rates(model)
    analysis = analyze.analyze(
        breakdown["tree"], tokens.content_blocks(payload, provider),
        input_per_1k=rate[0] if rate else None, cached_tokens=read, cache_write=write,
        called_tools=called_tools, model=model or "gpt-4o",
    )
    app.state.store.record(
        {
            "ts": started,
            "provider": provider,
            "model": model,
            "endpoint": endpoint,
            "status": status,
            "prompt_tokens": prompt_total,
            "completion_tokens": completion_tokens,
            "total_tokens": total,
            "cost_usd": pricing.cost_cached(model, provider, fresh, read, write, completion_tokens),
            "streamed": streamed,
            "components": breakdown["components"],
            "detail": breakdown["tree"],
            "cached_tokens": read,
            "cache_write_tokens": write,
            "called_tools": called_tools,
            "msg_fp": msg_fp,
            "session_hint": session_hint,
            "route": route,
            "analysis": analysis,
            "reclaimable_usd": analysis["reclaimable_usd"],
        }
    )


async def _proxy_raw(app: FastAPI, request: Request, path: str):
    # unknown paths default to the OpenAI-compatible upstream
    url = app.state.upstreams["openai"] + path
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
