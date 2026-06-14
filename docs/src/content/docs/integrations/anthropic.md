---
title: Anthropic
description: Profile Anthropic Messages API calls through llmprof.
---

llmprof speaks the Anthropic Messages API (`/v1/messages`) as well as OpenAI. It
attributes the `system` prompt, message history, tool schemas, tool calls, and
tool results, and reads Anthropic's `usage` (including cache reads).

## Point the proxy upstream at Anthropic

```bash
llmprof up --upstream https://api.anthropic.com
```

## Point the client's base URL at the proxy

```python
import anthropic

client = anthropic.Anthropic(base_url="http://localhost:4000")  # api key unchanged

client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    system="You are a helpful assistant.",
    messages=[{"role": "user", "content": "Summarize this contract..."}],
    tools=[...],
)
```

Or set it via environment variable:

```bash
export ANTHROPIC_BASE_URL=http://localhost:4000
```

## Notes

- Prompt caching is detected: when Anthropic reports `cache_read_input_tokens`,
  the call is marked as cached and the waste detector stops suggesting caching
  for it.
- Claude model pricing (3.x and 4.x families, including the newer Opus tiers) is
  built in; see [Providers & pricing](../../reference/pricing/).
- For the Claude Code CLI specifically, see [Claude Code](../claude-code/).
