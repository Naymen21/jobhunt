"""sitemap.xml discovery: follows sitemap indexes, gzip, with hard caps."""
from __future__ import annotations

import gzip
import re
from typing import Callable

from ..http import PoliteFetcher

_LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.I | re.S)
MAX_SITEMAP_FILES = 6
MAX_URLS = 5000

# Child sitemaps worth reading first; product/post/tag sitemaps rarely list people.
_GOOD = ("page", "team", "people", "about", "author", "press", "news", "main", "site", "default")
_BAD = ("product", "tag", "category", "image", "video", "post", "store")


def parse_sitemap(body: bytes) -> tuple[list[str], list[str]]:
    """(page_urls, child_sitemap_urls)"""
    if body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except OSError:
            return [], []
    text = body.decode("utf-8", errors="replace")
    locs = [l.replace("&amp;", "&") for l in _LOC.findall(text)]
    if "<sitemapindex" in text.lower():
        return [], locs
    return locs, []


def _child_rank(url: str) -> int:
    u = url.lower()
    return (0 if any(g in u for g in _GOOD) else 1) + (2 if any(b in u for b in _BAD) else 0)


def collect_sitemap_urls(start: list[str], fetcher: PoliteFetcher, allowed: Callable[[str], bool]) -> list[str]:
    queue = list(dict.fromkeys(start))
    seen, urls = set(), []
    while queue and len(seen) < MAX_SITEMAP_FILES and len(urls) < MAX_URLS:
        sm = queue.pop(0)
        if sm in seen or not allowed(sm):
            continue
        seen.add(sm)
        res = fetcher.get(sm, max_bytes=10 * 1024 * 1024)
        if not res.ok:
            continue
        pages, children = parse_sitemap(res.body)
        urls.extend(pages[: MAX_URLS - len(urls)])
        queue.extend(sorted((c for c in children if c not in seen), key=_child_rank))
    return urls
