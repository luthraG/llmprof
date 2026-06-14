"""SQLite storage for captured traces. Zero-config, single local file."""

from __future__ import annotations

import json
import os
import sqlite3
import time
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
    called_tools TEXT
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

    def record(self, trace: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO traces
                   (ts, provider, model, endpoint, status, prompt_tokens,
                    completion_tokens, total_tokens, cost_usd, streamed, components, detail,
                    cached_tokens, called_tools)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                ),
            )

    def _row(self, r: sqlite3.Row, with_detail: bool = False) -> dict:
        d = dict(r)
        d["components"] = json.loads(d.get("components") or "{}")
        d["called_tools"] = json.loads(d.get("called_tools") or "[]")
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
