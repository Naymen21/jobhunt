"""Decide which URLs are worth spending the page budget on."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

KEYWORDS = {
    "contact": 100, "team": 95, "our-team": 95, "people": 95, "staff": 90, "directory": 85, "leadership": 90,
    "management": 85, "executives": 85, "founders": 80, "board": 70, "about": 85, "about-us": 85, "who-we-are": 80,
    "imprint": 75, "impressum": 75, "press": 75, "newsroom": 75, "media": 70, "press-releases": 70, "news": 45,
    "author": 65, "authors": 65, "contributors": 60, "careers": 55, "jobs": 45, "investor": 60, "investors": 60,
    "investor-relations": 65, "ir": 45, "legal": 45, "privacy": 45, "terms": 25, "security": 35, "partners": 30,
    "company": 50, "blog": 30, "faculty": 80, "lawyers": 85, "attorneys": 85, "professionals": 80, "experts": 70,
    "advisors": 70, "offices": 60, "locations": 50, "support": 25,
}
SKIP_EXT = re.compile(
    r"\.(?:jpe?g|png|gif|webp|svg|ico|bmp|tiff?|css|js|mjs|json|xml|rss|atom|zip|gz|tgz|rar|7z|mp4|mov|avi|"
    r"webm|mp3|wav|ogg|woff2?|ttf|eot|otf|exe|dmg|msi|apk|iso|docx?|xlsx?|pptx?|csv|ics)(?:$|\?)", re.I)
_LANG = re.compile(r"^/(?:[a-z]{2})(?:-[a-z]{2})?/", re.I)
_NOISE = re.compile(r"/(?:tag|tags|category|categories|page/\d+|search|wp-json|feed|cart|checkout|login|signin|"
                    r"sign-in|signup|register|account|cdn-cgi)(?:/|$)", re.I)


def is_crawlable(url: str) -> bool:
    return not SKIP_EXT.search(urlsplit(url).path)


def is_pdf_url(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(".pdf")


def score_url(url: str, is_home: bool = False) -> int:
    if is_home:
        return 1000
    parts = urlsplit(url)
    path = parts.path.lower().rstrip("/")
    segments = [s for s in path.split("/") if s]
    score = 0
    for seg in segments:
        words = set(re.split(r"[-_.]", seg)) | {seg}
        score = max(score, max((KEYWORDS.get(w, 0) for w in words), default=0))
    if is_pdf_url(url):
        score = score + 15 if score else 10
    score -= 6 * max(0, len(segments) - 1)
    if parts.query:
        score -= 25
    if _NOISE.search(path + "/"):
        score -= 60
    lang = _LANG.match(path + "/")
    if lang and not lang.group().lower().startswith("/en"):
        score -= 10  # translated duplicates of pages we probably already have
    return score
