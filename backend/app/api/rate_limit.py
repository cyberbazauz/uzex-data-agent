from __future__ import annotations

import math
import threading
import time
from collections import deque


class RateLimiter:
    """Small in-process sliding-window limiter for the public chat endpoint."""

    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = max(1, int(limit))
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            bucket = self._hits.setdefault(key or "unknown", deque())
            while bucket and now - bucket[0] >= self.window_seconds:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry_after = max(
                    1,
                    int(math.ceil(self.window_seconds - (now - bucket[0]))),
                )
                return False, retry_after
            bucket.append(now)
            if len(self._hits) > 10_000:
                self._prune(now)
            return True, 0

    def _prune(self, now: float) -> None:
        stale = [
            key
            for key, bucket in self._hits.items()
            if not bucket or now - bucket[-1] >= self.window_seconds
        ]
        for key in stale:
            self._hits.pop(key, None)
