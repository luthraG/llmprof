"""Label components yourself when the proxy's heuristics are not enough.

The proxy attributes tokens by inspecting the wire payload. When you want exact
attribution (which RAG chunk, which tool, your own component names), use the SDK:
open a `profile`, `add` each component with a label, hand it the response `usage`,
and it records a trace on exit. The Python SDK records straight into the same
local SQLite the dashboard reads, so no proxy is needed; it does not make the
model call for you.

Run:
    python examples/sdk_manual_attribution.py
    llmprof up                       # then open the dashboard to see the trace
"""

import llmprof

SYSTEM_PROMPT = "You are a helpful assistant. Be precise and cite sources."
KB_DOC = "Flame graphs visualize stack samples as nested rectangles..." * 20
SEARCH_SCHEMA = {
    "name": "search",
    "description": "Search the knowledge base",
    "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
}

with llmprof.profile(model="gpt-4o") as p:
    p.add("system prompt", SYSTEM_PROMPT)
    p.add("rag_chunk", KB_DOC, name="kb#42")
    p.add("tool", SEARCH_SCHEMA, name="search", called=True)
    # In a real app this comes from resp.usage; shown explicitly so the example
    # runs without a live API call.
    p.usage(prompt_tokens=1280, completion_tokens=64)

print("Recorded a labeled trace. Open http://localhost:4000 to drill into it.")
