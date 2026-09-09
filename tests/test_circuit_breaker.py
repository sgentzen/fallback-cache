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


def test_repeated_probes_while_already_half_open_are_allowed():
    """Once HALF_OPEN, should_attempt() keeps returning True until a call is recorded.

    Every other test calls should_attempt() exactly once while HALF_OPEN — the
    call that performs the OPEN -> HALF_OPEN transition. This covers the
    separate branch taken when the breaker is *already* HALF_OPEN, so a second
    probe is not silently blocked while Redis is recovering.
    """
    cb = CircuitBreaker(threshold=3, cooldown=10.0)
    for _ in range(3):
        cb.record_failure()
    cb._last_failure_time -= 15.0

    assert cb.should_attempt() is True          # OPEN -> HALF_OPEN
    assert cb.state is CircuitState.HALF_OPEN

    # No success or failure recorded in between.
    assert cb.should_attempt() is True
    assert cb.should_attempt() is True
    assert cb.state is CircuitState.HALF_OPEN


def test_open_with_no_failure_time_is_treated_as_attemptable():
    """OPEN with no recorded failure time is reachable, and must not crash.

    CircuitBreaker takes no lock, and the cache calls record_success() and
    record_failure() outside its own lock. record_failure() writes
    _last_failure_time and then _state; record_success() writes _state and then
    clears _last_failure_time. If a success lands between those two writes, the
    breaker is left OPEN with _last_failure_time still None.

    Without this guard, should_attempt() would compute
    ``now - self._last_failure_time`` against None and raise TypeError on every
    subsequent cache operation — a hard failure in the very path that exists to
    degrade gracefully. Allowing the attempt lets the next call re-establish a
    real state.
    """
    cb = CircuitBreaker(threshold=3, cooldown=10.0)
    cb._state = CircuitState.OPEN
    cb._last_failure_time = None

    assert cb.should_attempt() is True
    # The breaker is not silently "repaired" into HALF_OPEN by the guard.
    assert cb.state is CircuitState.OPEN
