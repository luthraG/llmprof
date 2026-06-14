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

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from . import pricing, tokens
from .store import Store

DEFAULT_UPSTREAM = os.environ.get("LLMPROF_UPSTREAM", "https://api.openai.com")
_SKIP_HEADERS = {"host", "content-length", "connection", "accept-encoding"}


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


def _usage_from_body(data: bytes, provider: str) -> tuple[int | None, int | None]:
    try:
        usage = json.loads(data).get("usage") or {}
    except (json.JSONDecodeError, AttributeError):
        return (None, None)
    if provider == "anthropic":
        return (usage.get("input_tokens"), usage.get("output_tokens"))
    return (usage.get("prompt_tokens"), usage.get("completion_tokens"))


def _scrape_openai(chunk: bytes, state: dict) -> None:
    for obj in _sse_objects(chunk):
        try:
            delta = (obj.get("choices") or [{}])[0].get("delta", {}).get("content")
            if delta:
                state["text"].append(delta)
            usage = obj.get("usage")  # present only with stream_options include_usage
            if usage:
                state["input"] = usage.get("prompt_tokens", state["input"])
                state["output"] = usage.get("completion_tokens", state["output"])
        except (KeyError, IndexError, AttributeError):
            pass


def _scrape_anthropic(chunk: bytes, state: dict) -> None:
    for obj in _sse_objects(chunk):
        t = obj.get("type")
        if t == "message_start":
            usage = (obj.get("message") or {}).get("usage") or {}
            state["input"] = usage.get("input_tokens", state["input"])
            state["output"] = usage.get("output_tokens", state["output"])
        elif t == "content_block_delta":
            text = (obj.get("delta") or {}).get("text")
            if text:
                state["text"].append(text)
        elif t == "message_delta":
            usage = obj.get("usage") or {}
            if "output_tokens" in usage:
                state["output"] = usage["output_tokens"]


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

    if payload.get("stream"):
        req = app.state.client.build_request("POST", url, content=body, headers=headers)
        upstream = await app.state.client.send(req, stream=True)
        status = upstream.status_code
        scrape = _scrape_anthropic if provider == "anthropic" else _scrape_openai
        state = {"text": [], "input": None, "output": None}

        async def gen():
            async for chunk in upstream.aiter_raw():
                yield chunk
                scrape(chunk, state)
            await upstream.aclose()
            # tokenization runs in a threadpool after the stream, off the loop
            await run_in_threadpool(
                _record_blocking, app, provider, model, payload,
                state["input"], state["output"], "".join(state["text"]), status, started, True,
            )

        return StreamingResponse(
            gen(),
            status_code=status,
            media_type=upstream.headers.get("content-type", "text/event-stream"),
        )

    upstream = await app.state.client.post(url, content=body, headers=headers)
    data = upstream.content
    usage_in, usage_out = _usage_from_body(data, provider)
    # forward immediately; attribute + record in the background so the proxy adds
    # essentially no latency to the proxied call.
    record = BackgroundTask(
        _record_blocking, app, provider, model, payload,
        usage_in, usage_out, None, upstream.status_code, started, False,
    )
    return Response(
        content=data,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
        background=record,
    )


def _record_blocking(app, provider, model, payload, usage_in, usage_out, completion_text,
                     status, started, streamed):
    """All the CPU work (tokenization + attribution) and the DB write. Runs in a
    threadpool / background task so it never blocks the proxied request."""
    breakdown = _attribute(payload, provider)
    prompt_tokens = usage_in if usage_in is not None else breakdown["total"]
    completion_tokens = (
        usage_out
        if usage_out is not None
        else tokens.count_tokens(completion_text or "", model)
    )
    total = (prompt_tokens or 0) + (completion_tokens or 0)
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
