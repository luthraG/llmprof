---
title: Configuration
description: Environment variables and CLI options for the llmprof proxy.
---

llmprof is configured by CLI flags or environment variables - flags win when both
are set.

## Environment variables

| Variable | Default | What it does |
| --- | --- | --- |
| `LLMPROF_HOST` | `127.0.0.1` | Host the proxy binds to. |
| `LLMPROF_PORT` | `4000` | Port the proxy binds to. |
| `LLMPROF_UPSTREAM` | `https://api.openai.com` | The OpenAI-compatible upstream (OpenAI, Groq, Together, ...). |
| `LLMPROF_ANTHROPIC_UPSTREAM` | `https://api.anthropic.com` | The Anthropic upstream. |
| `LLMPROF_HOME` | `~/.llmprof` | Directory for the SQLite database. |
| `LLMPROF_DB_URL` | _(unset)_ | Storage backend URL (`sqlite://...`, `postgresql://...`). Overrides `LLMPROF_HOME`. See [Storage backends](../storage/). |
| `LLMPROF_PRICING` | _(unset)_ | Path to a JSON file of price overrides. See [Providers & pricing](../pricing/). |
| `LLMPROF_CAPTURE` | _(unset)_ | Directory to save each call as a replayable fixture. Feed it to `llmprof selftest --corpus`. See [CLI](../cli/). |
| `LLMPROF_DEBUG` | _(unset)_ | When set, log each captured call and upstream issue to stderr. |

## CLI flags

`llmprof up` accepts:

| Flag | Env | Default |
| --- | --- | --- |
| `--host` | `LLMPROF_HOST` | `127.0.0.1` |
| `--port` | `LLMPROF_PORT` | `4000` |
| `--upstream` | `LLMPROF_UPSTREAM` | OpenAI |
| `--anthropic-upstream` | `LLMPROF_ANTHROPIC_UPSTREAM` | Anthropic |

A single instance routes each request to the right upstream by endpoint, so it
profiles OpenAI and Anthropic clients (e.g. Codex and Claude Code) at the same
time. `--upstream` only changes the OpenAI-compatible target.

```bash
llmprof up --host 0.0.0.0 --port 4100 --upstream https://api.groq.com/openai
```

## Per-request: grouping a run

Send an `x-llmprof-session` request header to force a set of calls to be grouped
into one run on the [timeline](../../features/timeline/), instead of relying on
the automatic prefix-chaining heuristic.

## Endpoints

With the proxy running on port 4000:

- `POST /v1/chat/completions` - OpenAI chat calls (captured).
- `POST /v1/responses` - OpenAI Responses API, used by Codex (captured).
- `POST /v1/messages` - Anthropic-format calls (captured).
- `POST /llmprof/api/ingest` - record a trace from labeled components; used by
  the [JS/TS SDK](../../sdk/javascript/) and any non-Python client.
- any other path - proxied verbatim to the upstream.
- `GET /` - the dashboard.
- `GET /llmprof/health` - health check.
- `GET /llmprof/api/...` - the JSON the dashboard reads.
