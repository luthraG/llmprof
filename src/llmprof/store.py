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
    components TEXT
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
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def record(self, trace: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO traces
                   (ts, provider, model, endpoint, status, prompt_tokens,
                    completion_tokens, total_tokens, cost_usd, streamed, components)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
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
                ),
            )

    def recent(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM traces ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["components"] = json.loads(d.get("components") or "{}")
            out.append(d)
        return out
