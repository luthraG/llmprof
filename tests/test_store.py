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


def test_postgres_url_errors_clearly_until_backend_exists():
    # the door is open: a postgres URL is recognized and routed, and fails with
    # a clear message instead of silently falling back to SQLite.
    with pytest.raises(RuntimeError, match="Postgres"):
        open_store(url="postgresql://user:pass@localhost/llmprof")
