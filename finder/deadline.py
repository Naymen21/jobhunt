"""A shared time budget for one operation spanning several network calls."""
from __future__ import annotations

import time
from typing import Optional


class DeadlineExceeded(TimeoutError):
    pass


class Deadline:
    def __init__(self, seconds: Optional[float]):
        self._end = None if seconds is None or seconds <= 0 else time.monotonic() + seconds
        self.total = seconds

    def remaining(self) -> float:
        if self._end is None:
            return float("inf")
        return max(0.0, self._end - time.monotonic())

    def expired(self) -> bool:
        return self.remaining() <= 0

    def cap(self, timeout: float) -> float:
        """Per-call timeout, never longer than what's left of the budget."""
        left = self.remaining()
        if left <= 0:
            raise DeadlineExceeded(f"overall deadline of {self.total:g}s reached")
        return min(timeout, left)

    def extend(self, seconds: float) -> None:
        """Push the deadline back (e.g. for time spent waiting on a rate limiter, not the network)."""
        if self._end is not None and seconds > 0:
            self._end += seconds
