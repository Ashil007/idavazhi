"""Per-visitor rate limits and a cap on how many trips plan at once.

Kept in memory only: a visitor is known by their network address for as long
as a limit's window lasts, and nothing is written down.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque

# kind -> (requests allowed, per this many seconds), per visitor.
LIMITS = {
    "plan": (40, 600),        # a trip every 15 s, sustained for ten minutes
    "search": (240, 300),     # typing in the search box asks as each letter lands
    "prefetch": (120, 300),
    "report": (20, 3600),     # riders' bus times (timings.py)
    "vote": (120, 3600),
    "stop": (30, 3600),       # stop names and new stops (stopnames.py)
    "stopvote": (200, 3600),
}

_lock = threading.Lock()
_hits: dict = {}
_last_sweep = [time.monotonic()]
SWEEP_S = 600.0


class Limited(Exception):
    """Too many requests; `retry_after` is in seconds."""

    def __init__(self, retry_after):
        super().__init__("Too many requests - please wait a moment and try again.")
        self.retry_after = max(1, int(retry_after))


def hit(kind: str, client: str) -> None:
    """Count one request, or raise Limited when the visitor is over the limit."""
    limit, window = LIMITS[kind]
    now = time.monotonic()
    with _lock:
        if now - _last_sweep[0] > SWEEP_S:
            _sweep(now)
        q = _hits.setdefault((kind, client), deque())
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            raise Limited(window - (now - q[0]))
        q.append(now)


def _sweep(now):
    """Forget visitors whose window has passed, so memory does not grow."""
    _last_sweep[0] = now
    for key in [k for k, q in _hits.items() if not q or now - q[-1] > LIMITS[k[0]][1]]:
        del _hits[key]


def reset() -> None:
    """For tests."""
    with _lock:
        _hits.clear()


# Planning is the heavy part (seconds of work each). Past this many at once,
# a new plan waits for a free slot rather than slowing every plan down.
MAX_PLANS = int(os.environ.get("VAZHI_MAX_PLANS", "4"))
PLAN_WAIT_S = 25.0
plan_slots = threading.BoundedSemaphore(MAX_PLANS)
# Map prefetches are a nicety: when this many run, more are skipped.
prefetch_slots = threading.BoundedSemaphore(4)
