from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator

from app.llm.error_classifier import RateLimitInfo


@dataclass
class CooldownSnapshot:
    scope: str
    remaining_seconds: float
    reason: str | None
    retry_after_seconds: float | None
    remaining_requests: int | None
    remaining_tokens: int | None
    limit_requests: int | None
    limit_tokens: int | None

    def public_dict(self) -> dict[str, object]:
        return {
            "scope": self.scope,
            "remaining_seconds": self.remaining_seconds,
            "reason": self.reason,
            "retry_after_seconds": self.retry_after_seconds,
            "remaining_requests": self.remaining_requests,
            "remaining_tokens": self.remaining_tokens,
            "limit_requests": self.limit_requests,
            "limit_tokens": self.limit_tokens,
        }


class CooldownRegistry:
    """Scope-level quota cooldown (organization/project/model).

    The registry stores only durations actually supplied by the provider.  A
    missing reset header means "unknown", which is treated as a long quota
    failure by the classifier; no duration is invented here.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._until: dict[str, float] = {}
        self._reason: dict[str, str] = {}
        self._rate: dict[str, RateLimitInfo] = {}

    def set(
        self,
        scope: str,
        seconds: float | None,
        *,
        reason: str | None = None,
        rate: RateLimitInfo | None = None,
    ) -> None:
        if seconds is None:
            # Unknown reset: remember the rate snapshot but do not fabricate a
            # cooldown deadline.  The caller must treat it as a long failure.
            with self._lock:
                if rate is not None:
                    self._rate[scope] = rate
                if reason is not None:
                    self._reason[scope] = reason
            return
        with self._lock:
            self._until[scope] = self._clock() + max(0.0, seconds)
            if reason is not None:
                self._reason[scope] = reason
            if rate is not None:
                self._rate[scope] = rate

    def remaining(self, scope: str) -> float:
        with self._lock:
            until = self._until.get(scope)
            if until is None:
                return 0.0
            return max(0.0, until - self._clock())

    def is_cooling(self, scope: str) -> bool:
        return self.remaining(scope) > 0.0

    def clear(self, scope: str) -> None:
        with self._lock:
            self._until.pop(scope, None)
            self._reason.pop(scope, None)
            self._rate.pop(scope, None)

    def snapshot(self, scope: str) -> CooldownSnapshot:
        with self._lock:
            rate = self._rate.get(scope)
            return CooldownSnapshot(
                scope=scope,
                remaining_seconds=self.remaining(scope),
                reason=self._reason.get(scope),
                retry_after_seconds=rate.retry_after_seconds if rate else None,
                remaining_requests=rate.remaining_requests if rate else None,
                remaining_tokens=rate.remaining_tokens if rate else None,
                limit_requests=rate.limit_requests if rate else None,
                limit_tokens=rate.limit_tokens if rate else None,
            )


class RequestGate:
    """Bounded concurrency gate for provider calls.

    The gate is intentionally tiny: it limits parallel LLM requests and can
    refuse (rather than queue forever) when the caller's deadline is too short.
    """

    def __init__(self, limit: int = 4, *, max_wait_seconds: float = 5.0) -> None:
        self._semaphore = threading.BoundedSemaphore(max(1, limit))
        self.max_wait_seconds = max(0.0, max_wait_seconds)

    def acquire(self, timeout: float | None = None) -> bool:
        wait = self.max_wait_seconds if timeout is None else timeout
        return self._semaphore.acquire(timeout=max(0.0, wait))

    def release(self) -> None:
        try:
            self._semaphore.release()
        except ValueError:  # pragma: no cover - defensive double release
            pass

    @contextmanager
    def slot(self, timeout: float | None = None) -> Iterator[None]:
        if not self.acquire(timeout):
            raise TimeoutError("provider request gate is saturated")
        try:
            yield
        finally:
            self.release()
