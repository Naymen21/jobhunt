"""Full posting text for listings that arrive without one (Simplify, Hacker News). Uses the job site's own public
posting API when there is one (Workday, Greenhouse, Lever, Ashby, SmartRecruiters), otherwise the page's JobPosting
data (schema.org JSON-LD), otherwise its main text. robots.txt is checked for every host; results are cached by URL,
so each posting is fetched once."""
from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from .normalize import html_to_text

MIN_USEFUL = 300  # shorter than this is a shell page or a summary, not a posting

GetJson = Callable[[str], Optional[dict]]  # robots-checked JSON GET; None when blocked or failed
GetPage = Callable[[str], Optional[tuple[str, str]]]  # robots-checked HTML GET -> (final url, html)


def _workday(url: str, get_json: GetJson) -> Optional[str]:
    # https://acme.wd5.myworkdayjobs.com/[en-US/]External/job/City/Title_R123 -> /wday/cxs/acme/External/job/City/Title_R123
    p = urlsplit(url)
    parts = [x for x in p.path.split("/") if x]
    if parts and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", parts[0]):
        parts = parts[1:]
    if len(parts) < 3 or "job" not in parts:
        return None
    i = parts.index("job")
    site, rest = parts[i - 1] if i else parts[0], "/".join(parts[i + 1:])
    data = get_json(f"https://{p.netloc}/wday/cxs/{p.netloc.split('.')[0]}/{site}/job/{rest}")
    info = (data or {}).get("jobPostingInfo") or {}
    return html_to_text(info.get("jobDescription") or "") or None


def _greenhouse(url: str, get_json: GetJson) -> Optional[str]:
    m = re.search(r"greenhouse\.io/([\w-]+)/jobs/(\d+)", url)
    q = parse_qs(urlsplit(url).query)
    if m:
        board, jid = m.group(1), m.group(2)
    elif "for" in q and "token" in q:  # embed/job_app?for=board&token=id
        board, jid = q["for"][0], q["token"][0]
    else:
        return None
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{jid}")
    return html_to_text((data or {}).get("content") or "") or None


def _lever(url: str, get_json: GetJson) -> Optional[str]:
    m = re.search(r"jobs\.lever\.co/([\w.-]+)/([0-9a-f-]{36})", url)
    if not m:
        return None
    d = get_json(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}")
    if not d:
        return None
    parts = [d.get("descriptionPlain") or html_to_text(d.get("description") or "")]
    for block in d.get("lists") or []:
        items = html_to_text(block.get("content") or "")
        parts.append(f"{block.get('text', '')}\n{items}")
    parts.append(d.get("additionalPlain") or html_to_text(d.get("additional") or ""))
    return "\n\n".join(x.strip() for x in parts if x and x.strip()) or None


_ashby_boards: dict[str, list] = {}
_ashby_lock = threading.Lock()


def _ashby(url: str, get_json: GetJson) -> Optional[str]:
    m = re.search(r"jobs\.ashbyhq\.com/([^/]+)/([0-9a-f-]{36})", url)
    if not m:
        return None
    org, jid = m.group(1), m.group(2)
    with _ashby_lock:
        if org not in _ashby_boards:
            _ashby_boards[org] = ((get_json(f"https://api.ashbyhq.com/posting-api/job-board/{org}") or {}).get("jobs") or [])
    job = next((j for j in _ashby_boards[org] if j.get("id") == jid), None)
    if not job:
        return None
    return job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml") or "") or None


def _smartrecruiters(url: str, get_json: GetJson) -> Optional[str]:
    m = re.search(r"smartrecruiters\.com/([\w-]+)/(\d+)", url)
    if not m:
        return None
    d = get_json(f"https://api.smartrecruiters.com/v1/companies/{m.group(1)}/postings/{m.group(2)}")
    sections = ((d or {}).get("jobAd") or {}).get("sections") or {}
    parts = [f"{s.get('title', '')}\n{html_to_text(s.get('text') or '')}" for s in sections.values() if s.get("text")]
    return "\n\n".join(parts) or None


def _workable(url: str, get_json: GetJson) -> Optional[str]:
    m = re.search(r"apply\.workable\.com/([\w-]+)/j/(\w+)", url)
    if not m:
        return None
    d = get_json(f"https://apply.workable.com/api/v2/accounts/{m.group(1)}/jobs/{m.group(2)}") or {}
    parts = [html_to_text(d.get(k) or "") for k in ("description", "requirements", "benefits")]
    return "\n\n".join(x for x in parts if x) or None


def _oracle(url: str, get_json: GetJson) -> Optional[str]:
    # https://x.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210774038
    m = re.search(r"https://([^/]+)/hcmUI/CandidateExperience/\w+/sites/([\w-]+)/job/(\d+)", url)
    if not m:
        return None
    d = get_json(f"https://{m.group(1)}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all"
                 f'&onlyData=true&finder=ById;Id="{m.group(3)}",siteNumber={m.group(2)}') or {}
    item = (d.get("items") or [{}])[0]
    parts = [html_to_text(item.get(k) or "") for k in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr",
                                                      "ExternalQualificationsStr")]
    return "\n\n".join(x for x in parts if x) or None


API_HANDLERS = [("myworkdayjobs.com", _workday), ("greenhouse.io", _greenhouse), ("lever.co", _lever),
                ("ashbyhq.com", _ashby), ("smartrecruiters.com", _smartrecruiters),
                ("workable.com", _workable), ("oraclecloud.com", _oracle)]


def polite_getters(delay: float = 1.0) -> tuple[GetJson, GetPage]:
    """robots.txt-checked getters sharing one polite fetcher (same-host requests spaced by `delay` seconds)."""
    from finder.config import settings as finder_settings
    from finder.crawl.robots import load_robots
    from finder.http import PoliteFetcher
    fetcher = PoliteFetcher(finder_settings, delay=delay)
    robots: dict = {}
    lock = threading.Lock()

    def allowed(url: str) -> bool:
        p = urlsplit(url)
        base = f"{p.scheme}://{p.netloc}"
        with lock:
            if base not in robots:
                robots[base] = load_robots(base, fetcher)
        return robots[base].allowed(url)

    def get_json(url: str):
        if not allowed(url):
            return None
        res = fetcher.get(url, max_bytes=3 * 1024 * 1024)
        if not res.ok:
            return None
        try:
            return json.loads(res.text)
        except ValueError:
            return None

    def get_page(url: str):
        if not allowed(url):
            return None
        res = fetcher.get(url, max_bytes=3 * 1024 * 1024)
        return (res.url, res.text) if res.ok and res.is_html else None
    return get_json, get_page


def from_page(html: str) -> Optional[str]:
    """schema.org JobPosting description if the page has one, else the page's main text."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for item in items:
            if isinstance(item, dict) and "JobPosting" in str(item.get("@type")) and item.get("description"):
                text = html_to_text(item["description"])
                if len(text) >= MIN_USEFUL:
                    return text
    for tag in soup(["script", "style", "nav", "header", "footer", "form", "noscript", "svg"]):
        tag.decompose()
    main = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.find("article") or soup.body
    text = html_to_text(str(main)) if main else ""
    return text if len(text) >= MIN_USEFUL else None


def fetch_description(url: str, get_json: GetJson, get_page: GetPage) -> Optional[str]:
    host = urlsplit(url).netloc.lower()
    for marker, handler in API_HANDLERS:
        if marker in host:
            try:
                text = handler(url, get_json)
            except Exception:  # an odd URL shape just falls back to the page
                text = None
            if text and len(text) >= MIN_USEFUL:
                return text
    page = get_page(url)
    return from_page(page[1]) if page else None


def enrich(jobs: list, get_json: GetJson, get_page: GetPage, cache: dict, limit: int = 60, workers: int = 6) -> int:
    """Fill in `description` for jobs that only have a listing summary. Cached URLs are free; at most `limit` new
    fetches per call. Returns how many got a real description. `cache`: url -> text ("" = tried, nothing found)."""
    todo, got = [], 0
    for job in jobs:
        if not job.url:
            continue
        if job.url in cache:
            if cache[job.url]:
                job.description = _with_summary(cache[job.url], job.description)
                got += 1
        elif len(todo) < limit:
            todo.append(job)

    def one(job):
        try:
            return job, fetch_description(job.url, get_json, get_page) or ""
        except Exception:
            return job, ""
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="describe") as pool:
        for job, text in pool.map(one, todo):
            cache[job.url] = text
            if text:
                job.description = _with_summary(text, job.description)
                got += 1
    return got


def _with_summary(text: str, summary: str) -> str:
    """The real posting first; the listing summary (locations, terms) after it, for reference."""
    return text if not summary else f"{text}\n\n---\nFrom the listing: {summary}"
