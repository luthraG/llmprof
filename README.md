# llmprof

**pprof for your LLM context — see where every token and dollar goes.**

> ⚠️ Early / work in progress (v0.0.1). The proxy + token attribution + cost
> tracking work today; the flame-graph UI and waste detector are next.

Your billing dashboard and `/usage` are *meters* — they tell you *how much* you
spent. `llmprof` is a *profiler* — it tells you **where** the tokens in each
request actually went: system prompt vs. tool/function schemas vs. retrieved
context vs. conversation history. You profile CPU and memory; why fly blind on
the most expensive resource in your AI app — the context window?

## Why

A single agent call can spend thousands of tokens on tool schemas and stale
history *before the user says anything*, and nothing shows you the breakdown.
`llmprof` sits in front of your LLM provider, attributes every request's tokens
by component, prices the call, and (soon) flame-graphs it so the waste is
obvious.

## Quickstart (30 seconds)

```bash
pipx install llmprof      # or: pip install llmprof
llmprof up                # starts the profiling proxy on http://127.0.0.1:4000
```

Point your client's base URL at the proxy — your API key passes straight
through to the real provider:

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:4000/v1")  # that's the only change
client.chat.completions.create(model="gpt-4o", messages=[...])
```

Profiling Anthropic instead? Start the proxy pointed at Anthropic and set the
SDK's base URL:

```bash
llmprof up --upstream https://api.anthropic.com
```
```python
from anthropic import Anthropic
client = Anthropic(base_url="http://127.0.0.1:4000")
```

Then see where the tokens went:

```bash
llmprof traces
```

```
                          last 1 calls
 model    prompt  completion  total   cost     top component
 gpt-4o      842           2    844   $0.0021  tool schemas (537)
```

Everything runs locally; your prompts and keys never leave your machine.

## Status / roadmap

- [x] OpenAI-compatible profiling proxy (streaming supported)
- [x] Anthropic Messages API (`/v1/messages`) attribution, with exact usage from the stream
- [x] Per-component token attribution (system / tools / history / input / tool calls / tool results)
- [x] Cost estimation (OpenAI + Anthropic pricing)
- [x] Local SQLite trace store + `llmprof traces`
- [ ] Flame-graph web UI (the wow) + context-growth timeline
- [ ] Waste detector ("~$X/mo reclaimable")
- [ ] SDK decorator for precise RAG / tool / step labels

## What llmprof is not

Not a full observability platform (no eval suite, prompt management, or hosted
cloud — that's Langfuse / Phoenix). `llmprof` is the focused **profiler**: where
your tokens go, and what to cut.

## Develop

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && pytest
```

## License

MIT © Gaurav Luthra
