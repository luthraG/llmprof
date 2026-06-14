---
title: Storage backends
description: SQLite by default, with a pluggable backend for centralized/team setups.
---

llmprof stores captured traces through a small backend abstraction (`BaseStore`),
so the engine can change without touching the proxy, SDK, or dashboard.

## SQLite (default)

Out of the box, traces go to a single local SQLite file:

```
~/.llmprof/llmprof.db
```

Change the directory with `LLMPROF_HOME`. The database uses WAL mode, so the
dashboard's reads never block the proxy's writes, and recording happens off the
hot path. This is the right choice for the common case: one developer, one
machine, zero setup, data stays local.

### When SQLite is enough

- A single developer profiling their own app or agent.
- One machine running the proxy and the dashboard.
- You want nothing to install and nothing to leave the box.

## Switching backends with `LLMPROF_DB_URL`

`open_store()` picks the backend from `LLMPROF_DB_URL` (an explicit URL wins over
the default path). Today the bundled schemes are:

```bash
# explicit sqlite path
LLMPROF_DB_URL=sqlite:///srv/llmprof/traces.db llmprof up
```

A `postgresql://` URL is recognized and routed to an optional
`llmprof._postgres.PostgresStore`. If that backend is not present in your build,
llmprof fails fast with a clear message instead of silently falling back to
SQLite:

```bash
LLMPROF_DB_URL=postgresql://user:pass@host/llmprof llmprof up
# RuntimeError: ... no Postgres backend in this build ...
```

### When you'd want a centralized backend

- A **team** wanting one shared dashboard across machines.
- **Multiple proxy instances** writing concurrently at high volume.
- Long retention with heavy analytical queries.

These are exactly the cases SQLite is not built for (it is single-file and
single-writer). The abstraction leaves the door open: adding Postgres is a matter
of dropping in one `BaseStore` implementation, with no other code changes.

## Implementing a backend

A backend implements the `BaseStore` contract: `record`, `recent`, `get`,
`daily_summary`, `model_summary`, `sessions`, `session`, `routes`, and
`reclaimable_summary`, all returning plain JSON-decoded dict/list structures.
`SQLiteStore` is the reference implementation.
