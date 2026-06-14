---
title: Installation
description: Install llmprof with pipx, pip, Docker, or from source.
---

llmprof is a small Python package. It needs Python 3.9+ and runs entirely on
your machine.

## pipx (recommended)

`pipx` installs the `llmprof` CLI into its own isolated environment so it never
clashes with your project's dependencies.

```bash
pipx install llmprof
llmprof up
```

## npx (no Python needed)

Not a Python shop? Run llmprof straight from npm - no Python, pip, or virtualenv
required:

```bash
npx llmprof up
```

The launcher bootstraps [`uv`](https://github.com/astral-sh/uv) (a single static
binary that provisions its own Python) and runs the real llmprof package. The
first run downloads `uv` once and caches it under `~/.cache/llmprof`; every
argument is forwarded, so `npx llmprof traces` and `npx llmprof up --port 4100`
work as expected.

Useful environment overrides:

- `LLMPROF_SPEC` - the package spec to run (default `llmprof==<launcher version>`);
  point it at a path or `git+https://...` URL for a dev build.
- `LLMPROF_CACHE_DIR` - where the `uv` binary is cached.
- `LLMPROF_UV` - path to an existing `uv` binary to use directly.

## pip

If you would rather install into the current environment (for example to use the
[Python SDK](../../sdk/python/) alongside your app):

```bash
pip install llmprof
```

## From source

While the project is pre-launch, or if you want to hack on it:

```bash
git clone https://github.com/luthraG/llmprof
cd llmprof
pip install -e .
llmprof up
```

## Docker

```bash
docker compose up
```

This serves the proxy and dashboard on port 4000 and keeps the trace database in
a mounted volume so your history survives restarts.

## Verify

With the proxy running, check it is healthy and open the dashboard:

```bash
curl http://localhost:4000/llmprof/health
# {"ok": true, "upstream": "https://api.openai.com"}
```

Open [http://localhost:4000](http://localhost:4000) in a browser. It will be
empty until you send a call - head to the [Quickstart](../quickstart/).

## What gets stored, and where

llmprof writes captured traces to a single local SQLite file at
`~/.llmprof/llmprof.db` (override the directory with `LLMPROF_HOME`). Nothing is
sent anywhere except the upstream provider you are already calling. See
[Storage backends](../../reference/storage/) to point it at a shared database
instead.
