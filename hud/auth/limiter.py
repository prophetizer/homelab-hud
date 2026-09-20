# SPDX-License-Identifier: Apache-2.0
"""Fixed-window failure counter for login attempts, keyed by client IP and by username.

In-memory on purpose: one process, one dashboard. It resets on restart, which only helps an
attacker who can restart the container — and then they have bigger tools.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque


class LoginLimiter:
    def __init__(self, max_failures: int = 10, window_seconds: float = 900.0) -> None:
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> deque[float]:
        q = self._failures[key]
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def retry_after(self, *keys: str, now: float | None = None) -> int:
        """Seconds until any of ``keys`` may try again; 0 when not limited."""
        now = time.monotonic() if now is None else now
        worst = 0
        for key in keys:
            q = self._prune(key, now)
            if len(q) >= self.max_failures:
                worst = max(worst, int(self.window - (now - q[0])) + 1)
        return worst

    def record_failure(self, *keys: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        for key in keys:
            self._prune(key, now).append(now)
        # Sweep every key so the map cannot grow without bound; it holds at most the
        # distinct keys that failed inside one window.
        for key in [k for k in self._failures if not self._prune(k, now)]:
            del self._failures[key]

    def reset(self, *keys: str) -> None:
        for key in keys:
            self._failures.pop(key, None)


__all__ = ["LoginLimiter"]
