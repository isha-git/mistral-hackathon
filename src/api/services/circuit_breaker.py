"""Simple circuit breaker for protecting external service calls."""

import threading
import time
import logging
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is open and calls are rejected."""

    pass


class CircuitBreaker:
    """Simple circuit breaker to prevent cascading failures.

    States:
        closed  — normal operation, calls pass through
        open    — calls are rejected immediately
        half_open — one trial call is allowed to test recovery
    """

    def __init__(self, failure_threshold: int = 5, reset_timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self._lock = threading.Lock()
        self.failure_count = 0
        self.last_failure_time: float = 0
        self.state = "closed"

    def call(self, func: Callable[..., T], *args, **kwargs) -> T:
        """Execute func through the circuit breaker."""
        with self._lock:
            if self.state == "open":
                if time.time() - self.last_failure_time > self.reset_timeout:
                    self.state = "half_open"
                    logger.info("Circuit breaker transitioning to half_open")
                else:
                    raise CircuitOpenError(
                        f"Circuit breaker is open (failures={self.failure_count})"
                    )

        try:
            result = func(*args, **kwargs)
            with self._lock:
                if self.state == "half_open":
                    self.state = "closed"
                    self.failure_count = 0
                    logger.info("Circuit breaker recovered, closing")
            return result
        except Exception as e:
            with self._lock:
                self.failure_count += 1
                self.last_failure_time = time.time()
                if self.failure_count >= self.failure_threshold:
                    self.state = "open"
                    logger.warning(
                        f"Circuit breaker opened after {self.failure_count} failures"
                    )
            raise e
