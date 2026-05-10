"""Tiny in-memory metrics: ring buffer of recent requests.

Per-process, no external dependency. The admin panel polls these to render
load + response-time graphs. State is lost on restart, which is fine — these
are observability hints, not durable telemetry.
"""

from collections import deque
from threading import Lock
from time import time
from typing import Deque

# Fixed cap so memory stays bounded across long-running uvicorn workers.
_MAX_SAMPLES = 500

# Sample = (timestamp, path, method, status, duration_ms, user_id_or_none).
_samples: Deque[tuple[float, str, str, int, float, int | None]] = deque(maxlen=_MAX_SAMPLES)
_lock = Lock()


def record(path: str, method: str, status: int, duration_ms: float, user_id: int | None) -> None:
    with _lock:
        _samples.append((time(), path, method, status, duration_ms, user_id))


def snapshot() -> list[dict]:
    """Copy out the current ring as plain dicts. Caller is responsible for filtering."""
    with _lock:
        return [
            {
                "ts": ts,
                "path": path,
                "method": method,
                "status": status,
                "duration_ms": duration_ms,
                "user_id": user_id,
            }
            for (ts, path, method, status, duration_ms, user_id) in _samples
        ]
