"""Which public job board (Greenhouse, Lever, Ashby) does a company use, and what's its board name?

Given a company name, a careers page URL, or a board link:
  1. a board link (job-boards.greenhouse.io/riotgames, jobs.lever.co/x, jobs.ashbyhq.com/x) is used directly;
  2. otherwise find the company's domain (the Finder's company-name lookup), read its careers pages
     (robots.txt obeyed) and look for links or embeds pointing at a board;
  3. otherwise guess board names from the company name and confirm each against the board's API;
  4. otherwise report "not found" so the owner can enter it manually.
Every board found is confirmed by actually reading its feed."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlsplit

from finder.config import settings as finder_settings
from finder.crawl.robots import load_robots
from finder.discovery import BadDomain
from finder.http import PoliteFetcher
from finder.resolve import company_tokens, registrable, resolve_company

from .models import ATS_TYPES
from .sources.base import FeedClient
from .sources.feeds import FEEDS

BOARD_PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([A-Za-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([A-Za-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_.-]+)", re.I)),
    ("lever", re.compile(r"api\.(?:eu\.)?lever\.co/v0/postings/([A-Za-z0-9_.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)", re.I)),
    ("ashby", re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_.%-]+)", re.I)),
]
NOT_BOARDS = {"embed", "js", "api", "v1", "v0", "jobs", "boards", "job_board", "postings", "search", "static", "assets"}
GENERIC_WORDS = {"games", "game", "studios", "studio", "entertainment", "interactive", "labs", "technologies", "technology",
                 "software", "digital", "group", "media", "online", "hq"}
CAREERS_PATHS = ["/careers", "/jobs", "/careers/", "/join-us", "/company/careers", "/about/careers", "/"]
MAX_PAGES = 5


@dataclass
class Detection:
    status: str  # found | not_found
    query: str
    ats: Optional[str] = None
    board: Optional[str] = None
    company_name: str = ""
    jobs: int = 0
    how: str = ""  # one line: how it was found
    domain: Optional[str] = None
    careers_url: Optional[str] = None
    tried: list[str] = field(default_factory=list)  # what was checked, shown when nothing is found


def boards_in(text: str) -> list[tuple[str, str]]:
    found = []
    for ats, pat in BOARD_PATTERNS:
        for m in pat.finditer(text or ""):
            board = m.group(1).strip(".").split("?")[0]
            if board.lower() not in NOT_BOARDS and (ats, board) not in found:
                found.append((ats, board))
    return found


def board_guesses(name: str, domain: Optional[str]) -> list[str]:
    tokens = company_tokens(name)
    out = []
    if tokens:
        out += ["".join(tokens), "-".join(tokens)]
        core = [t for t in tokens if t not in GENERIC_WORDS]
        if core and core != tokens:
            out.append("".join(core))
    if domain:
        out.append(registrable(domain).split(".")[0])
    seen, result = set(), []
    for slug in out:
        if slug and slug not in seen:
            seen.add(slug)
            result.append(slug)
    return result[:4]


def default_page_fn() -> Callable[[str], Optional[tuple[str, str]]]:
    """Fetch a page politely (robots.txt per host). Returns (final url, html) or None."""
    fetcher = PoliteFetcher(finder_settings, delay=1.0)
    robots: dict[str, object] = {}

    def fetch(url: str):
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in robots:
            robots[base] = load_robots(base, fetcher)
        if not robots[base].allowed(url):
            return None
        res = fetcher.get(url, max_bytes=2 * 1024 * 1024)
        return (res.url, res.text) if res.ok and res.is_html else None
    return fetch


def _confirm(ats: str, board: str, client: FeedClient, need_jobs: bool = False):
    probe = FEEDS[ats].probe(board, client)
    if probe.ok and (probe.jobs > 0 or not need_jobs):
        return probe
    return None


def detect_ats(query: str, client: FeedClient, *, store=None, resolve_fn: Callable = resolve_company,
               page_fn: Optional[Callable] = None) -> Detection:
    query = (query or "").strip()
    det = Detection("not_found", query)
    if not query:
        det.tried.append("nothing entered")
        return det

    # 1. a board link
    for ats, board in boards_in(query):
        det.tried.append(f"{FEEDS[ats].label} board '{board}' from the link")
        probe = _confirm(ats, board, client)
        if probe:
            return _found(det, ats, board, probe, "from the board link you entered")

    # 2. the company's own careers pages
    url_given = query.startswith(("http://", "https://")) or "/" in query
    name = query
    if url_given:
        url = query if query.startswith("http") else "https://" + query
        det.domain = registrable(urlsplit(url).hostname or "")
        det.careers_url = url
        name = det.domain.split(".")[0]
    else:
        try:
            res = resolve_fn(query, store)
            det.domain = res.domain or (res.candidates[0].domain if res.candidates else None)
            if det.domain:
                det.tried.append(f"company website: {det.domain}"
                                 + ("" if res.domain else " (best guess, not certain)"))
            else:
                det.tried.append("couldn't find the company's website")
        except BadDomain as e:
            det.tried.append(str(e))
    if det.domain or det.careers_url:
        page_fn = page_fn or default_page_fn()
        urls = ([det.careers_url] if det.careers_url else []) + [f"https://{det.domain}{p}" for p in CAREERS_PATHS]
        urls += [f"https://careers.{det.domain}/", f"https://jobs.{det.domain}/"]
        checked = 0
        for url in dict.fromkeys(urls):
            if checked >= MAX_PAGES:
                break
            page = page_fn(url)
            checked += 1
            if not page:
                continue
            final_url, html = page
            for ats, board in boards_in(final_url + "\n" + html):
                probe = _confirm(ats, board, client)
                if probe:
                    det.careers_url = det.careers_url or final_url
                    return _found(det, ats, board, probe, f"linked from {final_url}")
        det.tried.append(f"read {checked} careers page{'s' if checked != 1 else ''}: no board links")

    # 3. guess board names
    # Short names collide across companies ("ramp" on Greenhouse has 1 job; Ramp's real board is on Ashby),
    # so check every board type and keep the biggest board rather than the first hit.
    guesses = board_guesses(name, det.domain)
    hits = []
    for ats in ATS_TYPES:
        for slug in guesses:
            probe = _confirm(ats, slug, client, need_jobs=True)
            if probe:
                hits.append((probe.jobs, ats, slug, probe))
                break  # one board per job-board site is enough
    if hits:
        jobs, ats, slug, probe = max(hits, key=lambda h: h[0])
        others = [f"{FEEDS[a].label} '{s}' ({n} jobs)" for n, a, s, _ in hits if (a, s) != (ats, slug)]
        how = f"guessed board name '{slug}' and confirmed it" + (f"; also found {', '.join(others)}" if others else "")
        return _found(det, ats, slug, probe, how)
    det.tried.append(f"guessed board names {', '.join(guesses) or '(none)'} on Greenhouse, Lever and Ashby")
    return det


def _found(det: Detection, ats: str, board: str, probe, how: str) -> Detection:
    det.status, det.ats, det.board, det.jobs, det.how = "found", ats, board, probe.jobs, how
    det.company_name = probe.company_name
    return det
