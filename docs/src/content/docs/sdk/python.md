---
title: Python SDK
description: Profile LLM calls from your own code with precise component labels - no proxy required.
---

The proxy's heuristics are good, but sometimes you know more than they can infer:
*this* block is a RAG chunk, *that* one is history, *these* are the tools. The
Python SDK lets you label components yourself and records straight into the same
local database the dashboard reads - no base-URL change needed.

```bash
pip install llmprof
```

## The context manager

```python
import llmprof

with llmprof.profile(model="gpt-4o") as p:
    p.add("system prompt", system_text)
    p.add("rag_chunk", retrieved_doc, name="kb#42")
    p.add("tool", search_schema, name="search", called=True)

    resp = client.chat.completions.create(...)

    p.usage(resp.usage)   # exact prompt/completion tokens + cost
# the trace shows up in the dashboard with precise component labels
```

When the `with` block exits, the trace is recorded (idempotent - calling
`p.record()` yourself is safe too).

### `p.add(component, content, *, name=None, called=False)`

Tags a component and returns the token count of `content` (counted with
`tiktoken`). `content` can be a string or any JSON-serializable object (a tool
schema, a dict). Friendly labels map onto the dashboard's component buckets:

| You pass | Shows as |
| --- | --- |
| `system` / `system prompt` | system prompt |
| `user` / `input` | user input |
| `history` / `assistant` | history (assistant) |
| `tool` / `tools` | tool schemas (with `name` as a drill-down child) |
| `rag` / `rag_chunk` / `retrieved` | rag chunks (with `name` as a child) |
| `tool_result` / `tool_results` | tool results |

Pass `called=True` (or call `p.called("search", ...)`) to mark which tools the
model actually used, so the waste detector can flag the unused ones.

### `p.usage(...)`

Set exact token counts. Accepts a provider usage object or dict, or explicit
numbers:

```python
p.usage(resp.usage)                                  # OpenAI/Anthropic usage object
p.usage(prompt_tokens=1234, completion_tokens=56)    # explicit
p.usage(prompt_tokens=1234, completion_tokens=56, cached_tokens=400)
```

If you never call `usage()`, llmprof falls back to the summed token count of the
components you added.

## The decorator

To profile a whole function, wrap it and tag components inside with the
module-level helpers (they target the active profile):

```python
@llmprof.profiled(model="gpt-4o")
def answer(question: str) -> str:
    llmprof.add("system prompt", SYSTEM)
    llmprof.add("user input", question)
    resp = client.chat.completions.create(...)
    llmprof.usage(resp.usage)
    return resp.choices[0].message.content
```

## Options

`profile()` and `profiled()` accept:

- `model` - model id, used for tokenizer choice and pricing (default `gpt-4o`).
- `provider` - `"openai"` (default) or `"anthropic"`, for labeling.
- `session` - an explicit run id, to group calls into a
  [timeline](../../features/timeline/).
- `db_path` - write to a specific SQLite file instead of the default.

By default the SDK writes to the same store as the proxy
(`~/.llmprof/llmprof.db`), so SDK traces and proxied traces appear together in
one dashboard.

:::note[JavaScript / TypeScript]
There is a [JS/TS SDK](../javascript/) with the same component-labeling API.
:::
