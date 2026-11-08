"""Job source interface. Every source turns its own response format into `Job`s.

Feed sources (Greenhouse, Lever, Ashby) read one company's public board. The Apify source runs a search.
Network access goes through `FeedClient`, so tests can swap in an httpx.MockTransport."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from ..config import JobSettings
from ..models import Job, WatchCompany


class FeedError(Exception):
    """A feed couldn't be read. The message is shown to the owner as-is."""


@dataclass
class Probe:
    ok: bool  # the board exists
    jobs: int = 0
    company_name: str = ""
    message: str = ""


class FeedClient:
    """Shared HTTP client: identifying User-Agent, timeouts, and a small gap between requests to one host."""

    def __init__(self, settings: JobSettings, transport: Optional[httpx.BaseTransport] = None):
        self.settings = settings
        self.client = httpx.Client(headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
                                   timeout=httpx.Timeout(settings.http_timeout), follow_redirects=True,
                                   transport=transport)
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self.requests = 0

    def close(self) -> None:
        self.client.close()

    def _wait(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._last.get(host, 0.0) + self.settings.feed_delay)
            self._last[host] = slot
        if slot > now:
            time.sleep(slot - now)

    def request(self, method: str, url: str, **kw) -> httpx.Response:
        self._wait(httpx.URL(url).host)
        self.requests += 1
        try:
            return self.client.request(method, url, **kw)
        except httpx.TimeoutException:
            raise FeedError(f"timed out after {self.settings.http_timeout:.0f}s")
        except httpx.HTTPError as e:
            raise FeedError(f"network error: {type(e).__name__}")

    def get_json(self, url: str, **kw):
        resp = self.request("GET", url, **kw)
        if resp.status_code == 404:
            raise FeedError("board not found (404)")
        if resp.status_code >= 400:
            raise FeedError(f"HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError:
            raise FeedError("response wasn't JSON")


class FeedSource:
    name = ""  # greenhouse | lever | ashby
    label = ""

    def fetch(self, company: WatchCompany, client: FeedClient) -> list[Job]:
        raise NotImplementedError

    def probe(self, board: str, client: FeedClient) -> Probe:
        """Does this board exist, and how many jobs does it list?"""
        try:
            jobs = self.fetch(WatchCompany(name=board, ats=self.name, board=board), client)
        except FeedError as e:
            return Probe(False, message=str(e))
        return Probe(True, len(jobs), message=f"{len(jobs)} open jobs")

    def board_url(self, board: str) -> str:
        raise NotImplementedError
