// Label components of an LLM call from JavaScript, for precise attribution.
//
// Unlike the Python SDK (which writes the local SQLite directly), the JS SDK
// sends the labeled components to a running proxy, which tokenizes, attributes,
// and prices them. So this needs `llmprof up` (or `npx llmprof up`) running.
//
// Run:
//     npm install @llmprof/sdk
//     llmprof up                   # in another terminal
//     node examples/javascript/sdk_manual_attribution.mjs

import { profile } from "@llmprof/sdk";

const SYSTEM_PROMPT = "You are a helpful assistant. Be precise and cite sources.";
const KB_DOC = "Flame graphs visualize stack samples as nested rectangles...".repeat(20);
const SEARCH_SCHEMA = {
  name: "search",
  description: "Search the knowledge base",
  parameters: { type: "object", properties: { q: { type: "string" } } },
};

await profile({ model: "gpt-4o" }, async (p) => {
  p.add("system prompt", SYSTEM_PROMPT);
  p.add("rag_chunk", KB_DOC, { name: "kb#42" });
  p.add("tool", SEARCH_SCHEMA, { name: "search", called: true });
  // In a real app this comes from resp.usage; shown explicitly so the example
  // runs without a live API call.
  p.usage({ prompt_tokens: 1280, completion_tokens: 64 });
});

console.log("Recorded a labeled trace. Open http://localhost:4000 to drill into it.");
