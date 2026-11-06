from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable


class BreakerState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class BreakerSnapshot:
    scope: str
    state: BreakerState
    consecutive_failures: int
    opened_at: float | None
    last_failure_at: float | None
    last_success_at: float | None
    half_open_in_flight: int

    def public_dict(self) -> dict[str, object]:
        return {
            "scope": self.scope,
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "opened_at": self.opened_at,
            "last_failure_at": self.last_failure_at,
            "last_success_at": self.last_success_at,
            "half_open_in_flight": self.half_open_in_flight,
        }


class CircuitBreaker:
    """One breaker for ``provider + model + credential scope``.

    Semantic outcomes (valid ``NOT_FOUND``, ``NEEDS_CLARIFICATION``) never reach
    this class: only transport/provider failures call ``record_failure``.  A
    breaker therefore cannot turn an honest empty SQL result into an outage.
    """

    def __init__(
        self,
        scope: str,
        *,
        failure_threshold: int = 5,
        recovery_seconds: float = 60.0,
        half_open_max_calls: int = 1,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.scope = scope
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_seconds = max(0.0, recovery_seconds)
        self.half_open_max_calls = max(1, half_open_max_calls)
        self._clock = clock
        self._lock = threading.RLock()
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._last_failure_at: float | None = None
        self._last_success_at: float | None = None
        self._half_open_in_flight = 0

    @property
    def state(self) -> BreakerState:
        with self._lock:
            self._maybe_half_open()
            return self._state

    def allow(self) -> bool:
        """Reserve a call or reject it without touching the provider."""

        with self._lock:
            self._maybe_half_open()
            if self._state is BreakerState.CLOSED:
                return True
            if self._state is BreakerState.HALF_OPEN:
                if self._half_open_in_flight < self.half_open_max_calls:
                    self._half_open_in_flight += 1
                    return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._state = BreakerState.CLOSED
            self._consecutive_failures = 0
            self._opened_at = None
            self._half_open_in_flight = 0
            self._last_success_at = self._clock()

    def release(self) -> None:
        """Release a half-open reservation without judging the provider.

        Used for outcomes that are neither a provider-health success nor a
        failure (for example a 400 configuration error or a 401 credential
        rotation), so a half-open canary slot cannot leak forever.
        """

        with self._lock:
            if self._half_open_in_flight > 0:
                self._half_open_in_flight -= 1

    def record_failure(self) -> None:
        with self._lock:
            now = self._clock()
            self._last_failure_at = now
            self._consecutive_failures += 1
            if self._state is BreakerState.HALF_OPEN:
                # The canary failed: stay open for another recovery window.
                self._state = BreakerState.OPEN
                self._opened_at = now
                self._half_open_in_flight = 0
                return
            if self._consecutive_failures >= self.failure_threshold:
                self._state = BreakerState.OPEN
                self._opened_at = now

    def reset(self) -> None:
        with self._lock:
            self._state = BreakerState.CLOSED
            self._consecutive_failures = 0
            self._opened_at = None
            self._half_open_in_flight = 0

    def snapshot(self) -> BreakerSnapshot:
        with self._lock:
            self._maybe_half_open()
            return BreakerSnapshot(
                scope=self.scope,
                state=self._state,
                consecutive_failures=self._consecutive_failures,
                opened_at=self._opened_at,
                last_failure_at=self._last_failure_at,
                last_success_at=self._last_success_at,
                half_open_in_flight=self._half_open_in_flight,
            )

    # -- internals -------------------------------------------------------
    def _maybe_half_open(self) -> None:
        if self._state is not BreakerState.OPEN or self._opened_at is None:
            return
        if self._clock() - self._opened_at >= self.recovery_seconds:
            self._state = BreakerState.HALF_OPEN
            self._half_open_in_flight = 0


class CircuitBreakerRegistry:
    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        recovery_seconds: float = 60.0,
        half_open_max_calls: int = 1,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lock = threading.RLock()
        self._breakers: dict[str, CircuitBreaker] = {}
        self._failure_threshold = failure_threshold
        self._recovery_seconds = recovery_seconds
        self._half_open_max_calls = half_open_max_calls
        self._clock = clock

    def for_scope(self, scope: str) -> CircuitBreaker:
        with self._lock:
            breaker = self._breakers.get(scope)
            if breaker is None:
                breaker = CircuitBreaker(
                    scope,
                    failure_threshold=self._failure_threshold,
                    recovery_seconds=self._recovery_seconds,
                    half_open_max_calls=self._half_open_max_calls,
                    clock=self._clock,
                )
                self._breakers[scope] = breaker
            return breaker

    def snapshot(self) -> list[dict[str, object]]:
        with self._lock:
            return [breaker.snapshot().public_dict() for breaker in self._breakers.values()]
