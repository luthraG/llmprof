"""Tests for the hosted-demo dataset generator (scripts/demo/gen_demo_data.py).

The demo at /llmprof/try/ is the real dashboard served against a static snapshot.
These tests prove the snapshot is built from llmprof's own ingest/pricing (so the
numbers are real) and that it carries the story the demo is meant to tell: a long
agent run, tools shipped but never called, duplicated context, and cache savings.
The snapshot is generated through the real FastAPI app, so the payload shapes are
guaranteed to match what a running proxy returns.
"""

from __future__ import annotations

import importlib.util
import pathlib
import tempfile

from llmprof.store import SQLiteStore

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "demo" / "gen_demo_data.py"
_spec = importlib.util.spec_from_file_location("gen_demo_data", _SCRIPT)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def _snapshot():
    with tempfile.TemporaryDirectory() as tmp:
        store = SQLiteStore(str(pathlib.Path(tmp) / "demo.db"))
        gen.seed(store)
        upstreams = {"openai": "https://api.openai.com", "anthropic": "https://api.anthropic.com"}
        return gen.build_snapshot(store, upstreams)


def test_snapshot_has_all_five_route_payloads():
    snap = _snapshot()
    assert set(snap) == {"ver", "summary", "traces", "trace", "sessions", "session"}
    assert snap["ver"]  # asset version, taken from the live traces response
    assert set(snap["summary"]) == {"totals", "days", "models", "routes", "reclaimable"}
    assert "traces" in snap["traces"] and "ver" in snap["traces"]


def test_traces_non_empty_and_capped_to_list():
    snap = _snapshot()
    listed = snap["traces"]["traces"]
    assert len(listed) > 0
    # every clickable trace in the list must have a detail payload to open
    listed_ids = {str(t["id"]) for t in listed}
    assert listed_ids == set(snap["trace"])
    # the traces response ver matches the snapshot ver (no reload-loop in demo)
    assert snap["traces"]["ver"] == snap["ver"]


def test_totals_are_positive_real_numbers():
    snap = _snapshot()
    totals = snap["summary"]["totals"]
    assert totals["calls"] > 50          # enough for the monthly projection to engage
    assert totals["tokens"] > 0
    assert totals["cost"] > 0


def test_trace_detail_carries_flame_tree_and_pricing():
    snap = _snapshot()
    for body in snap["trace"].values():
        assert body["context_window"] is not None
        assert body["input_per_1k"] is not None and body["input_per_1k"] > 0
        assert body["output_per_1k"] is not None
        # the flame graph tree the dashboard renders
        assert body["detail"]["name"] == "context"
        assert body["detail"]["tokens"] > 0
        assert "analysis" in body and "findings" in body["analysis"]


def test_demo_tells_the_waste_story():
    snap = _snapshot()
    rec = snap["summary"]["reclaimable"]
    assert rec["reclaimable_usd"] > 0
    assert 0 < rec["pct"] <= 100
    actions = " ".join(a["action"] for a in rec["actions"])
    # the headline reclaimable signals: dead tools, dedupe, and caching
    assert "never used" in actions          # tools shipped but never called
    assert "Dedupe" in actions              # duplicated content
    assert "prefix" in actions              # uncached stable prefix (cache it / turn caching on)


def test_sessions_present_for_timeline():
    snap = _snapshot()
    sessions = snap["sessions"]["sessions"]
    assert len(sessions) >= 2
    # each listed session resolves to its turns, in order
    for s in sessions:
        sid = s["session_id"]
        turns = snap["session"][sid]["turns"]
        assert len(turns) >= 2
        assert [t["turn"] for t in turns] == sorted(t["turn"] for t in turns)


def test_seed_uses_only_known_priced_models():
    """A model that does not resolve in pricing would render '$0' calls and a
    broken cost story, so guard that every demo model is priced."""
    from llmprof import pricing

    snap = _snapshot()
    for m in snap["summary"]["models"]:
        assert pricing.rates(m["model"]) is not None, m["model"]
        assert m["cost"] >= 0
