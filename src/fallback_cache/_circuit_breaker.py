"""Circuit breaker for Redis connection management."""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any


class CircuitState(enum.Enum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Tracks consecutive failures and short-circuits calls when a threshold is reached.

    States:
        CLOSED  — normal operation, all calls pass through.
        OPEN    — too many failures; calls are blocked until cooldown elapses.
        HALF_OPEN — cooldown elapsed; one probe call is allowed to test recovery.
    """

    def __init__(self, threshold: int = 5, cooldown: float = 30.0) -> None:
        self._threshold = threshold
        self._cooldown = cooldown
        self._failure_count: int = 0
        self._state: CircuitState = CircuitState.CLOSED
        self._last_failure_time: float | None = None

    @property
    def state(self) -> CircuitState:
        """Current circuit breaker state."""
        return self._state

    def should_attempt(self) -> bool:
        """Return True if a call should be attempted."""
        if self._state is CircuitState.CLOSED:
            return True

        if self._state is CircuitState.OPEN:
            if self._last_failure_time is None:
                return True
            elapsed = datetime.now(timezone.utc).timestamp() - self._last_failure_time
            if elapsed >= self._cooldown:
                self._state = CircuitState.HALF_OPEN
                return True
            return False

        # HALF_OPEN — allow the probe
        return True

    def record_success(self) -> None:
        """Record a successful call, resetting the breaker to CLOSED."""
        self._failure_count = 0
        self._state = CircuitState.CLOSED
        self._last_failure_time = None

    def record_failure(self) -> None:
        """Record a failed call, potentially tripping the breaker to OPEN."""
        self._failure_count += 1
        self._last_failure_time = datetime.now(timezone.utc).timestamp()

        if self._state is CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
        elif self._failure_count >= self._threshold:
            self._state = CircuitState.OPEN

    def stats(self) -> dict[str, Any]:
        """Return circuit breaker statistics."""
        return {
            "circuit_breaker_state": self._state.value,
            "circuit_breaker_failure_count": self._failure_count,
        }
