# SPDX-License-Identifier: Apache-2.0
"""Circuit breaker per poll group (PLAN.md §7.2).

Three consecutive failures open the circuit. While open, the group is not polled; the
open period doubles on every re-open (starting at one interval) up to a five-minute
ceiling, and a single success closes it and resets the backoff. Time is a monotonic float
so tests can drive it by hand.
"""

from __future__ import annotations

from dataclasses import dataclass

FAILURE_THRESHOLD = 3
BACKOFF_CEILING = 300.0


@dataclass
class CircuitBreaker:
    base_delay: float  # the group's poll interval
    threshold: int = FAILURE_THRESHOLD
    ceiling: float = BACKOFF_CEILING
    consecutive_failures: int = 0
    opened_count: int = 0
    open_until: float | None = None

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_count = 0
        self.open_until = None

    def record_failure(self, now: float) -> float | None:
        """Register a failure. Returns the open-until time if the circuit (re)opened."""
        self.consecutive_failures += 1
        if self.consecutive_failures < self.threshold:
            return None
        delay = min(self.ceiling, max(self.base_delay, 1.0) * (2**self.opened_count))
        self.opened_count += 1
        self.open_until = now + delay
        return self.open_until

    def is_open(self, now: float) -> bool:
        return self.open_until is not None and now < self.open_until

    def seconds_remaining(self, now: float) -> float:
        return max(0.0, (self.open_until or now) - now)
