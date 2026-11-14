"""In-memory sliding-window rate limit per client IP, for POST /explain.

One process on Render's free plan, so memory is enough; limits reset on restart.
Protects the free Gemini quota from a single visitor or script.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

MINUTE = 60.0
DAY = 86_400.0


class RateLimiter:
    def __init__(
        self,
        per_minute: int,
        per_day: int,
        max_clients: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.per_minute = per_minute
        self.per_day = per_day
        self.max_clients = max_clients
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        """Records a request from ``key`` and returns True, or returns False if over a limit."""
        now = self._clock()
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= self.max_clients:
                self._prune(now)
            hits = self._hits[key] = deque()
        while hits and now - hits[0] >= DAY:
            hits.popleft()
        recent = sum(1 for t in hits if now - t < MINUTE)
        if recent >= self.per_minute or len(hits) >= self.per_day:
            return False
        hits.append(now)
        return True

    def _prune(self, now: float) -> None:
        for key in [k for k, h in self._hits.items() if not h or now - h[-1] >= DAY]:
            del self._hits[key]
        if len(self._hits) >= self.max_clients:  # still full: forget the oldest client
            del self._hits[min(self._hits, key=lambda k: self._hits[k][-1])]
