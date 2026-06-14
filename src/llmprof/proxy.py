"""OpenAI/Anthropic-compatible profiling proxy.

Point your client's base_url at this proxy. It forwards requests to the real
provider unchanged (your API key passes straight through), and on the way it
attributes the prompt tokens by component, prices the call, and records a trace
locally. Streaming is supported (chunks are forwarded as they arrive).
"""

from __future__ import annotations

import json
import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

from . import pricing, tokens
from .store import Store

DEFAULT_UPSTREAM = os.environ.get("LLMPROF_UPSTREAM", "https://api.openai.com")
_SKIP_HEADERS = {"host", "content-length", "connection", "accept-encoding"}


def _forward_headers(request: Request) -> dict[str, str]:
    return {k: v for k, v in request.headers.items() if k.lower() not in _SKIP_HEADERS}


def _provider_of(upstream: str) -> str:
    if "anthropic" in upstream:
        return "anthropic"
    if "openai" in upstream:
        return "openai"
    return "custom"


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
        return await _handle_chat(app, request)

    @app.api_route(
        "/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
    )
    async def passthrough(path: str, request: Request):
        return await _proxy_raw(app, request, "/" + path)

    return app


async def _handle_chat(app: FastAPI, request: Request):
    body = await request.body()
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        payload = {}

    model = payload.get("model", "")
    messages = payload.get("messages", [])
    tools = payload.get("tools") or payload.get("functions")
    stream = bool(payload.get("stream"))

    breakdown = tokens.attribute(messages, tools, model)
    headers = _forward_headers(request)
    url = app.state.upstream + "/v1/chat/completions"
    started = time.time()

    if stream:
        req = app.state.client.build_request("POST", url, content=body, headers=headers)
        upstream = await app.state.client.send(req, stream=True)
        status = upstream.status_code
        parts: list[str] = []

        async def gen():
            async for chunk in upstream.aiter_raw():
                yield chunk
                _scrape_stream_content(chunk, parts)
            await upstream.aclose()
            completion_tokens = tokens.count_tokens("".join(parts), model)
            _record(app, model, breakdown, completion_tokens, None, status, started, True)

        return StreamingResponse(
            gen(),
            status_code=status,
            media_type=upstream.headers.get("content-type", "text/event-stream"),
        )

    upstream = await app.state.client.post(url, content=body, headers=headers)
    data = upstream.content
    usage = None
    try:
        usage = json.loads(data).get("usage")
    except (json.JSONDecodeError, AttributeError):
        pass
    completion_tokens = (usage or {}).get("completion_tokens", 0) or 0
    _record(app, model, breakdown, completion_tokens, usage, upstream.status_code, started, False)
    return Response(
        content=data,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )


def _scrape_stream_content(chunk: bytes, parts: list[str]) -> None:
    """Best-effort: pull assistant content deltas out of an SSE chunk."""
    try:
        for line in chunk.decode("utf-8", "ignore").splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            obj = json.loads(data)
            delta = (obj.get("choices") or [{}])[0].get("delta", {}).get("content")
            if delta:
                parts.append(delta)
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError):
        pass


def _record(app, model, breakdown, completion_tokens, usage, status, started, streamed):
    prompt_tokens = (usage or {}).get("prompt_tokens") or breakdown["total"]
    total = prompt_tokens + completion_tokens
    app.state.store.record(
        {
            "ts": started,
            "provider": _provider_of(app.state.upstream),
            "model": model,
            "endpoint": "/v1/chat/completions",
            "status": status,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total,
            "cost_usd": pricing.cost(model, prompt_tokens, completion_tokens),
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
