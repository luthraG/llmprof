# llmprof

**pprof for your LLM context. See where every token and dollar goes.**

> Early / work in progress (v0.0.1). The proxy, token attribution, cost
> tracking, OpenAI + Anthropic support, and the CLI work today. The flame-graph
> UI and waste detector are next.

Your billing dashboard and `/usage` are *meters*: they tell you *how much* you
spent. `llmprof` is a *profiler*: it tells you **where** the tokens in each
request actually went, broken down into system prompt vs. tool/function schemas
vs. retrieved context vs. conversation history. You profile CPU and memory, so
why fly blind on the most expensive resource in your AI app, the context window?

## Why

A single agent call can spend thousands of tokens on tool schemas and stale
history before the user says anything, and nothing shows you the breakdown.
`llmprof` sits in front of your LLM provider, attributes every request's tokens
by component, prices the call, and (soon) flame-graphs it so the waste is
obvious.

## Quickstart (30 seconds)

```bash
pipx install llmprof      # or: pip install llmprof
llmprof up                # starts the profiling proxy on http://127.0.0.1:4000
```

Point your client's base URL at the proxy. Your API key passes straight through
to the real provider:

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:4000/v1")  # the only change
client.chat.completions.create(model="gpt-4o", messages=[...])
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

Everything runs locally. Your prompts and keys never leave your machine.

## Works with

`llmprof` profiles by intercepting the API, so it is language-agnostic and
provider-agnostic:

- **Any language.** The proxy is a local HTTP service. Your app can be Python,
  Node/TypeScript, Go, Ruby, anything. You only change its base URL. (A Python
  package is how you install the proxy today; an `npx` launcher and a JS SDK are
  on the roadmap.)
- **OpenAI and any OpenAI-compatible API** through `/v1/chat/completions`:
  Azure OpenAI, Groq, Together, OpenRouter, Mistral, DeepSeek, Fireworks, local
  Ollama / vLLM, and Gemini's OpenAI-compatible endpoint. Point the proxy at it:

  ```bash
  llmprof up --upstream https://api.groq.com/openai
  ```

- **Anthropic** through `/v1/messages` (exact token usage is read from the
  stream):

  ```bash
  llmprof up --upstream https://api.anthropic.com
  ```
  ```python
  from anthropic import Anthropic
  client = Anthropic(base_url="http://127.0.0.1:4000")
  ```

## Configuration

| What | Flag | Env var | Default |
|------|------|---------|---------|
| Bind host | `--host` | `LLMPROF_HOST` | `127.0.0.1` |
| Bind port | `--port` | `LLMPROF_PORT` | `4000` |
| Upstream API | `--upstream` | `LLMPROF_UPSTREAM` | OpenAI |
| Price overrides | | `LLMPROF_PRICING` | built-in table |
| Data dir | | `LLMPROF_HOME` | `~/.llmprof` |

Port already taken? `llmprof up --port 4100`.

Custom or missing model prices? Point `LLMPROF_PRICING` at a JSON file of
`{"model-id": [input_per_1k, output_per_1k]}`. Models without a price still get
their tokens recorded; only the dollar figure is omitted.

## Status / roadmap

- [x] OpenAI-compatible profiling proxy (streaming supported)
- [x] Anthropic Messages API (`/v1/messages`) attribution, with exact usage from the stream
- [x] Per-component token attribution (system / tools / history / input / tool calls / tool results)
- [x] Cost estimation with a user-overridable pricing table
- [x] Local SQLite trace store + `llmprof traces`
- [ ] Flame-graph web UI (the wow) + context-growth timeline
- [ ] Waste detector ("~$X/mo reclaimable")
- [ ] SDK decorators for precise RAG / tool / step labels (Python, then JS/TS)
- [ ] `npx llmprof` launcher for non-Python users

## What llmprof is not

Not a full observability platform (no eval suite, prompt management, or hosted
cloud, that is Langfuse / Phoenix). `llmprof` is the focused **profiler**: where
your tokens go, and what to cut.

## Develop

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && pytest
```

## License

MIT (c) Gaurav Luthra
