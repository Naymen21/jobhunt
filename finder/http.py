"""Polite HTTP client for crawling: identifying User-Agent, timeouts, size cap, per-host delay."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

import httpx

from .config import Settings


@dataclass
class FetchResult:
    url: str  # final URL after redirects
    status: int
    content_type: str
    body: bytes
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    @property
    def is_html(self) -> bool:
        return "html" in self.content_type or (not self.content_type and self.body.lstrip()[:1] == b"<")

    @property
    def is_pdf(self) -> bool:
        return "pdf" in self.content_type or self.body[:5] == b"%PDF-"


class PoliteFetcher:
    """One fetcher per crawl. Spaces requests to the same host by `delay` seconds."""

    def __init__(self, settings: Settings, delay: Optional[float] = None,
                 transport: Optional[httpx.BaseTransport] = None):
        self.settings = settings
        self.delay = settings.crawl_delay if delay is None else delay
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self.client = httpx.Client(
            headers={"User-Agent": settings.user_agent, "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.5"},
            timeout=httpx.Timeout(settings.http_timeout),
            follow_redirects=True,
            transport=transport,
        )
        self.requests = 0

    def close(self) -> None:
        self.client.close()

    def _wait(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._last.get(host, 0.0) + self.delay)
            self._last[host] = slot
        if slot > now:
            time.sleep(slot - now)

    def get(self, url: str, max_bytes: Optional[int] = None, polite: bool = True) -> FetchResult:
        max_bytes = max_bytes or self.settings.crawl_max_bytes
        if polite:
            self._wait(urlsplit(url).hostname or "")
        self.requests += 1
        try:
            with self.client.stream("GET", url) as resp:
                ctype = resp.headers.get("content-type", "").lower()
                declared = int(resp.headers.get("content-length") or 0)
                if declared > max_bytes:
                    return FetchResult(str(resp.url), resp.status_code, ctype, b"", f"too large ({declared} bytes)")
                chunks, size = [], 0
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        return FetchResult(str(resp.url), resp.status_code, ctype, b"", f"too large (> {max_bytes} bytes)")
                    chunks.append(chunk)
                return FetchResult(str(resp.url), resp.status_code, ctype, b"".join(chunks))
        except httpx.TimeoutException:
            return FetchResult(url, 0, "", b"", f"timed out after {self.settings.http_timeout}s")
        except httpx.HTTPError as e:
            return FetchResult(url, 0, "", b"", f"{type(e).__name__}: {e}")
