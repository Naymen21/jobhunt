"""Polite, budgeted crawl of a company's own website for addresses at its domain."""
from __future__ import annotations

import heapq
import itertools
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup

from ..config import Settings
from ..http import FetchResult, PoliteFetcher
from ..models import FoundEmail
from .extract import extract_from_html, extract_from_text, merge
from .pdf import pdf_text
from .prioritize import is_crawlable, is_pdf_url, score_url
from .robots import Robots, load_robots
from .sitemap import collect_sitemap_urls

MAX_DEPTH = 3
MAX_CRAWL_DELAY = 10.0  # honour robots Crawl-delay, but don't let one site stall us for minutes
GUESSED_PATHS = ["/contact", "/about", "/team", "/about-us", "/our-team", "/people", "/leadership", "/press"]

Progress = Callable[[dict], None]


@dataclass
class CrawlResult:
    domain: str
    base_url: Optional[str] = None
    emails: list[FoundEmail] = field(default_factory=list)
    pages_crawled: int = 0
    pdfs_parsed: int = 0
    robots_status: str = ""
    robots_detail: str = ""
    blocked: bool = False  # robots.txt (or an unreachable robots.txt) kept us out
    skipped_by_robots: int = 0
    errors: list[str] = field(default_factory=list)
    visited: list[str] = field(default_factory=list)
    stopped: str = ""  # "budget", "exhausted", "cancelled", "unreachable", "blocked"


def _norm(url: str) -> str:
    url, _ = urldefrag(url)
    parts = urlsplit(url)
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return f"{parts.scheme}://{(parts.hostname or '').lower()}{path}" + (f"?{parts.query}" if parts.query else "")


class _Crawl:
    def __init__(self, domain: str, settings: Settings, fetcher: PoliteFetcher,
                 progress: Optional[Progress], cancel: Optional[threading.Event], max_pages: int):
        self.domain = domain
        self.settings = settings
        self.fetcher = fetcher
        self.progress = progress or (lambda e: None)
        self.cancel = cancel or threading.Event()
        self.max_pages = max_pages
        self.result = CrawlResult(domain)
        self.robots: dict[str, Robots] = {}
        self.frontier: list[tuple[int, int, str, int]] = []
        self.queued: set[str] = set()
        self.counter = itertools.count()
        self.found: list[FoundEmail] = []

    # -- helpers -------------------------------------------------------------
    def host_ok(self, url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        return urlsplit(url).scheme in ("http", "https") and (host == self.domain or host.endswith("." + self.domain))

    def robots_for(self, url: str) -> Robots:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.hostname}"
        if origin not in self.robots:
            self.robots[origin] = load_robots(origin, self.fetcher)
            delay = self.robots[origin].crawl_delay
            if delay:
                self.fetcher.delay = max(self.fetcher.delay, min(delay, MAX_CRAWL_DELAY))
        return self.robots[origin]

    def push(self, url: str, depth: int, is_home: bool = False, score: Optional[int] = None) -> None:
        url = _norm(url)
        if url in self.queued or not self.host_ok(url) or not is_crawlable(url) or depth > MAX_DEPTH:
            return
        self.queued.add(url)
        s = score_url(url, is_home) if score is None else score
        heapq.heappush(self.frontier, (-s, next(self.counter), url, depth))

    def emit(self, message: str, url: str = "") -> None:
        self.progress({
            "phase": "crawl", "message": message, "current_url": url,
            "pages_crawled": self.result.pages_crawled, "pages_queued": len(self.frontier),
            "emails_found": len({f.email for f in self.found}),
        })

    # -- main ----------------------------------------------------------------
    def start(self, start_url: Optional[str]) -> Optional[FetchResult]:
        candidates = [start_url] if start_url else [f"https://{self.domain}/", f"https://www.{self.domain}/",
                                                    f"http://{self.domain}/"]
        for url in candidates:
            robots = self.robots_for(url)
            self.result.robots_status, self.result.robots_detail = robots.status, robots.detail
            if robots.status == "unavailable" and "could not fetch" in robots.detail:
                continue  # host not reachable at all; try the next form of the address
            if robots.blocks_everything:
                self.result.blocked = True
                self.result.stopped = "blocked"
                self.result.robots_detail = robots.detail if robots.status != "ok" else "robots.txt disallows crawling this site"
                return None
            self.emit(f"Fetching {url}", url)
            res = self.fetcher.get(url)
            if res.ok:
                return res
            self.result.errors.append(f"{url}: {res.error or res.status}")
        self.result.stopped = "unreachable"
        return None

    def run(self, start_url: Optional[str] = None) -> CrawlResult:
        home = self.start(start_url)
        if home is None:
            return self.result
        self.result.base_url = home.url
        home_url = _norm(home.url)
        self.queued.add(home_url)
        self.handle_page(home_url, home, depth=0)

        # Seeds: sitemap(s), then a few common paths at a modest priority.
        robots = self.robots_for(home.url)
        origin = f"{urlsplit(home.url).scheme}://{urlsplit(home.url).hostname}"
        sitemaps = robots.sitemaps or [origin + "/sitemap.xml"]
        self.emit("Reading sitemap")
        sm_urls = collect_sitemap_urls(sitemaps, self.fetcher, lambda u: self.robots_for(u).allowed(u))
        ranked = sorted({_norm(u) for u in sm_urls if self.host_ok(u)}, key=score_url, reverse=True)
        for u in ranked[:400]:
            self.push(u, depth=1)
        for path in GUESSED_PATHS:
            self.push(origin + path, depth=1, score=20)

        while self.frontier and self.result.pages_crawled < self.max_pages:
            if self.cancel.is_set():
                self.result.stopped = "cancelled"
                break
            _, _, url, depth = heapq.heappop(self.frontier)
            if not self.robots_for(url).allowed(url):
                self.result.skipped_by_robots += 1
                continue
            if is_pdf_url(url) and self.result.pdfs_parsed >= self.settings.crawl_max_pdfs:
                continue
            self.emit(f"Fetching {url}", url)
            res = self.fetcher.get(url)
            if not res.ok:
                if res.status != 404:
                    self.result.errors.append(f"{url}: {res.error or res.status}")
                continue
            if not self.host_ok(res.url):  # redirected off-site
                continue
            self.handle_page(_norm(res.url), res, depth)
        else:
            if not self.result.stopped:
                self.result.stopped = "budget" if self.result.pages_crawled >= self.max_pages else "exhausted"

        self.result.emails = merge(self.found)
        self.emit("Crawl finished")
        return self.result

    def handle_page(self, url: str, res: FetchResult, depth: int) -> None:
        self.result.pages_crawled += 1
        self.result.visited.append(url)
        if res.is_pdf:
            self.result.pdfs_parsed += 1
            self.found += extract_from_text(pdf_text(res.body), url, self.domain, "pdf")
            return
        if not res.is_html:
            return
        html = res.text
        self.found += extract_from_html(html, url, self.domain)
        for a in BeautifulSoup(html, "lxml").select("a[href]"):
            href = a["href"].strip()
            if href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            self.push(urljoin(res.url, href), depth + 1)


def crawl_domain(domain: str, settings: Settings, *, fetcher: Optional[PoliteFetcher] = None,
                 start_url: Optional[str] = None, progress: Optional[Progress] = None,
                 cancel: Optional[threading.Event] = None, max_pages: Optional[int] = None) -> CrawlResult:
    own = fetcher is None
    fetcher = fetcher or PoliteFetcher(settings)
    try:
        return _Crawl(domain.lower(), settings, fetcher, progress, cancel,
                      max_pages or settings.crawl_max_pages).run(start_url)
    finally:
        if own:
            fetcher.close()
