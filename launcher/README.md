# llmprof (npx launcher)

**pprof for your LLM context** - run it with no Python install.

```bash
npx llmprof up
```

This is a thin launcher for the [llmprof](https://github.com/luthraG/llmprof)
profiler. It bootstraps [`uv`](https://github.com/astral-sh/uv) - a single static
binary that provisions its own Python - and runs the real llmprof package. You do
not need Python, pip, or a virtualenv.

## Usage

```bash
npx llmprof up                 # start the profiling proxy on http://localhost:4000
npx llmprof up --port 4100     # any llmprof argument is forwarded
npx llmprof traces             # show recent captured calls
```

Then point your LLM client's base URL at `http://localhost:4000/v1` and open the
dashboard at `http://localhost:4000`. Full docs:
<https://luthrag.github.io/llmprof>.

## How it works

1. If `uv` is already on your `PATH`, it is used as-is.
2. Otherwise the matching `uv` static binary is downloaded once (checksum
   verified) and cached under `~/.cache/llmprof`.
3. `uv tool run --from llmprof llmprof <args>` runs the profiler, provisioning a
   managed Python the first time if your machine has none.

## Environment

| Variable | Purpose |
| --- | --- |
| `LLMPROF_SPEC` | Package spec to run (default `llmprof==<launcher version>`); set to a path or `git+https://...` URL for a dev build. |
| `LLMPROF_CACHE_DIR` | Where the `uv` binary is cached (default `~/.cache/llmprof`). |
| `LLMPROF_UV` | Path to an existing `uv` binary to use directly. |

Prefer Python? `pipx install llmprof` works too - see the docs.

## License

MIT (c) Gaurav Luthra
