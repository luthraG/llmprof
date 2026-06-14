---
title: Architecture
description: How llmprof captures, attributes, prices, and stores your LLM calls.
---

llmprof sits between your app and the provider as a transparent proxy. It
forwards every request unchanged (your API key passes straight through) and, on
the way, records what the call was made of.

![How llmprof works: your app talks to the llmprof proxy via a localhost base URL; the proxy forwards to the real provider and streams the response back, while off the hot path it tokenizes, attributes by component, prices, and writes to a local store that the dashboard reads.](../../../assets/architecture.svg)

## The request path

1. Your client calls the proxy instead of the provider (`base_url` points at
   `localhost`). The proxy recognizes `/v1/chat/completions` as OpenAI format and
   `/v1/messages` as Anthropic format; anything else is proxied verbatim.
2. The request is forwarded to the real upstream and the response is streamed
   straight back. Streaming (SSE) is preserved, so token-by-token output is not
   buffered or broken.
3. **Only after** the response is on its way back does llmprof do the analysis
   work, on a background task / threadpool. The proxy adds essentially no
   latency to your call.

## Attribution

For each captured request, the attribution engine breaks the prompt tokens into
the components that make it up:

- `system prompt`
- `user input`
- `history (assistant)` and `tool results`
- `tool schemas` - with each individual tool as a drill-down child
- `tool calls`

Counts come from `tiktoken` and are provider-aware (exact for OpenAI models,
a close approximation for others, labeled as such). The result is both a flat
component map and a tree that drives the [flame graph](../../features/flame-graph/).

## Pricing

A pricing table maps each model to input/output dollars per token, so every call
gets a cost and the dashboard can project monthly spend. It covers 100+ models
and is fully overridable - see [Providers & pricing](../../reference/pricing/).

## Sessions (context creep)

Consecutive calls that extend one another are chained into a *run* by
fingerprinting the message sequence: if a call's messages start with a previous
call's full message list, it is the next turn of that run. This needs no code
change and powers the [context timeline](../../features/timeline/). You can also
set an explicit `x-llmprof-session` header to group calls yourself.

## Storage

Traces are written to a local store - SQLite by default, in a single file. The
storage layer is abstracted behind a `BaseStore` contract, so a centralized
backend (e.g. Postgres for a shared team dashboard) can be plugged in via
`LLMPROF_DB_URL` without changing anything else. See
[Storage backends](../../reference/storage/).

## Why local-first

llmprof is a focused profiler, not an observability platform. Running locally
means zero setup, no data leaving your machine, and a 30-second try. A shared /
team tier is a future option layered on the same store abstraction, not a
prerequisite.
