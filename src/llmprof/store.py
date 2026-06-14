"""Trace storage.

`BaseStore` is the backend contract the rest of llmprof talks to; `SQLiteStore`
is the default zero-config, single-local-file implementation. Call `open_store()`
to get a backend: it returns SQLite by default, or dispatches on `LLMPROF_DB_URL`
(e.g. a `postgresql://...` URL) so a centralized/team backend can be plugged in
later without touching any call site.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path


class BaseStore(ABC):
    """The storage contract every backend implements.

    Methods return plain dict/list structures with JSON already decoded, so the
    API layer and the SDK never depend on the underlying engine. A new backend
    (Postgres, a remote service, ...) only has to implement these methods.
    """

    @abstractmethod
    def record(self, trace: dict) -> None:
        """Persist one captured call. Recognized keys: ts, provider, model,
        endpoint, status, prompt_tokens, completion_tokens, total_tokens,
        cost_usd, streamed, components, detail, cached_tokens, called_tools,
        msg_fp, session_hint, route, analysis, reclaimable_usd."""
        raise NotImplementedError

    @abstractmethod
    def recent(self, limit: int = 50) -> list[dict]:
        """Most recent calls, newest first (without the heavy detail blobs)."""
        raise NotImplementedError

    @abstractmethod
    def get(self, trace_id: int) -> dict | None:
        """One call with its full detail tree and analysis, or None if absent."""
        raise NotImplementedError

    @abstractmethod
    def daily_summary(self, days: int = 30) -> list[dict]:
        """Per-day totals (oldest to newest), for trend charts."""
        raise NotImplementedError

    @abstractmethod
    def model_summary(self) -> list[dict]:
        """Per-model totals, most expensive first."""
        raise NotImplementedError

    @abstractmethod
    def sessions(self, limit: int = 50, min_turns: int = 2) -> list[dict]:
        """Multi-turn runs, most recent first."""
        raise NotImplementedError

    @abstractmethod
    def session(self, session_id: str) -> list[dict]:
        """Every turn of one run, in order, with its per-component breakdown."""
        raise NotImplementedError

    @abstractmethod
    def routes(self, limit: int = 15) -> list[dict]:
        """Most expensive prompt templates, most expensive first."""
        raise NotImplementedError

    @abstractmethod
    def reclaimable_summary(self) -> dict:
        """Total reclaimable spend, projected to a month from the observed rate."""
        raise NotImplementedError


def default_db_path() -> str:
    base = os.environ.get("LLMPROF_HOME") or os.path.join(Path.home(), ".llmprof")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "llmprof.db")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    provider TEXT,
    model TEXT,
    endpoint TEXT,
    status INTEGER,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    cost_usd REAL,
    streamed INTEGER,
    components TEXT,
    detail TEXT,
    cached_tokens INTEGER,
    called_tools TEXT,
    session_id TEXT,
    turn INTEGER,
    msg_fp TEXT,
    route TEXT,
    analysis TEXT,
    reclaimable_usd REAL,
    cache_write_tokens INTEGER
);
"""


class SQLiteStore(BaseStore):
    """Default backend: a single local SQLite file (WAL mode), zero-config."""

    def __init__(self, path: str | None = None):
        self.path = path or default_db_path()
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        conn.row_factory = sqlite3.Row
        # tolerate concurrent writers (traces are recorded from a threadpool)
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            if self.path != ":memory:":
                conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            # migrate older databases that predate newer columns
            cols = [r[1] for r in conn.execute("PRAGMA table_info(traces)")]
            if "detail" not in cols:
                conn.execute("ALTER TABLE traces ADD COLUMN detail TEXT")
            if "cached_tokens" not in cols:
                conn.execute("ALTER TABLE traces ADD COLUMN cached_tokens INTEGER")
            if "called_tools" not in cols:
                conn.execute("ALTER TABLE traces ADD COLUMN called_tools TEXT")
            if "session_id" not in cols:
                conn.execute("ALTER TABLE traces ADD COLUMN session_id TEXT")
            if "turn" not in cols:
                conn.execute("ALTER TABLE traces ADD COLUMN turn INTEGER")
            if "msg_fp" not in cols:
                conn.execute("ALTER TABLE traces ADD COLUMN msg_fp TEXT")
            if "route" not in cols:
                conn.execute("ALTER TABLE traces ADD COLUMN route TEXT")
            if "analysis" not in cols:
                conn.execute("ALTER TABLE traces ADD COLUMN analysis TEXT")
            if "reclaimable_usd" not in cols:
                conn.execute("ALTER TABLE traces ADD COLUMN reclaimable_usd REAL")
            if "cache_write_tokens" not in cols:
                conn.execute("ALTER TABLE traces ADD COLUMN cache_write_tokens INTEGER")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_traces_session ON traces (session_id)"
            )

    def _resolve_session(self, conn, fp: list, hint: str | None) -> tuple[str, int]:
        """Assign (session_id, turn) for a call.

        An explicit hint (the x-llmprof-session header) wins. Otherwise we chain
        by prefix: the most recent prior call whose fingerprint is a strict
        prefix of this one is the previous turn of the same run, so we inherit
        its session and increment the turn. No match starts a fresh run.
        """
        if hint:
            row = conn.execute(
                "SELECT COALESCE(MAX(turn), 0) FROM traces WHERE session_id = ?", (hint,)
            ).fetchone()
            return hint, (row[0] or 0) + 1
        if fp:
            rows = conn.execute(
                "SELECT session_id, turn, msg_fp FROM traces "
                "WHERE msg_fp IS NOT NULL ORDER BY id DESC LIMIT 200"
            ).fetchall()
            best = None  # (prefix_len, session_id, turn)
            for r in rows:
                prior = json.loads(r["msg_fp"]) if r["msg_fp"] else []
                n = len(prior)
                if 0 < n < len(fp) and fp[:n] == prior and (best is None or n > best[0]):
                    best = (n, r["session_id"], r["turn"])
            if best:
                return best[1], (best[2] or 1) + 1
        return uuid.uuid4().hex[:12], 1

    def record(self, trace: dict) -> None:
        fp = trace.get("msg_fp") or []
        with self._connect() as conn:
            session_id, turn = self._resolve_session(conn, fp, trace.get("session_hint"))
            conn.execute(
                """INSERT INTO traces
                   (ts, provider, model, endpoint, status, prompt_tokens,
                    completion_tokens, total_tokens, cost_usd, streamed, components, detail,
                    cached_tokens, called_tools, session_id, turn, msg_fp, route,
                    analysis, reclaimable_usd, cache_write_tokens)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    trace.get("ts", time.time()),
                    trace.get("provider"),
                    trace.get("model"),
                    trace.get("endpoint"),
                    trace.get("status"),
                    trace.get("prompt_tokens"),
                    trace.get("completion_tokens"),
                    trace.get("total_tokens"),
                    trace.get("cost_usd"),
                    1 if trace.get("streamed") else 0,
                    json.dumps(trace.get("components") or {}),
                    json.dumps(trace.get("detail")) if trace.get("detail") else None,
                    trace.get("cached_tokens"),
                    json.dumps(trace.get("called_tools")) if trace.get("called_tools") else None,
                    session_id,
                    turn,
                    json.dumps(fp) if fp else None,
                    trace.get("route"),
                    json.dumps(trace.get("analysis")) if trace.get("analysis") else None,
                    trace.get("reclaimable_usd"),
                    trace.get("cache_write_tokens"),
                ),
            )

    def _row(self, r: sqlite3.Row, with_detail: bool = False) -> dict:
        d = dict(r)
        d["components"] = json.loads(d.get("components") or "{}")
        d["called_tools"] = json.loads(d.get("called_tools") or "[]")
        d.pop("msg_fp", None)  # internal fingerprint, never exposed
        detail = d.pop("detail", None)
        analysis = d.pop("analysis", None)
        if with_detail:
            d["detail"] = json.loads(detail) if detail else None
            d["analysis"] = json.loads(analysis) if analysis else None
        return d

    def recent(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM traces ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row(r) for r in rows]

    def get(self, trace_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM traces WHERE id = ?", (trace_id,)).fetchone()
        return self._row(row, with_detail=True) if row else None

    def daily_summary(self, days: int = 30) -> list[dict]:
        """Per-day totals (oldest to newest), for trend charts."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT date(ts, 'unixepoch') AS day, COUNT(*) AS calls,
                          COALESCE(SUM(total_tokens), 0) AS tokens,
                          COALESCE(SUM(cost_usd), 0) AS cost
                   FROM traces GROUP BY day ORDER BY day DESC LIMIT ?""",
                (days,),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def sessions(self, limit: int = 50, min_turns: int = 2) -> list[dict]:
        """Multi-turn runs, most recent first. Each row is one agent run; the
        timeline view charts how its context grows turn over turn."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT session_id, COUNT(*) AS turns, MIN(ts) AS started,
                          MAX(ts) AS last, COALESCE(SUM(total_tokens), 0) AS tokens,
                          COALESCE(SUM(cost_usd), 0) AS cost,
                          MAX(model) AS model, MAX(provider) AS provider
                   FROM traces WHERE session_id IS NOT NULL
                   GROUP BY session_id HAVING turns >= ?
                   ORDER BY last DESC LIMIT ?""",
                (min_turns, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def session(self, session_id: str) -> list[dict]:
        """Every turn of one run, in order, with its per-component breakdown."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, ts, turn, model, prompt_tokens, completion_tokens,
                          total_tokens, cost_usd, components
                   FROM traces WHERE session_id = ? ORDER BY turn ASC, id ASC""",
                (session_id,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["components"] = json.loads(d.get("components") or "{}")
            out.append(d)
        return out

    # a monthly projection is only meaningful with enough spread-out data;
    # below this we show the scale-invariant numbers instead of extrapolating
    # a short burst to 24/7 (which produced absurd, wildly swinging figures).
    _PROJECT_MIN_CALLS = 50
    _PROJECT_MIN_SPAN_S = 12 * 3600  # 12 hours

    def reclaimable_summary(self) -> dict:
        """Reclaimable spend across recorded calls. The percent of spend and the
        absolute reclaimable are always trustworthy; a per-month projection is
        only included once there is enough data (`projectable`)."""
        with self._connect() as conn:
            r = conn.execute(
                """SELECT COUNT(*) AS calls,
                          COALESCE(SUM(reclaimable_usd), 0) AS reclaimable,
                          COALESCE(SUM(cost_usd), 0) AS cost,
                          MIN(ts) AS first_ts, MAX(ts) AS last_ts
                   FROM traces WHERE reclaimable_usd IS NOT NULL"""
            ).fetchone()
        calls = r["calls"] or 0
        reclaimable, cost = r["reclaimable"] or 0.0, r["cost"] or 0.0
        span_s = max((r["last_ts"] or 0) - (r["first_ts"] or 0), 0)
        projectable = calls >= self._PROJECT_MIN_CALLS and span_s >= self._PROJECT_MIN_SPAN_S
        out = {
            "calls": calls,
            "reclaimable_usd": round(reclaimable, 6),
            "cost_usd": round(cost, 6),
            "pct": round(reclaimable / cost * 100, 1) if cost else 0,
            "span_seconds": int(span_s),
            "projectable": projectable,
            "monthly_reclaimable_usd": None,
            "monthly_calls": None,
        }
        if projectable:
            days = span_s / 86400
            out["monthly_reclaimable_usd"] = round(reclaimable / days * 30, 2)
            out["monthly_calls"] = int(calls / days * 30)
        return out

    def routes(self, limit: int = 15) -> list[dict]:
        """Most expensive prompt templates (system prompt + tool set), so you can
        see which recurring call shape drives the bill. Most expensive first."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT route, COUNT(*) AS calls,
                          COALESCE(SUM(cost_usd), 0) AS cost,
                          COALESCE(SUM(total_tokens), 0) AS tokens,
                          COALESCE(AVG(total_tokens), 0) AS avg_tokens,
                          MAX(model) AS model
                   FROM traces WHERE route IS NOT NULL
                   GROUP BY route ORDER BY cost DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def model_summary(self) -> list[dict]:
        """Per-model totals, most expensive first."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT model, COUNT(*) AS calls,
                          COALESCE(SUM(total_tokens), 0) AS tokens,
                          COALESCE(SUM(cost_usd), 0) AS cost
                   FROM traces GROUP BY model ORDER BY cost DESC""",
            ).fetchall()
        return [dict(r) for r in rows]


def _sqlite_path_from_url(url: str) -> str | None:
    # sqlite:///abs/path -> /abs/path, sqlite://rel.db -> rel.db, sqlite://:memory:
    rest = url.split("://", 1)[1] if "://" in url else ""
    return rest or None


def open_store(db_path: str | None = None, url: str | None = None) -> BaseStore:
    """Return a storage backend.

    Defaults to SQLite. If a URL is given (argument or the LLMPROF_DB_URL
    environment variable) it is dispatched by scheme, so a centralized backend
    can be swapped in without changing call sites. An explicit URL wins over
    db_path. Today only sqlite:// is bundled; a postgresql:// URL looks for an
    optional llmprof._postgres.PostgresStore and errors clearly if it is absent.
    """
    url = url or os.environ.get("LLMPROF_DB_URL")
    if url:
        scheme = url.split("://", 1)[0].lower() if "://" in url else ""
        if scheme in ("postgres", "postgresql"):
            try:
                from ._postgres import PostgresStore
            except ImportError as exc:
                raise RuntimeError(
                    "LLMPROF_DB_URL points at Postgres, but this build ships no "
                    "Postgres backend. Provide llmprof._postgres.PostgresStore (a "
                    "BaseStore implementation), or unset LLMPROF_DB_URL to use SQLite."
                ) from exc
            return PostgresStore(url)  # pragma: no cover - no bundled implementation yet
        if scheme == "sqlite":
            return SQLiteStore(_sqlite_path_from_url(url))
    return SQLiteStore(db_path)


# Backward-compatible alias: the default backend is SQLite.
Store = SQLiteStore
