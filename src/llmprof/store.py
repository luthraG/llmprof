"""SQLite storage for captured traces. Zero-config, single local file."""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path


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
    msg_fp TEXT
);
"""


class Store:
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
                    cached_tokens, called_tools, session_id, turn, msg_fp)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                ),
            )

    def _row(self, r: sqlite3.Row, with_detail: bool = False) -> dict:
        d = dict(r)
        d["components"] = json.loads(d.get("components") or "{}")
        d["called_tools"] = json.loads(d.get("called_tools") or "[]")
        d.pop("msg_fp", None)  # internal fingerprint, never exposed
        detail = d.pop("detail", None)
        if with_detail:
            d["detail"] = json.loads(detail) if detail else None
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
