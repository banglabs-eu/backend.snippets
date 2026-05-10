"""Admin observability endpoints. Gated to the configured INVITE_ADMIN user.

Returns a single JSON blob the frontend renders as a Grafana-style dashboard
(without actually depending on Grafana). Numbers are point-in-time samples
plus a recent rolling window from the in-memory metrics ring buffer.
"""

import os
from time import time

from fastapi import APIRouter, HTTPException, Request

import db
import metrics
from deps import get_conn, get_username


router = APIRouter(prefix="/admin")

_ADMIN = os.environ.get("INVITE_ADMIN", "adam")


def _require_admin(request: Request) -> None:
    if get_username(request) != _ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")


def _pool_stats(pool) -> dict:
    """Best-effort introspection of psycopg2 ThreadedConnectionPool state."""
    out: dict = {"min": None, "max": None, "in_use": None, "free": None}
    try:
        out["min"] = getattr(pool, "minconn", None)
        out["max"] = getattr(pool, "maxconn", None)
        # Private attrs but stable across psycopg2 versions: _used dict + _pool list.
        used = getattr(pool, "_used", None)
        free = getattr(pool, "_pool", None)
        if used is not None:
            out["in_use"] = len(used)
        if free is not None:
            out["free"] = len(free)
    except Exception:
        pass
    return out


def _table_counts(conn) -> dict:
    counts: dict = {}
    cur = conn.cursor()
    for table in ("users", "snippets", "posts", "sources", "tags"):
        try:
            cur.execute(f"SELECT COUNT(*) AS c FROM {table}")
            row = cur.fetchone()
            counts[table] = int(row["c"]) if row else 0
        except Exception:
            counts[table] = None
    return counts


def _db_size_bytes(conn) -> int | None:
    cur = conn.cursor()
    try:
        cur.execute("SELECT pg_database_size(current_database()) AS sz")
        row = cur.fetchone()
        return int(row["sz"]) if row else None
    except Exception:
        return None


def _slow_recent(samples: list[dict], window_s: float = 600.0) -> list[dict]:
    """Aggregate the last `window_s` seconds by route → avg/max/count, top 10 by avg."""
    cutoff = time() - window_s
    bucket: dict[tuple[str, str], list[float]] = {}
    for s in samples:
        if s["ts"] < cutoff:
            continue
        key = (s["method"], s["path"])
        bucket.setdefault(key, []).append(s["duration_ms"])
    out = []
    for (method, path), durations in bucket.items():
        durations.sort()
        out.append({
            "method": method,
            "path": path,
            "count": len(durations),
            "avg_ms": sum(durations) / len(durations),
            "p95_ms": durations[max(0, int(len(durations) * 0.95) - 1)],
            "max_ms": durations[-1],
        })
    out.sort(key=lambda r: r["avg_ms"], reverse=True)
    return out[:10]


@router.get("/metrics")
def admin_metrics(request: Request):
    _require_admin(request)
    conn = get_conn(request)
    samples = metrics.snapshot()
    now = time()

    # Active users: distinct user_ids that hit any endpoint in the last 5 minutes.
    active_user_ids = {
        s["user_id"] for s in samples
        if s["user_id"] is not None and (now - s["ts"]) <= 300
    }

    # Last 60s aggregate (request rate + p95 + error rate).
    last_minute = [s for s in samples if (now - s["ts"]) <= 60]
    err_count = sum(1 for s in last_minute if s["status"] >= 500)

    # Recent samples for the response-time chart (last 120 samples, in order).
    recent = samples[-120:]

    return {
        "now": now,
        "pool": _pool_stats(request.app.state.pool),
        "active_users_5m": len(active_user_ids),
        "last_minute": {
            "requests": len(last_minute),
            "errors_5xx": err_count,
            "avg_ms": (sum(s["duration_ms"] for s in last_minute) / len(last_minute)) if last_minute else 0.0,
        },
        "totals": _table_counts(conn),
        "db_size_bytes": _db_size_bytes(conn),
        "slow_endpoints": _slow_recent(samples),
        "recent_samples": [
            {"ts": s["ts"], "duration_ms": s["duration_ms"], "status": s["status"]}
            for s in recent
        ],
        "sample_count": len(samples),
    }
