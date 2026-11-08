"""Per-domain spacing between SMTP sessions so our IP doesn't get flagged."""
from __future__ import annotations

import threading
import time


class DomainRateLimiter:
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._next_slot: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, domain: str) -> float:
        """Block until this domain may be contacted again. Returns seconds waited.

        Each caller reserves the next free slot before sleeping, so concurrent
        callers for the same domain queue up instead of all firing together.
        """
        domain = domain.lower()
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot.get(domain, 0.0))
            self._next_slot[domain] = slot + self.min_interval
        delay = slot - now
        if delay > 0:
            time.sleep(delay)
        return delay
