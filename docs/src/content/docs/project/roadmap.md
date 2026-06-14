---
title: Roadmap
description: What llmprof does today and what is coming next.
---

## Available today

- Zero-code-change proxy for **OpenAI** and **Anthropic** APIs (streaming-safe).
- Per-component **token attribution** with per-tool drill-down.
- **Cost** for 100+ models, fully overridable.
- **Context flame graph**, **trends**, **context timeline**, and **cost
  leaderboard** views.
- **Waste detector** with a reclaimable-per-month headline.
- **Python SDK** for precise, in-code component labels.
- **Pluggable storage** (SQLite default; backend abstraction for a centralized
  database).
- **`npx llmprof` launcher** - run the proxy with no Python install. See
  [Installation](../../start/installation/#npx-no-python-needed).

## Next

- **JavaScript / TypeScript SDK** - the same component-labeling API as Python
  (`rag`, `tool`, `step` labels).
- **README + demo GIF** - a 15-second above-the-fold demo of the flow.
- **Postgres backend** - a shared/team dashboard on the existing store
  abstraction.
- **Hosted live demo** - try it without installing anything.

## Not in scope

llmprof is a focused profiler, not a full observability platform. It does not do
eval suites, prompt management, or model hosting - it diagnoses where your tokens
and dollars go and tells you what to cut.
