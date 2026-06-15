# Changelog

All notable changes to llmprof are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and llmprof follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-06-15

Initial public release.

### Added
- **Profiling proxy** - a local OpenAI- and Anthropic-compatible proxy. Point a
  client's base URL at it and every call is tokenized, attributed, priced, and
  flame-graphed off the hot path, so it adds essentially no latency.
- **Context flame graph** - per-request token breakdown (system prompt vs tool
  schemas vs RAG/history vs user input) with per-tool drill-down.
- **Waste detector** - duplicated content, never-called tool schemas, and
  uncached stable prefixes, rolled into a single "reclaimable per month" number,
  with cache advice that is aware of each provider's caching model.
- **Context timeline** - how context grows turn over turn across an agent run,
  with calls grouped into sessions by message fingerprint.
- **Cost leaderboard** - which prompt template (system prompt + tools) drives the
  bill, not just which model.
- **Cost for 100+ models**, cache-aware, overridable via `LLMPROF_PRICING`.
- **Dual provider, one proxy** - `/v1/chat/completions`, `/v1/responses`, and
  `/v1/messages`, so Claude Code and the Codex CLI can be profiled together.
- **Python and JavaScript SDKs** for exact, hand-labeled component attribution
  when the proxy's heuristics are not enough.
- **Local by default** - a single SQLite file you own, with a pluggable backend
  for a shared database. Prompts, completions, and keys only ever go to the
  upstream you already use.
- **CLI** - `llmprof up`, `traces`, `selftest`, `reset`, `version`.
- **Capture and replay self-test** (`LLMPROF_CAPTURE` + `llmprof selftest`) plus a
  headless dashboard harness, so token and cost correctness is checked against
  real captured responses rather than by eye.

[0.1.0]: https://github.com/luthraG/llmprof/releases/tag/v0.1.0
