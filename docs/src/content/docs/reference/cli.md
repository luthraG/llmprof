---
title: CLI
description: The llmprof command-line interface.
---

## `llmprof up`

Start the profiling proxy and dashboard.

```bash
llmprof up [--host HOST] [--port PORT] [--upstream URL]
```

- `--host` (default `127.0.0.1`) - bind address.
- `--port` (default `4000`) - bind port. If the port is taken, llmprof tells you
  and exits, so you can pick another (`--port 4001`).
- `--upstream` (default OpenAI) - the provider to forward to.

Each flag also reads from its `LLMPROF_*` environment variable. See
[Configuration](../configuration/).

On startup it prints the base URL to point your client at, and the dashboard URL.

## `llmprof traces`

Print recent captured calls as a table in the terminal - handy for a quick look
without opening the dashboard.

```bash
llmprof traces [--limit N]
```

Reads the same local database the dashboard uses (`LLMPROF_HOME` /
`LLMPROF_DB_URL` apply).
