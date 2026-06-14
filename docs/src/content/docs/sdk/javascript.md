---
title: JavaScript / TypeScript SDK
description: Label LLM context components from JS/TS for precise token attribution.
---

The JS/TS SDK gives JavaScript and TypeScript apps the same precise,
component-labeled profiling as the [Python SDK](../python/): you tag which block
is a RAG chunk, which is history, which are the tools, and llmprof attributes the
tokens exactly.

```bash
npm install @llmprof/sdk
```

It needs a running proxy ([`llmprof up`](../../start/quickstart/), or
`npx llmprof up`). The SDK sends labeled components to the proxy, which does the
tokenizing, attribution, waste analysis, and pricing - so JS traces look exactly
like Python ones in the same dashboard, with no tokenizer dependency shipped to
the browser or Node.

## Profile a call

```js
import { profile } from '@llmprof/sdk';

await profile({ model: 'gpt-4o' }, async (p) => {
  p.add('system prompt', systemText);
  p.add('rag_chunk', retrievedDoc, { name: 'kb#42' });
  p.add('tool', searchSchema, { name: 'search', called: true });

  const resp = await client.chat.completions.create(/* ... */);

  p.usage(resp.usage); // exact prompt/completion tokens + cost
});
```

`profile(opts, fn)` records the trace when `fn` returns and never throws on a
recording failure (set `LLMPROF_DEBUG` to log them), so profiling cannot break
your app. It returns whatever `fn` returns.

## Wrap a function

```js
import { profiled } from '@llmprof/sdk';

const answer = profiled({ model: 'gpt-4o' }, async (p, question) => {
  p.add('system prompt', SYSTEM);
  p.add('user input', question);
  const resp = await client.chat.completions.create(/* ... */);
  p.usage(resp.usage);
  return resp.choices[0].message.content;
});

await answer('How do I...');
```

The profile is passed as the first argument to your function.

## Manual control

```js
import { createProfile } from '@llmprof/sdk';

const p = createProfile({ model: 'claude-sonnet-4-6', provider: 'anthropic' });
p.add('system prompt', SYSTEM);
p.usage(resp.usage);
await p.record(); // resolves to { ok, reclaimable_usd }; throws on failure
```

## Component labels

`p.add(component, content, { name, called })` - `content` is a string or any
JSON-serializable value. Friendly labels map to the dashboard's buckets, the same
as the Python SDK:

| You pass | Shows as |
| --- | --- |
| `system` / `system prompt` | system prompt |
| `user` / `input` | user input |
| `history` / `assistant` | history (assistant) |
| `tool` / `tools` | tool schemas (with `name` as a drill-down child) |
| `rag` / `rag_chunk` / `retrieved` | rag chunks (with `name` as a child) |
| `tool_result` / `tool_results` | tool results |

Pass `{ called: true }` (or `p.called('search')`) to mark tools the model
actually used, so the waste detector can flag the unused ones.

## Options and exact usage

`{ model, provider, session, url }`. `url` defaults to the `LLMPROF_URL`
environment variable or `http://localhost:4000`. `session` groups calls into a
[timeline](../../features/timeline/) run.

`p.usage(...)` accepts a provider usage object as-is - OpenAI
(`prompt_tokens` / `completion_tokens` / `prompt_tokens_details.cached_tokens`)
or Anthropic (`input_tokens` / `output_tokens` / `cache_read_input_tokens`). If
you never set usage, llmprof falls back to the summed token count of the
components you added.

## How it talks to the proxy

The SDK POSTs to `POST /llmprof/api/ingest` on the proxy. That endpoint is the
shared path every non-Python client uses; it builds the identical breakdown,
waste findings, and pricing as a proxied call.
