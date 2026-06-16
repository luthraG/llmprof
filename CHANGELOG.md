# Changelog

All notable changes to llmprof are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and llmprof follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.4] - 2026-06-16

### Fixed
- **"Today" and the daily chart now use your local day, not UTC.** Day buckets
  were computed in UTC, so a late-night session landed on the previous calendar
  day and "today's" totals looked far lower than provider tools like ccusage
  (which use the local day). Daily totals now bucket by local time, so the day
  cards and the cost-per-day chart line up with your wall clock.

## [0.1.3] - 2026-06-16

### Changed
- **The npm launcher page now shows the demo.** Added the demo image and a
  live-demo link to the launcher README so `npmjs.com/package/llmprof` is not
  text-only. Launcher and PyPI versions bump together because the launcher pins
  the matching `llmprof` release.

## [0.1.2] - 2026-06-16

### Fixed
- **README images now render on PyPI.** The README referenced screenshots and the
  demo with relative paths, which resolve on GitHub but not on the PyPI project
  page (it showed a broken demo image). They now use absolute raw URLs.

## [0.1.1] - 2026-06-16

### Fixed
- **Monthly reclaimable projection no longer extrapolates a single burst to
  24/7.** A dense session (for example 1500+ calls in a few hours) was scaled by
  the observed span, projecting an entire month at the burst rate. The figure now
  appears only once usage spans 2+ distinct calendar days and is averaged per
  active day; a single day shows the trustworthy percent and absolute reclaimed
  instead.
- **The caching reclaim action no longer says "turn on prompt caching" when
  caching is already in use.** It now counts cached vs uncached calls and, when
  caching is active on some traffic, points at the specific calls that shipped an
  uncached prefix.

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
- **Cost for 1000+ models** from a bundled, offline LiteLLM pricing snapshot,
  cache-aware, with curated rates for headline models and overridable via
  `LLMPROF_PRICING`.
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

[0.1.4]: https://github.com/luthraG/llmprof/releases/tag/v0.1.4
[0.1.3]: https://github.com/luthraG/llmprof/releases/tag/v0.1.3
[0.1.2]: https://github.com/luthraG/llmprof/releases/tag/v0.1.2
[0.1.1]: https://github.com/luthraG/llmprof/releases/tag/v0.1.1
[0.1.0]: https://github.com/luthraG/llmprof/releases/tag/v0.1.0
