---
title: OpenAI-compatible clients
description: Profile any client that speaks the OpenAI chat API by changing its base URL.
---

Anything that talks the OpenAI `/v1/chat/completions` API works with llmprof -
the official SDKs, LangChain, LlamaIndex, and the many providers that expose an
OpenAI-compatible endpoint (DeepInfra, Fireworks, Cerebras, DeepSeek, Together,
local servers, ...).

## Point the base URL at the proxy

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:4000/v1")  # api_key unchanged
```

Or via environment variable, which most tools respect:

```bash
export OPENAI_BASE_URL=http://localhost:4000/v1
```

The proxy forwards to OpenAI by default. Your API key is passed through
untouched; llmprof never stores it.

## Using a different OpenAI-compatible provider

Point the proxy upstream at that provider, then send its model ids as usual:

```bash
llmprof up --upstream https://api.deepinfra.com/v1/openai
# or: export LLMPROF_UPSTREAM=https://api.fireworks.ai/inference/v1
```

llmprof recognizes 100+ model ids for pricing, including the popular open-weight
models on these hosts. If a model is unknown, tokens still show; only the dollar
cost is omitted. See [Providers & pricing](../../reference/pricing/) to add or
override prices.

## Streaming

Streaming responses are forwarded token-by-token without buffering, so `stream=True`
behaves exactly as it would against the provider directly. The trace (including
which tools were called) is recorded after the stream completes.
