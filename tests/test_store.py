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


def test_reclaimable_projection_is_gated(tmp_path):
    """A short burst must not be extrapolated to a month; percent + absolute are
    always shown, the /mo figure only once there is enough spread-out data."""
    import time

    st = SQLiteStore(str(tmp_path / "rec.db"))
    now = time.time()

    # a tiny burst: 5 calls in 2 minutes -> not projectable
    for i in range(5):
        st.record({"ts": now - 120 + i * 20, "provider": "anthropic", "model": "claude-opus-4-8",
                   "prompt_tokens": 1000, "completion_tokens": 50, "total_tokens": 1050,
                   "cost_usd": 0.05, "reclaimable_usd": 0.02})
    burst = st.reclaimable_summary()
    assert burst["projectable"] is False
    assert burst["monthly_reclaimable_usd"] is None
    assert burst["pct"] > 0 and burst["reclaimable_usd"] > 0  # still trustworthy

    # plenty of calls across more than half a day -> projectable
    st2 = SQLiteStore(str(tmp_path / "rec2.db"))
    for i in range(60):
        st2.record({"ts": now - 14 * 3600 + i * 800, "provider": "openai", "model": "gpt-4o",
                    "prompt_tokens": 1000, "completion_tokens": 50, "total_tokens": 1050,
                    "cost_usd": 0.05, "reclaimable_usd": 0.02})
    big = st2.reclaimable_summary()
    assert big["projectable"] is True
    assert big["monthly_reclaimable_usd"] is not None and big["monthly_calls"] > 0


def test_reclaimable_summary_ranks_actionable_fixes(tmp_path):
    """The headline reclaimable number comes with a ranked how-to, aggregated
    from the per-call findings, sorted by dollars saved."""
    st = SQLiteStore(str(tmp_path / "act.db"))
    base = {"provider": "anthropic", "model": "claude-opus-4-8", "prompt_tokens": 1000,
            "completion_tokens": 10, "total_tokens": 1010, "cost_usd": 0.05}
    # unused tool schemas: cheap per call but on every call
    for _ in range(5):
        st.record({**base, "reclaimable_usd": 0.01, "analysis": {"findings": [
            {"severity": "warn", "title": "3 of 12 tools were not called",
             "reclaimable_tokens": 200, "save_usd": 0.01}]}})
    # one big duplicate-content finding worth more dollars
    st.record({**base, "reclaimable_usd": 0.30, "analysis": {"findings": [
        {"severity": "warn", "title": "Duplicated content in the context",
         "reclaimable_tokens": 5000, "save_usd": 0.30},
        {"severity": "ok", "title": "Prompt caching is active"}]}})

    summary = st.reclaimable_summary()
    actions = summary["actions"]
    assert [a["action"][:5] for a in actions][:2] == ["Dedup", "Drop "]  # $0.30 ranks above $0.05
    dedupe = actions[0]
    assert dedupe["calls"] == 1 and dedupe["save_usd"] == 0.30 and dedupe["tokens"] == 5000
    unused = actions[1]
    assert unused["calls"] == 5 and round(unused["save_usd"], 2) == 0.05
    # the "ok" finding is never surfaced as an action
    assert all("caching is active" not in a["action"] for a in actions)


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
