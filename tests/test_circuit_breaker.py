"""Tests for circuit breaker pattern."""

import time

from src.api.services.circuit_breaker import CircuitBreaker, CircuitOpenError


def test_closed_state_passes_through():
    cb = CircuitBreaker(failure_threshold=3, reset_timeout=1.0)
    result = cb.call(lambda: "ok")
    assert result == "ok"
    assert cb.state == "closed"


def test_opens_after_threshold_failures():
    cb = CircuitBreaker(failure_threshold=2, reset_timeout=60.0)

    for _ in range(2):
        try:
            cb.call(_raise_error)
        except ValueError:
            pass

    assert cb.state == "open"


def test_open_state_rejects_calls():
    cb = CircuitBreaker(failure_threshold=1, reset_timeout=60.0)

    try:
        cb.call(_raise_error)
    except ValueError:
        pass

    import pytest

    with pytest.raises(CircuitOpenError):
        cb.call(lambda: "should not run")


def test_half_open_after_timeout():
    cb = CircuitBreaker(failure_threshold=1, reset_timeout=0.1)

    try:
        cb.call(_raise_error)
    except ValueError:
        pass

    assert cb.state == "open"
    time.sleep(0.15)

    # Next call should transition to half_open and succeed
    result = cb.call(lambda: "recovered")
    assert result == "recovered"
    assert cb.state == "closed"
    assert cb.failure_count == 0


def test_half_open_failure_reopens():
    cb = CircuitBreaker(failure_threshold=1, reset_timeout=0.1)

    try:
        cb.call(_raise_error)
    except ValueError:
        pass

    time.sleep(0.15)

    try:
        cb.call(_raise_error)
    except ValueError:
        pass

    assert cb.state == "open"


def _raise_error():
    raise ValueError("test error")
