# Examples

Small, runnable scripts. Each reads its API key from the environment; nothing is
hard-coded. Start the proxy with `llmprof up` (or `npx llmprof up`) first unless
noted otherwise.

| File | Shows | Needs |
|------|-------|-------|
| [`openai_quickstart.py`](openai_quickstart.py) | The one-line `base_url` change to profile an OpenAI-compatible call. | proxy running, `OPENAI_API_KEY` |
| [`anthropic_quickstart.py`](anthropic_quickstart.py) | Same proxy, Anthropic client (base URL with no `/v1`). | proxy running, `ANTHROPIC_API_KEY` |
| [`sdk_manual_attribution.py`](sdk_manual_attribution.py) | Labeling components yourself for exact attribution. Records straight to the local SQLite. | no proxy needed; `llmprof up` to view |
| [`javascript/sdk_manual_attribution.mjs`](javascript/sdk_manual_attribution.mjs) | The same manual attribution from JS/TS. Sends components to the proxy. | proxy running, `@llmprof/sdk` |

## Run them

```bash
# Proxy-based examples
export OPENAI_API_KEY=sk-...
llmprof up                       # in another terminal
python examples/openai_quickstart.py

# Python SDK (no proxy needed to record; start it to view the dashboard)
python examples/sdk_manual_attribution.py
llmprof up

# JavaScript SDK
npm install @llmprof/sdk
node examples/javascript/sdk_manual_attribution.mjs
```

After any of them, open <http://localhost:4000> for the flame graph, cost, and
the dollars you can reclaim.
