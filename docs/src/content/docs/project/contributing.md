---
title: Contributing
description: Develop, test, and build the docs for llmprof.
---

Contributions are welcome. llmprof is intentionally small and dependency-thin -
please keep it that way.

## Development setup

```bash
git clone https://github.com/luthraG/llmprof
cd llmprof
pip install -e ".[dev]"
```

## Tests, lint, coverage

The suite uses `pytest` (with coverage) and `ruff`. Coverage is gated at 90%.

```bash
ruff check src tests
pytest -q -m "not ui"   # unit + replay tests, runs with --cov and --cov-fail-under=90
```

There are also performance and load tests that guard against attribution-latency
regressions and prove the proxy holds up under concurrency while still recording
every trace.

## QA harnesses

Two harnesses catch the bug classes unit tests miss. Both are self-validating:
each ships a meta-test that runs its own checker against a deliberately broken
case, so a checker that can't fail would fail the suite.

**Render correctness (headless dashboard).** Loads the real dashboard in a
headless browser and asserts invariants instead of eyeballing screenshots: the
optimization pill count must equal the *visible* findings (the guard for a CSS
collision that once hid findings), no NaN/undefined leaks into a panel, a
reclaimable percent stays within 0..100, zero console errors.

```bash
pip install -e ".[ui]"
playwright install chromium
pytest tests/ui --no-cov -q
```

**Data correctness (replay).** Replays request/response fixtures through the real
proxy pipeline (no network) and checks the recorded trace against the response's
own usage block plus invariants: `cached <= prompt`, `total == prompt +
completion`, `reclaimable <= spend`. The ground truth is the upstream response,
so no API key is needed. This is the guard for the streamed-cache mispricing bug.

```bash
llmprof selftest                      # built-in synthetic fixtures
```

To validate against real workloads, record a session and replay it:

```bash
LLMPROF_CAPTURE=./corpus llmprof up   # use it normally, then:
llmprof selftest --corpus ./corpus
```

`FAIL` lines name the exact discrepancy. Keep a corpus around and re-run it after
changes - it re-verifies token capture and cost end to end in seconds.

## Building the docs

The docs are an Astro Starlight site under `docs/`:

```bash
cd docs
npm install
npm run dev      # local preview
npm run build    # production build (what CI deploys)
```

They deploy to GitHub Pages automatically on push to `main`.

## Conventions

- Keep new model prices sourced from the provider's official pricing page, and
  prefer omitting an unverifiable price over guessing (unknown models degrade
  gracefully).
- New storage backends implement the `BaseStore` contract; don't special-case
  the engine elsewhere.
- The dashboard is dependency-light vanilla JS/SVG - no heavy framework.
