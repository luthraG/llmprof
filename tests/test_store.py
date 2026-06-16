import pytest

from llmprof.store import BaseStore, SQLiteStore, Store, open_store


def test_basestore_is_abstract():
    with pytest.raises(TypeError):
        BaseStore()  # cannot instantiate the contract directly


def test_sqlite_is_the_default_backend(tmp_path):
    s = open_store(str(tmp_path / "d.db"))
    assert isinstance(s, SQLiteStore)
    assert isinstance(s, BaseStore)
    # the backward-compatible alias still points at the SQLite backend
    assert Store is SQLiteStore


def test_open_store_writes_to_the_given_path(tmp_path):
    db = str(tmp_path / "w.db")
    open_store(db).record({"provider": "openai", "model": "gpt-4o",
                           "prompt_tokens": 10, "completion_tokens": 1,
                           "total_tokens": 11, "cost_usd": 0.0})
    assert len(open_store(db).recent(10)) == 1


def test_totals_count_every_call_not_just_the_recent_window(tmp_path):
    """The header KPIs read store.totals(), which must aggregate the whole table.
    Regression: the dashboard used to sum only the last 100 loaded traces, so the
    header undercounted calls/tokens/cost once there were more than 100 calls."""
    s = open_store(str(tmp_path / "t.db"))
    for _ in range(150):
        s.record({"provider": "openai", "model": "gpt-4o", "prompt_tokens": 10,
                  "completion_tokens": 2, "total_tokens": 12, "cost_usd": 0.01})
    assert len(s.recent(100)) == 100          # the list view is capped
    t = s.totals()
    assert t["calls"] == 150                  # totals are not
    assert t["tokens"] == 150 * 12
    assert t["cost"] == pytest.approx(150 * 0.01)


def test_totals_on_empty_store_are_zero(tmp_path):
    t = open_store(str(tmp_path / "e.db")).totals()
    assert t == {"calls": 0, "tokens": 0, "cost": 0.0}


def test_sqlite_url_scheme(tmp_path):
    db = tmp_path / "u.db"
    s = open_store(url=f"sqlite://{db}")
    assert isinstance(s, SQLiteStore) and s.path == str(db)
    assert isinstance(open_store(url="sqlite://:memory:"), SQLiteStore)


def test_env_url_overrides_db_path(tmp_path, monkeypatch):
    env_db = tmp_path / "env.db"
    monkeypatch.setenv("LLMPROF_DB_URL", f"sqlite://{env_db}")
    s = open_store(str(tmp_path / "ignored.db"))  # explicit URL wins over db_path
    assert s.path == str(env_db)


def _dup_trace(ts, save_usd=0.02, cost=0.05):
    """A trace whose only waste is duplicated content worth `save_usd`."""
    return {"ts": ts, "provider": "openai", "model": "gpt-4o", "prompt_tokens": 1000,
            "completion_tokens": 50, "total_tokens": 1050, "cost_usd": cost,
            "analysis": {"findings": [{"severity": "warn",
                                       "title": "Duplicated content in the context",
                                       "reclaimable_tokens": 400, "save_usd": save_usd}]}}


def test_reclaimable_projection_is_gated(tmp_path):
    """A single dense session, however many calls, must not be extrapolated to a
    month; the /mo figure only appears across 2+ distinct days and is averaged per
    active day. Percent + absolute are always shown."""
    base = 1_700_000_000  # fixed epoch so UTC-day bucketing is deterministic

    # a tiny burst: 5 calls in under two minutes -> not projectable
    st = SQLiteStore(str(tmp_path / "rec.db"))
    for i in range(5):
        st.record(_dup_trace(base + i * 20))
    burst = st.reclaimable_summary()
    assert burst["projectable"] is False
    assert burst["monthly_reclaimable_usd"] is None
    assert burst["pct"] > 0 and burst["reclaimable_usd"] > 0  # still trustworthy

    # 60 calls but all on a SINGLE day -> still not projectable (the screenshot case)
    one_day = SQLiteStore(str(tmp_path / "oneday.db"))
    for i in range(60):
        one_day.record(_dup_trace(base + i * 60))  # spans ~1h, one calendar day
    od = one_day.reclaimable_summary()
    assert od["active_days"] == 1
    assert od["projectable"] is False
    assert od["monthly_reclaimable_usd"] is None
    assert od["pct"] > 0  # the trustworthy numbers are still there

    # 60 calls across 2 distinct days -> projectable, averaged per active day
    two_day = SQLiteStore(str(tmp_path / "twoday.db"))
    for i in range(30):
        two_day.record(_dup_trace(base + i * 60))            # day 1
    for i in range(30):
        two_day.record(_dup_trace(base + 86400 + i * 60))    # day 2
    big = two_day.reclaimable_summary()
    assert big["active_days"] == 2
    assert big["projectable"] is True
    # reclaimable = 60 * 0.02 = 1.20; per active day * 30 = 1.20 / 2 * 30 = 18.00
    assert big["monthly_reclaimable_usd"] == 18.0
    assert big["monthly_calls"] == 900


def test_daily_summary_buckets_by_local_day(tmp_path):
    """Daily buckets follow the local wall clock, not UTC, so a late-night call is
    counted on the user's day (matching provider tools), not the previous UTC day."""
    import os
    import time
    old_tz = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Kolkata"  # UTC+5:30
    time.tzset()
    try:
        st = SQLiteStore(str(tmp_path / "tz.db"))
        # 2023-11-14T20:00:00Z is 2023-11-15 01:30 IST -> local day is the 15th
        st.record(_dup_trace(1699992000))
        days = st.daily_summary()
        assert days[-1]["day"] == "2023-11-15"  # local (IST) day, not the UTC 14th
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()


def _tool_trace(ts, shipped, called, *, cost=0.05, cached=0, prompt=20000):
    """A trace shipping `shipped` tool schemas (name -> tokens), calling `called`."""
    children = [{"name": n, "tokens": t, "children": []} for n, t in shipped.items()]
    detail = {"name": "context", "tokens": sum(shipped.values()),
              "children": [{"name": "tool schemas", "tokens": sum(shipped.values()),
                            "children": children}]}
    return {"ts": ts, "provider": "openai", "model": "gpt-4o", "prompt_tokens": prompt,
            "completion_tokens": 10, "total_tokens": prompt + 10, "cost_usd": cost,
            "cached_tokens": cached, "detail": detail, "called_tools": called}


def test_reclaimable_counts_only_tools_never_used_across_window(tmp_path):
    """A tool unused on one call is not waste (agents need their full toolset);
    only tools never used across the whole window are reclaimable, and only once
    there are enough calls to trust 'never'."""
    import time
    now = time.time()
    shipped = {"search": 500, "send_email": 800, "dead_tool": 1200}

    # under the gate: 5 calls, dead_tool never used -> not yet claimed
    few = SQLiteStore(str(tmp_path / "few.db"))
    for i in range(5):
        few.record(_tool_trace(now + i, shipped, ["search"]))
    s = few.reclaimable_summary()
    assert s["reclaimable_usd"] == 0.0  # "never used" not trustworthy yet
    assert s["unused_tools_pending"] == 2  # send_email + dead_tool flagged as pending

    # over the gate: 25 calls, send_email gets used once, dead_tool never
    many = SQLiteStore(str(tmp_path / "many.db"))
    for i in range(25):
        called = ["search", "send_email"] if i == 0 else ["search"]
        many.record(_tool_trace(now + i, shipped, called))
    s2 = many.reclaimable_summary()
    assert s2["reclaimable_usd"] > 0  # dead_tool is genuinely reclaimable now
    assert s2["unused_tools_pending"] == 0
    act = s2["actions"][0]
    assert "1 tools never used" in act["action"] and act["save_usd"] > 0


def test_reclaimable_ranks_dead_tools_dupes_and_caching(tmp_path):
    """The how-to list surfaces each reclaimable source, ranked by dollars."""
    import time
    now = time.time()
    st = SQLiteStore(str(tmp_path / "rank.db"))
    shipped = {"used": 300, "dead": 400}
    for i in range(22):
        tr = _tool_trace(now + i, shipped, ["used"])
        tr["analysis"] = {"findings": [
            {"severity": "warn", "title": "Duplicated content in the context",
             "reclaimable_tokens": 100, "save_usd": 1.50},
            {"severity": "ok", "title": "Prompt caching is active"}]}
        st.record(tr)
    actions = st.reclaimable_summary()["actions"]
    assert actions[0]["action"].startswith("Dedupe")  # $1.50 * 22 dominates
    assert any("never used" in a["action"] for a in actions)
    assert all("caching is active" not in a["action"] for a in actions)


def _prefix_trace(ts, *, cached, save_usd=0.0):
    """A trace whose stable prefix is either served from cache, or flagged uncached."""
    if cached:
        findings = [{"severity": "ok", "title": "Prompt caching is active"}]
    else:
        findings = [{"severity": "tip", "title": "Stable prefix is not cached",
                     "save_usd": save_usd}]
    return {"ts": ts, "provider": "anthropic", "model": "claude-opus-4-8",
            "prompt_tokens": 20000, "completion_tokens": 10, "total_tokens": 20010,
            "cost_usd": 0.05, "cached_tokens": 5000 if cached else 0,
            "analysis": {"findings": findings}}


def test_caching_action_reflects_existing_caching(tmp_path):
    """If caching is already active on some traffic, the action must point at the
    calls that missed cache, not tell the user to 'turn on prompt caching'."""
    base = 1_700_000_000

    # mostly cached, a few calls shipped an uncached prefix
    mixed = SQLiteStore(str(tmp_path / "mixed.db"))
    for i in range(5):
        mixed.record(_prefix_trace(base + i, cached=True))
    for i in range(3):
        mixed.record(_prefix_trace(base + 100 + i, cached=False, save_usd=0.10))
    acts = mixed.reclaimable_summary()["actions"]
    cache_act = next(a for a in acts if "cache" in a["action"].lower())
    assert cache_act["action"] == (
        "Cache the stable prefix on 3 of 8 calls that shipped it uncached.")
    assert not cache_act["action"].startswith("Turn on")

    # no caching anywhere -> the original call-to-action stands
    none = SQLiteStore(str(tmp_path / "none.db"))
    for i in range(4):
        none.record(_prefix_trace(base + i, cached=False, save_usd=0.10))
    acts2 = none.reclaimable_summary()["actions"]
    assert acts2[0]["action"] == (
        "Turn on prompt caching for your stable system + tools prefix.")


def test_session_groups_despite_mutated_middle_context(tmp_path):
    """Agents (Claude Code, Codex) mutate earlier context between calls, so a
    strict prefix chain misses them. Calls sharing a conversation root still group."""
    st = SQLiteStore(str(tmp_path / "agent.db"))
    base = {"provider": "anthropic", "model": "claude-opus-4-8",
            "prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110, "cost_usd": 0.01}
    # same root (system + first user) but the middle diverges -> NOT a strict prefix
    st.record({**base, "msg_fp": ["s:sys", "u:q1", "a:X1"]})
    st.record({**base, "msg_fp": ["s:sys", "u:q1", "a:X2", "u:q2"]})
    st.record({**base, "msg_fp": ["s:sys", "u:q1", "a:X2", "u:q2", "a:Y", "u:q3"]})
    # a different conversation (different first user message) is its own run
    st.record({**base, "msg_fp": ["s:sys", "u:other"]})

    sessions = st.sessions(min_turns=2)
    assert len(sessions) == 1, "the three same-root calls should be one run"
    assert sessions[0]["turns"] == 3
    turns = st.session(sessions[0]["session_id"])
    assert [t["turn"] for t in turns] == [1, 2, 3]


def test_agent_run_with_drifting_system_groups_into_one_session(tmp_path):
    """End to end with the real fingerprint: a Claude-Code-style run that rewrites
    its system block every call still chains into a single multi-turn run, instead
    of showing 'no multi-turn runs' with every call its own session."""
    from llmprof import tokens

    st = SQLiteStore(str(tmp_path / "run.db"))
    base = {"provider": "anthropic", "model": "claude-sonnet-4-6", "prompt_tokens": 1000,
            "completion_tokens": 10, "total_tokens": 1010, "cost_usd": 0.01}
    msgs = [{"role": "user", "content": "build the feature"}]
    for i in range(4):
        payload = {"system": [{"type": "text", "text": f"You are Claude Code. ctx={90 - i * 5}%"}],
                   "messages": list(msgs)}
        fp = tokens.message_fingerprint(payload, "anthropic")
        st.record({**base, "ts": 1000.0 + i, "msg_fp": fp})
        msgs += [{"role": "assistant", "content": f"step {i}"},
                 {"role": "user", "content": f"continue {i}"}]

    sessions = st.sessions(min_turns=2)
    assert len(sessions) == 1, "the drifting-system run must be one session"
    assert sessions[0]["turns"] == 4


def test_clear_wipes_traces(tmp_path):
    st = SQLiteStore(str(tmp_path / "c.db"))
    for _ in range(3):
        st.record({"provider": "openai", "model": "gpt-4o", "prompt_tokens": 10,
                   "completion_tokens": 1, "total_tokens": 11, "cost_usd": 0.0})
    assert len(st.recent(10)) == 3
    assert st.clear() == 3
    assert st.recent(10) == []
    assert st.clear() == 0  # idempotent


def test_postgres_url_errors_clearly_until_backend_exists():
    # the door is open: a postgres URL is recognized and routed, and fails with
    # a clear message instead of silently falling back to SQLite.
    with pytest.raises(RuntimeError, match="Postgres"):
        open_store(url="postgresql://user:pass@localhost/llmprof")
