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


def _dup_trace(ts, save_usd=0.02, cost=0.05):
    """A trace whose only waste is duplicated content worth `save_usd`."""
    return {"ts": ts, "provider": "openai", "model": "gpt-4o", "prompt_tokens": 1000,
            "completion_tokens": 50, "total_tokens": 1050, "cost_usd": cost,
            "analysis": {"findings": [{"severity": "warn",
                                       "title": "Duplicated content in the context",
                                       "reclaimable_tokens": 400, "save_usd": save_usd}]}}


def test_reclaimable_projection_is_gated(tmp_path):
    """A short burst must not be extrapolated to a month; percent + absolute are
    always shown, the /mo figure only once there is enough spread-out data."""
    import time

    st = SQLiteStore(str(tmp_path / "rec.db"))
    now = time.time()

    # a tiny burst: 5 calls in 2 minutes -> not projectable
    for i in range(5):
        st.record(_dup_trace(now - 120 + i * 20))
    burst = st.reclaimable_summary()
    assert burst["projectable"] is False
    assert burst["monthly_reclaimable_usd"] is None
    assert burst["pct"] > 0 and burst["reclaimable_usd"] > 0  # still trustworthy

    # plenty of calls across more than half a day -> projectable
    st2 = SQLiteStore(str(tmp_path / "rec2.db"))
    for i in range(60):
        st2.record(_dup_trace(now - 14 * 3600 + i * 800))
    big = st2.reclaimable_summary()
    assert big["projectable"] is True
    assert big["monthly_reclaimable_usd"] is not None and big["monthly_calls"] > 0


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
