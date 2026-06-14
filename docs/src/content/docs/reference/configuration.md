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
| `LLMPROF_UPSTREAM` | `https://api.openai.com` | The real provider to forward to. Set to `https://api.anthropic.com` for Anthropic, or any OpenAI-compatible base URL. |
| `LLMPROF_HOME` | `~/.llmprof` | Directory for the SQLite database. |
| `LLMPROF_DB_URL` | _(unset)_ | Storage backend URL (`sqlite://...`, `postgresql://...`). Overrides `LLMPROF_HOME`. See [Storage backends](../storage/). |
| `LLMPROF_PRICING` | _(unset)_ | Path to a JSON file of price overrides. See [Providers & pricing](../pricing/). |

## CLI flags

`llmprof up` accepts:

| Flag | Env | Default |
| --- | --- | --- |
| `--host` | `LLMPROF_HOST` | `127.0.0.1` |
| `--port` | `LLMPROF_PORT` | `4000` |
| `--upstream` | `LLMPROF_UPSTREAM` | OpenAI |

```bash
llmprof up --host 0.0.0.0 --port 4100 --upstream https://api.anthropic.com
```

## Per-request: grouping a run

Send an `x-llmprof-session` request header to force a set of calls to be grouped
into one run on the [timeline](../../features/timeline/), instead of relying on
the automatic prefix-chaining heuristic.

## Endpoints

With the proxy running on port 4000:

- `POST /v1/chat/completions` - OpenAI-format calls (captured).
- `POST /v1/messages` - Anthropic-format calls (captured).
- any other path - proxied verbatim to the upstream.
- `GET /` - the dashboard.
- `GET /llmprof/health` - health check.
- `GET /llmprof/api/...` - the JSON the dashboard reads.
