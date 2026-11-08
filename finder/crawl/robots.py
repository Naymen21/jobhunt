"""robots.txt handling (RFC 9309 semantics)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from urllib.robotparser import RobotFileParser

from ..http import PoliteFetcher

AGENT_TOKEN = "JobHuntFinder"


@dataclass
class Robots:
    url: str
    status: str  # "ok", "missing" (4xx: allow all), "unavailable" (5xx/network: disallow all)
    parser: Optional[RobotFileParser] = None
    sitemaps: list[str] = field(default_factory=list)
    crawl_delay: Optional[float] = None
    detail: str = ""

    def allowed(self, url: str) -> bool:
        if self.status == "missing":
            return True
        if self.status == "unavailable" or self.parser is None:
            return False
        return self.parser.can_fetch(AGENT_TOKEN, url)

    @property
    def blocks_everything(self) -> bool:
        if self.status == "unavailable":
            return True
        root = self.url.rsplit("/robots.txt", 1)[0] + "/"
        return not self.allowed(root)


def parse_robots(url: str, text: str) -> Robots:
    parser = RobotFileParser(url)
    parser.parse(text.splitlines())
    delay = parser.crawl_delay(AGENT_TOKEN)
    return Robots(url=url, status="ok", parser=parser, sitemaps=list(parser.site_maps() or []),
                  crawl_delay=float(delay) if delay else None, detail="robots.txt found")


def load_robots(base_url: str, fetcher: PoliteFetcher) -> Robots:
    url = base_url.rstrip("/") + "/robots.txt"
    res = fetcher.get(url, max_bytes=512 * 1024, polite=False)
    if res.error and res.status == 0:
        return Robots(url, "unavailable", detail=f"could not fetch robots.txt ({res.error}); not crawling to be safe")
    if 400 <= res.status < 500:
        return Robots(url, "missing", detail=f"no robots.txt ({res.status}); everything allowed")
    if res.status >= 500:
        return Robots(url, "unavailable", detail=f"robots.txt returned {res.status}; not crawling to be safe")
    return parse_robots(url, res.text)
