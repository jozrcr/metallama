"""Performance stats persistence — per-request metrics, RSS samples, events.

Stdlib sqlite3 only. Every public function is fire-and-forget: it must never
raise into a request path or the watchdog loop.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_conn_path: str | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  ts INTEGER, model TEXT, server_started_at REAL,
  prompt_tokens INTEGER, completion_tokens INTEGER,
  duration_ms INTEGER, pp_tps REAL, gen_tps REAL, stream INTEGER);
CREATE TABLE IF NOT EXISTS samples (
  ts INTEGER, server TEXT, rss_mb REAL);
CREATE TABLE IF NOT EXISTS events (
  ts INTEGER, server TEXT, kind TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS idx_req_model_ts ON requests(model, ts);
CREATE INDEX IF NOT EXISTS idx_samples ON samples(server, ts);
CREATE INDEX IF NOT EXISTS idx_events ON events(server, ts);
"""


def _db_path() -> Path:
    raw = os.getenv("METALLAMA_STATS_DB", "stats.db")
    p = Path(raw)
    return p if p.is_absolute() else PROJECT_ROOT / p


def _get_conn() -> sqlite3.Connection:
    global _conn, _conn_path
    path = str(_db_path())
    if _conn is None or _conn_path != path:
        if _conn is not None:
            try:
                _conn.close()
            except sqlite3.Error:
                pass
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        conn.commit()
        _conn, _conn_path = conn, path
    return _conn


def _now_ms() -> int:
    return int(time.time() * 1000)


def _server_started_at(model: str) -> float | None:
    try:
        from .runtime import runtime_processes
        state = runtime_processes.get(model)
        return state.started_at if state else None
    except Exception:
        return None


def record_request(
    model: str,
    usage: dict[str, Any] | None,
    timings: dict[str, Any] | None,
    duration_ms: int,
    stream: bool,
) -> None:
    usage = usage or {}
    timings = timings or {}
    try:
        with _lock:
            _get_conn().execute(
                "INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    _now_ms(),
                    model,
                    _server_started_at(model),
                    usage.get("prompt_tokens"),
                    usage.get("completion_tokens"),
                    duration_ms,
                    timings.get("prompt_per_second"),
                    timings.get("predicted_per_second"),
                    1 if stream else 0,
                ),
            )
            _get_conn().commit()
    except Exception:
        logger.exception("stats: record_request failed")


def record_sample(server: str, rss_mb: float) -> None:
    try:
        with _lock:
            _get_conn().execute(
                "INSERT INTO samples VALUES (?,?,?)", (_now_ms(), server, rss_mb)
            )
            _get_conn().commit()
    except Exception:
        logger.exception("stats: record_sample failed")


def record_event(server: str, kind: str, detail: str = "") -> None:
    try:
        with _lock:
            _get_conn().execute(
                "INSERT INTO events VALUES (?,?,?,?)", (_now_ms(), server, kind, detail)
            )
            _get_conn().commit()
    except Exception:
        logger.exception("stats: record_event failed")


def overview(model: str | None, since_ms: int, limit: int = 2000) -> dict[str, Any]:
    """Aggregates + series for /api/stats/overview. Raises only sqlite errors
    (the endpoint wraps them)."""
    with _lock:
        conn = _get_conn()
        where = "ts >= ?"
        args: list[Any] = [since_ms]
        if model:
            where += " AND model = ?"
            args.append(model)

        agg = conn.execute(
            f"""SELECT COUNT(*), COALESCE(SUM(prompt_tokens),0),
                       COALESCE(SUM(completion_tokens),0),
                       AVG(gen_tps), AVG(pp_tps)
                FROM requests WHERE {where}""",
            args,
        ).fetchone()

        req_series = conn.execute(
            f"""SELECT ts, gen_tps, pp_tps, completion_tokens
                FROM requests WHERE {where}
                ORDER BY ts DESC LIMIT ?""",
            args + [limit],
        ).fetchall()

        rss_series: list = []
        event_series: list = []
        if model:
            rss_series = conn.execute(
                "SELECT ts, rss_mb FROM samples WHERE server=? AND ts>=? ORDER BY ts DESC LIMIT ?",
                (model, since_ms, limit),
            ).fetchall()
            event_series = conn.execute(
                "SELECT ts, kind, detail FROM events WHERE server=? AND ts>=? ORDER BY ts DESC LIMIT ?",
                (model, since_ms, limit),
            ).fetchall()

    return {
        "requests": agg[0],
        "prompt_tokens": agg[1],
        "completion_tokens": agg[2],
        "avg_gen_tps": round(agg[3], 1) if agg[3] is not None else None,
        "avg_pp_tps": round(agg[4], 1) if agg[4] is not None else None,
        "series": {
            "requests": [list(r) for r in reversed(req_series)],
            "rss": [list(r) for r in reversed(rss_series)],
            "events": [list(r) for r in reversed(event_series)],
        },
    }
