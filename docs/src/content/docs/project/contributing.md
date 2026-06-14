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
pytest        # runs with --cov and --cov-fail-under=90
```

There are also performance and load tests that guard against attribution-latency
regressions and prove the proxy holds up under concurrency while still recording
every trace.

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
