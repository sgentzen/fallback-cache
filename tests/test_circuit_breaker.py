"""Unit tests for the CircuitBreaker state machine."""
from fallback_cache._circuit_breaker import CircuitBreaker, CircuitState


def test_starts_in_closed_state():
    cb = CircuitBreaker(threshold=3, cooldown=10.0)
    assert cb.state is CircuitState.CLOSED


def test_should_attempt_when_closed():
    cb = CircuitBreaker(threshold=3, cooldown=10.0)
    assert cb.should_attempt() is True


def test_stays_closed_below_threshold():
    cb = CircuitBreaker(threshold=3, cooldown=10.0)
    cb.record_failure()
    cb.record_failure()
    assert cb.state is CircuitState.CLOSED
    assert cb.should_attempt() is True


def test_opens_at_threshold():
    cb = CircuitBreaker(threshold=3, cooldown=10.0)
    for _ in range(3):
        cb.record_failure()
    assert cb.state is CircuitState.OPEN


def test_should_not_attempt_when_open():
    cb = CircuitBreaker(threshold=3, cooldown=10.0)
    for _ in range(3):
        cb.record_failure()
    assert cb.should_attempt() is False


def test_transitions_to_half_open_after_cooldown():
    cb = CircuitBreaker(threshold=3, cooldown=10.0)
    for _ in range(3):
        cb.record_failure()
    # Simulate cooldown elapsed
    cb._last_failure_time -= 15.0
    assert cb.should_attempt() is True
    assert cb.state is CircuitState.HALF_OPEN


def test_success_in_half_open_resets_to_closed():
    cb = CircuitBreaker(threshold=3, cooldown=10.0)
    for _ in range(3):
        cb.record_failure()
    cb._last_failure_time -= 15.0
    cb.should_attempt()  # transitions to HALF_OPEN
    cb.record_success()
    assert cb.state is CircuitState.CLOSED
    assert cb.should_attempt() is True


def test_failure_in_half_open_returns_to_open():
    cb = CircuitBreaker(threshold=3, cooldown=10.0)
    for _ in range(3):
        cb.record_failure()
    cb._last_failure_time -= 15.0
    cb.should_attempt()  # transitions to HALF_OPEN
    cb.record_failure()
    assert cb.state is CircuitState.OPEN


def test_record_success_resets_failure_count():
    cb = CircuitBreaker(threshold=5, cooldown=10.0)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    # Should not trip after 3 more failures (total 3, not 5)
    for _ in range(3):
        cb.record_failure()
    assert cb.state is CircuitState.CLOSED


def test_stats_output():
    cb = CircuitBreaker(threshold=3, cooldown=10.0)
    stats = cb.stats()
    assert stats["circuit_breaker_state"] == "closed"
    assert stats["circuit_breaker_failure_count"] == 0


def test_stats_after_tripping():
    cb = CircuitBreaker(threshold=3, cooldown=10.0)
    for _ in range(3):
        cb.record_failure()
    stats = cb.stats()
    assert stats["circuit_breaker_state"] == "open"
    assert stats["circuit_breaker_failure_count"] == 3
