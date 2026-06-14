# @llmprof/sdk

JavaScript / TypeScript SDK for [llmprof](https://github.com/luthraG/llmprof) -
label the components of an LLM call (RAG chunks, tools, history) so the profiler
can attribute tokens precisely, beyond what the proxy can infer on its own.

```bash
npm install @llmprof/sdk
```

Requires a running llmprof proxy (`llmprof up`, or `npx llmprof up`). The SDK
sends labeled components to it; the proxy does the tokenizing, attribution, waste
analysis, and pricing, so JS traces look exactly like Python ones in the same
dashboard.

## Usage

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

The `profile(opts, fn)` wrapper records the trace when `fn` returns, and never
throws on a recording failure (set `LLMPROF_DEBUG` to log them), so profiling
cannot break your app.

### Wrap a function

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

### Manual control

```js
import { createProfile } from '@llmprof/sdk';

const p = createProfile({ model: 'claude-sonnet-4-6', provider: 'anthropic' });
p.add('system prompt', SYSTEM);
p.usage(resp.usage);
await p.record(); // resolves to { ok, reclaimable_usd }; throws on failure
```

## Component labels

`add(component, content, { name, called })`. Friendly labels map to the
dashboard's buckets: `system` / `user` / `history` / `tool` / `rag` (or
`rag_chunk`) / `tool_result`. For `tool` and `rag` components, `name` becomes a
drill-down child in the flame graph. Pass `called: true` (or `p.called('search')`)
to mark tools the model actually used, so the waste detector can flag unused ones.

## Options

`{ model, provider, session, url }`. `url` defaults to `LLMPROF_URL` or
`http://localhost:4000`. `session` groups calls into a timeline run.

Full docs: <https://luthrag.github.io/llmprof>.

## License

MIT (c) Gaurav Luthra
