"""While the site is running, step the outreach queue once a minute: schedule approved emails, check for replies every
few minutes, and send at most one due email per step under the warmup/hours/gap rules. Set JOBHUNT_WORKER=0 to turn
it off (tests do). scripts/outreach_worker.py does the same steps from Task Scheduler when the site is closed."""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from storage.outreach_repo import OutreachRepo

log = logging.getLogger("jobhunt.outreach")
STEP_SECONDS = 60


class OutreachWorker:
    def __init__(self):
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="outreach-worker", daemon=True)
        self.repo = OutreachRepo()
        self.last: dict = {}

    @classmethod
    def start_if_enabled(cls) -> Optional["OutreachWorker"]:
        if os.environ.get("JOBHUNT_WORKER", "1") == "0":
            return None
        worker = cls()
        worker.thread.start()
        return worker

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(STEP_SECONDS):
            try:
                self.last = self.repo.tick()
                if self.last.get("sent"):
                    log.info("outreach: sent outbox item %s", self.last["sent"])
            except Exception:  # never let one bad step kill the loop
                log.exception("outreach step failed")
