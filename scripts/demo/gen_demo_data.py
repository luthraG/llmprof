#!/usr/bin/env python3
"""Generate the static demo dataset for the hosted dashboard at /llmprof/try/.

The demo is the real dashboard frozen to a recorded set of calls. To keep it
honest, we do not hand-write the JSON: we feed sanitized-but-realistic calls
through llmprof's actual ingest + store + pricing code (the same path the JS SDK
and proxy use), then snapshot the five read endpoints through the real FastAPI
app. So every token count, waste finding, and dollar figure in the demo is what
llmprof would compute for that traffic - just with generic content instead of
anyone's real prompts.

Run locally to refresh the committed snapshot:

    python scripts/demo/gen_demo_data.py

It writes docs/public/try/demo-data.json. Timestamps are anchored to a fixed
epoch so regenerating produces a stable diff.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from llmprof import ingest, proxy  # noqa: E402
from llmprof.store import SQLiteStore  # noqa: E402

OUT = ROOT / "docs" / "public" / "try" / "demo-data.json"

# Anchor every timestamp to a fixed point so the snapshot is reproducible (no
# wall-clock churn in the committed file). Spans ~11 days, enough for the monthly
# projection (needs >= 50 calls and >= 12h span) and per-day trend charts.
BASE_TS = 1_748_000_000  # fixed; ~mid-2025
DAY = 86_400
HOUR = 3_600

# A generic config blob used to demonstrate duplicated-content detection: the
# same file pasted into context several times in one request. Must be >= 200
# chars (the detector's floor) and is intentionally not anyone's real content.
DUP_FILE = (
    "service: payments-gateway\n"
    "region: us-east-1\n"
    "replicas: 6\n"
    "timeouts: {connect_ms: 250, read_ms: 4000, write_ms: 4000}\n"
    "retries: {max: 3, backoff_ms: 200, jitter: true}\n"
    "circuit_breaker: {error_threshold: 0.5, window_s: 30, cooldown_s: 60}\n"
    "rate_limits: {rps: 800, burst: 1200, per_key: true}\n"
    "feature_flags: {settlement_v2: true, fraud_scoring: true, legacy_refunds: false}\n"
) * 2


def _tool(name: str, toks: int, called: bool) -> dict:
    """One tool schema shipped in a request. `called` marks whether the model
    actually invoked it on this turn (uncalled-everywhere tools become the
    reclaimable 'dead tool' signal in store.reclaimable_summary)."""
    return {"component": "tools", "name": name, "tokens": toks, "called": called}


def _spec(specs: list, *, model, provider, route, ts, components, usage, session):
    """Stash one call. Recorded later in global timestamp order so the dashboard's
    'recent 100' is a natural chronological mix of every workload."""
    specs.append({"model": model, "provider": provider, "route": route, "ts": ts,
                  "components": components, "usage": usage, "session": session})


# A typical developer week: a Claude Code run every working day, a few Codex
# sessions, a steady support bot, and some batch jobs. ~7 days, dense enough that
# the monthly projection (gated on >= 50 calls and >= 12h span) reflects real
# daily usage rather than a short burst.
DAYS = 7


def _claude_code_days(specs: list) -> None:
    """A Claude Code run each day on Claude Opus. Ships six tools every turn; the
    `browser` tool is never called on any turn of any day (the headline
    reclaimable 'dead tool'). The first turn of each day is cache-cold (uncached
    stable prefix -> a caching tip); the rest are cache-warm. A couple of turns
    paste the same file repeatedly (duplicate content)."""
    for d in range(DAYS):
        sid = f"cc-{d}a3f{d}9c2"
        day_start = BASE_TS + d * DAY + 9 * HOUR  # ~9am
        turns = 22 + (d % 3) * 6                  # 22-34 turns/day
        for i in range(turns):
            ts = day_start + i * 9 * 60
            cold = i == 0
            tools = [
                _tool("browser", 2300, called=False),   # never called, ever
                _tool("edit_file", 1450, called=(i % 3 == 0)),
                _tool("bash", 900, called=(i % 2 == 0)),
                _tool("read_file", 1150, called=True),
                _tool("search", 1500, called=(i % 5 == 0)),
                _tool("grep", 820, called=(i % 4 == 0)),
            ]
            comps = [
                {"component": "system", "tokens": 1850},
                *tools,
                {"component": "history", "tokens": 600 + i * 480},
                {"component": "tool_results", "tokens": 400 + i * 220},
                {"component": "user", "tokens": 180},
            ]
            if i in (10, 18):  # retrieval / re-read pastes the same file again
                for _ in range(3):
                    comps.append({"component": "tool_results", "text": DUP_FILE})
            prompt = sum(c.get("tokens", 0) for c in comps if "tokens" in c)
            if cold:
                usage = {"input_tokens": prompt, "output_tokens": 240}
            else:
                usage = {"input_tokens": prompt, "output_tokens": 260,
                         "cache_read_input_tokens": int(prompt * 0.82)}
            _spec(specs, model="claude-opus-4-8", provider="anthropic",
                  route="claude-code / agent loop", ts=ts, components=comps,
                  usage=usage, session=sid)


def _codex_sessions(specs: list) -> None:
    """Codex CLI runs on GPT-5 on a few days. Ships a `web_search` tool it never
    calls."""
    for n, d in enumerate((1, 3, 5)):
        sid = f"codex-{d}b8e44{n}"
        day_start = BASE_TS + d * DAY + 14 * HOUR
        for i in range(14):
            ts = day_start + i * 8 * 60
            tools = [
                _tool("apply_patch", 1300, called=(i % 2 == 0)),
                _tool("shell", 760, called=True),
                _tool("web_search", 1100, called=False),   # never called -> dead
                _tool("read", 980, called=True),
            ]
            comps = [
                {"component": "system", "tokens": 1400},
                *tools,
                {"component": "history", "tokens": 500 + i * 430},
                {"component": "tool_results", "tokens": 300 + i * 210},
                {"component": "user", "tokens": 140},
            ]
            prompt = sum(c.get("tokens", 0) for c in comps if "tokens" in c)
            cached = int(prompt * 0.6) if i > 1 else 0  # OpenAI auto-caches prefixes
            usage = {"prompt_tokens": prompt, "completion_tokens": 210,
                     "prompt_tokens_details": {"cached_tokens": cached}}
            _spec(specs, model="gpt-5", provider="openai",
                  route="codex / agent loop", ts=ts, components=comps,
                  usage=usage, session=sid)


def _rag_sessions(specs: list) -> None:
    """A support bot on GPT-4o running every day. Retrieval keeps pasting
    overlapping chunks, so some turns carry duplicate content."""
    for d in range(DAYS):
        sid = f"rag-{d}d2c07"
        day_start = BASE_TS + d * DAY + 11 * HOUR
        for i in range(4):
            ts = day_start + i * 70 * 60
            comps = [
                {"component": "system", "tokens": 760},
                {"component": "rag", "name": "kb/billing.md", "tokens": 900},
                {"component": "rag", "name": "kb/refunds.md", "tokens": 820},
                {"component": "history", "tokens": 300 + i * 160},
                {"component": "user", "tokens": 90},
            ]
            if i == 2:  # retrieval re-pastes the same doc
                comps.append({"component": "rag", "name": "kb/policy.md", "text": DUP_FILE})
                comps.append({"component": "rag", "name": "kb/policy.md", "text": DUP_FILE})
            prompt = sum(c.get("tokens", 0) for c in comps if "tokens" in c)
            usage = {"prompt_tokens": prompt, "completion_tokens": 180}
            _spec(specs, model="gpt-4o", provider="openai",
                  route="support bot / answer", ts=ts, components=comps,
                  usage=usage, session=sid)


def _batch_calls(specs: list) -> None:
    """Cheap standalone summarization calls on Haiku, a few most days (no session)."""
    for d in range(DAYS):
        for i in range(2):
            ts = BASE_TS + d * DAY + 19 * HOUR + i * HOUR
            comps = [
                {"component": "system", "tokens": 320},
                {"component": "user", "tokens": 2600 + i * 300},
            ]
            prompt = sum(c.get("tokens", 0) for c in comps if "tokens" in c)
            usage = {"input_tokens": prompt, "output_tokens": 320}
            _spec(specs, model="claude-haiku-4-5", provider="anthropic",
                  route="batch / summarize", ts=ts, components=comps,
                  usage=usage, session=None)


def seed(store) -> None:
    """Populate a store with the full demo dataset, recorded in timestamp order."""
    specs: list = []
    _claude_code_days(specs)
    _codex_sessions(specs)
    _rag_sessions(specs)
    _batch_calls(specs)
    specs.sort(key=lambda s: s["ts"])
    for s in specs:
        entries, called = ingest.normalize_items(s["components"], s["model"])
        trace = ingest.build_trace(
            s["model"], s["provider"], entries, called,
            usage=ingest.normalize_usage(s["usage"]),
            session=s["session"], started=float(s["ts"]),
        )
        # `route` (the recurring prompt-template label) drives the cost
        # leaderboard. build_trace does not set it (neither does the live ingest
        # endpoint), so we label the demo traffic here to light up that view.
        trace["route"] = s["route"]
        store.record(trace)


def build_snapshot(store, upstreams: dict) -> dict:
    """Snapshot the five read endpoints through the real FastAPI app so the demo
    payloads are byte-identical to what a running proxy returns (including the
    /traces/{id} pricing enrichment and the traces `ver`/`upstream` fields).

    The top-level `ver` is taken from the live traces response (the app's asset
    version), not passed in: build-try.mjs stamps window.__LLMPROF_VER from it,
    and app.js reloads itself if that disagrees with traces.ver, so the two must
    be the same value by construction.

    Returns the demo-data.json structure consumed by docs/public/try/demo.js.
    """
    from fastapi.testclient import TestClient

    app = proxy.create_app(db_path=store.path)
    app.state.upstreams = upstreams
    app.state.upstream = upstreams["openai"]

    with TestClient(app) as client:
        summary = client.get("/llmprof/api/summary").json()
        traces = client.get("/llmprof/api/traces?limit=100").json()
        sessions = client.get("/llmprof/api/sessions").json()
        trace_by_id = {}
        for t in traces["traces"]:
            tid = t["id"]
            trace_by_id[str(tid)] = client.get(f"/llmprof/api/traces/{tid}").json()
        session_by_id = {}
        for s in sessions["sessions"]:
            sid = s["session_id"]
            session_by_id[sid] = client.get(f"/llmprof/api/sessions/{sid}").json()

    return {
        "ver": traces["ver"],
        "summary": summary,
        "traces": traces,
        "trace": trace_by_id,
        "sessions": sessions,
        "session": session_by_id,
    }


def main() -> None:
    # Use a temp DB so we never read or touch the user's real ~/.llmprof db.
    import tempfile

    upstreams = {"openai": "https://api.openai.com", "anthropic": "https://api.anthropic.com"}
    with tempfile.TemporaryDirectory() as tmp:
        store = SQLiteStore(str(Path(tmp) / "demo.db"))
        seed(store)
        snapshot = build_snapshot(store, upstreams)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    rec = snapshot["summary"]["reclaimable"]
    tot = snapshot["summary"]["totals"]
    print(f"wrote {OUT.relative_to(ROOT)}")
    print(f"  calls={tot['calls']} tokens={tot['tokens']:,} cost=${tot['cost']:.4f}")
    print(f"  sessions={len(snapshot['sessions']['sessions'])} "
          f"traces_with_detail={len(snapshot['trace'])}")
    print(f"  reclaimable=${rec['reclaimable_usd']:.4f} ({rec['pct']}% of spend); "
          f"monthly={rec['monthly_reclaimable_usd']}")


if __name__ == "__main__":
    main()
