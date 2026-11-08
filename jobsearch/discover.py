"""Discover: find hiring companies (sweet spot 30-100 people) and internship listings from free public sources.

Sources (all free, public, no login, robots.txt respected, terms checked 2026-11-06):
- YC directory via the yc-oss mirror (https://yc-oss.github.io/api): team size, hiring status, location, industry.
- Built In startups for your home state (builtin.com): companies with headcount; read a few pages per day.
- Hacker News "Ask HN: Who is hiring?" via the official HN API: small companies posting jobs; posts that mention
  interns become jobs.
- Simplify's public internship list (github.com/SimplifyJobs): internship listings with accepted degrees.
Not used: Wellfound and Clera (terms forbid automated access), SeedTable (blocks bots), Crunchbase (login/paid).
"""
from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from bs4 import BeautifulSoup

from .models import Job
from .student import US_STATES as US_STATE_NAMES
from .normalize import detect_job_type, detect_work_mode, html_to_text, to_iso

YC_URL = "https://yc-oss.github.io/api/companies/hiring.json"
YC_ALL_URL = "https://yc-oss.github.io/api/companies/all.json"  # every YC company, hiring or not (for cold pitches)
COLD_RANGE = (5, 60)  # small companies kept from the full YC list
BUILTIN_URL = "https://builtin.com/companies/location/na/usa/mn/type/software-startups"  # Minnesota; see builtin_url()


def builtin_url(state: str) -> str:
    """Built In's startup list for a US state (two letters), e.g. "CA"."""
    return f"https://builtin.com/companies/location/na/usa/{state.lower()}/type/software-startups"
HN_USER = "https://hacker-news.firebaseio.com/v0/user/whoishiring.json"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{}.json"
SIMPLIFY_URL = "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json"

SIZE_RANGE = (30, 100)
SMALL_MAX = 100  # "pitch candidate" companies are at or under this size

PREFERRED = re.compile(r"software|saas|\bai\b|artificial intelligence|machine learning|\bml\b|data|analytics|gaming|games|"
                       r"education|edtech|developer tools|devtools|infrastructure|b2b|api|cloud|security|robotics", re.I)


@dataclass
class DiscoveredCompany:
    name: str
    source: str  # yc | builtin | hn
    source_url: str
    website: str = ""
    employees: Optional[int] = None
    employees_source: str = ""
    location: str = ""
    local: bool = False
    industry: str = ""
    one_liner: str = ""
    hiring: bool = True
    preferred: bool = False
    about: str = ""

    @property
    def rank(self) -> tuple:
        """Local first, then preferred industries, then closest to ~50 people."""
        size_gap = abs((self.employees or 999) - 50)
        return (not self.local, not self.preferred, size_gap, self.name.lower())


@dataclass
class DiscoverResult:
    companies: list[DiscoveredCompany] = field(default_factory=list)
    jobs: list[Job] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # one line per source: ok / error


def is_local(location: str) -> bool:
    """In your home area (Settings > Profile). No home area set: nothing is Local."""
    from .student import is_local as in_home_area
    return in_home_area(location)


def in_size_range(n: Optional[int], lo: int = SIZE_RANGE[0], hi: int = SIZE_RANGE[1]) -> bool:
    return n is not None and lo <= n <= hi


# --- YC ---------------------------------------------------------------------------------------------------------

def parse_yc(data: list[dict], lo: int = SIZE_RANGE[0], hi: int = SIZE_RANGE[1]) -> list[DiscoveredCompany]:
    out = []
    for c in data or []:
        size = c.get("team_size")
        if not c.get("isHiring") or (c.get("status") or "Active") != "Active" or not in_size_range(size, lo, hi):
            continue
        topics = " ".join([c.get("industry") or "", c.get("subindustry") or "", " ".join(c.get("industries") or []),
                           " ".join(c.get("tags") or []), c.get("one_liner") or ""])
        loc = c.get("all_locations") or ""
        out.append(DiscoveredCompany(
            name=c.get("name") or "", source="yc", source_url=c.get("url") or f"https://www.ycombinator.com/companies/{c.get('slug')}",
            website=c.get("website") or "", employees=size, employees_source="YC directory", location=loc, local=is_local(loc),
            industry=", ".join(c.get("industries") or [])[:120], one_liner=(c.get("one_liner") or "")[:200],
            preferred=bool(PREFERRED.search(topics))))
    return out


def parse_yc_all(data: list[dict], lo: int = COLD_RANGE[0], hi: int = COLD_RANGE[1]) -> list[DiscoveredCompany]:
    """Every active YC company with lo-hi people, hiring or not: candidates for a cold pitch."""
    out = []
    for c in data or []:
        size = c.get("team_size")
        if (c.get("status") or "Active") != "Active" or not in_size_range(size, lo, hi):
            continue
        topics = " ".join([c.get("industry") or "", c.get("subindustry") or "", " ".join(c.get("industries") or []),
                           " ".join(c.get("tags") or []), c.get("one_liner") or ""])
        loc = c.get("all_locations") or ""
        if "Remote" in (c.get("regions") or []) and "remote" not in loc.lower():
            loc = f"{loc} · Remote" if loc else "Remote"
        out.append(DiscoveredCompany(
            name=c.get("name") or "", source="yc", source_url=c.get("url") or f"https://www.ycombinator.com/companies/{c.get('slug')}",
            website=c.get("website") or "", employees=size, employees_source="YC directory", location=loc, local=is_local(loc),
            industry=", ".join((c.get("industries") or []) + (c.get("tags") or []))[:200],
            one_liner=(c.get("one_liner") or "")[:200], hiring=bool(c.get("isHiring")),
            preferred=bool(PREFERRED.search(topics)), about=(c.get("long_description") or "")[:1200]))
    return out


# --- Built In (your home state) --------------------------------------------------------------------------------------------

def parse_builtin(html: str, state: str = "MN") -> list[DiscoveredCompany]:
    soup = BeautifulSoup(html or "", "lxml")
    out = []
    for card in soup.select("div.company-content-area"):
        text = " ".join(card.get_text(" ").split())
        link = card.find("a", href=re.compile(r"^/company/"))
        title = card.find(["h2", "h3"])
        name = " ".join(title.get_text(" ").split()) if title else (link.get_text(" ").strip() if link else "")
        if not name:
            continue
        m = re.search(r"([\d,]+)\s+Employees", text)
        industry = text[len(name):].split(" Save ")[0].strip(" •")[:120]
        out.append(DiscoveredCompany(
            name=name, source="builtin", source_url="https://builtin.com" + link["href"] if link else BUILTIN_URL,
            employees=int(m.group(1).replace(",", "")) if m else None, employees_source="Built In" if m else "",
            location=f"{US_STATE_NAMES.get(state, state).title()} (Built In {state})", local=is_local(US_STATE_NAMES.get(state, state)), industry=industry,
            one_liner=re.sub(r".*?Hiring Now\s*", "", text)[:200] if "Hiring Now" in text else "",
            hiring="Hiring Now" in text, preferred=bool(PREFERRED.search(text))))
    return out


# --- Hacker News "Who is hiring" --------------------------------------------------------------------------------------

_HN_HEAD = re.compile(r"^\s*([^|\n]{2,60}?)\s*\|(.+)$")


def parse_hn_comment(item: dict, thread_url: str) -> tuple[Optional[DiscoveredCompany], Optional[Job]]:
    """First line convention: 'Company | Role(s) | Location | ONSITE/REMOTE | ...'"""
    if not item or item.get("deleted") or item.get("dead") or not item.get("text"):
        return None, None
    text = html_to_text(html_lib.unescape(item["text"]).replace("<p>", "\n\n"))
    first = text.splitlines()[0] if text else ""
    m = _HN_HEAD.match(first)
    if not m:
        return None, None
    company = re.sub(r"\s*\(.*?\)\s*$", "", m.group(1)).strip()
    parts = [p.strip() for p in first.split("|")]
    location = next((p for p in parts[1:] if re.search(r"remote|onsite|on-site|hybrid|,|\b[A-Z]{2}\b|city|york|francisco|london", p, re.I)), "")
    url = f"https://news.ycombinator.com/item?id={item['id']}"
    site = re.search(r"https?://(?!news\.ycombinator)[^\s)\]]+", text)
    comp = DiscoveredCompany(name=company, source="hn", source_url=url, website=site.group(0).rstrip(".,") if site else "",
                             location=location, local=is_local(first), one_liner=first[:200],
                             preferred=bool(PREFERRED.search(text)))
    job = None
    if re.search(r"\bintern(?:s|ship|ships)?\b", text, re.I):
        role = next((p for p in parts[1:] if re.search(r"intern", p, re.I)), "") or "Internship (see post)"
        job = Job(source="hn", source_id=str(item["id"]), company=company, title=role[:120], url=url, apply_url=url,
                  location=location, work_mode=detect_work_mode(location, role, text), job_type="internship",
                  posted_at=to_iso(item.get("time")), description=text[:6000])
    return comp, job


# --- Simplify internship list ----------------------------------------------------------------------------------------

def parse_simplify(data: list[dict], max_age_days: int = 150, now: Optional[datetime] = None) -> list[Job]:
    now = now or datetime.now(timezone.utc)
    jobs = []
    for x in data or []:
        if not x.get("active") or not x.get("is_visible", True):
            continue
        posted = x.get("date_posted") or x.get("date_updated")
        if posted and (now.timestamp() - posted) > max_age_days * 86400:
            continue
        locs = "; ".join(x.get("locations") or [])
        degrees = x.get("degrees") or []
        bits = [f"Internship at {x.get('company_name')}: {x.get('title')}.",
                f"Locations: {locs or 'not listed'}.",
                f"Terms: {', '.join(x.get('terms') or []) or 'not listed'}."]
        if degrees:
            # phrased so the eligibility flags read it like a posting would
            bits.append(f"Candidates must be currently pursuing a {' or '.join(degrees)} degree.")
        else:
            bits.append("Degree level not listed on the Simplify list; check the posting.")
        if x.get("sponsorship"):
            bits.append(f"Sponsorship: {x['sponsorship']}.")
        jobs.append(Job(source="simplify", source_id=str(x.get("id")), company=x.get("company_name") or "",
                        title=x.get("title") or "", url=x.get("url") or "", apply_url=x.get("url") or "", location=locs,
                        work_mode=detect_work_mode(locs, x.get("title") or ""),
                        job_type=detect_job_type(x.get("title") or "", "internship") or "internship",
                        posted_at=to_iso(posted), description=" ".join(bits), department=x.get("category") or ""))
    return jobs


# --- size from a company's own site ------------------------------------------------------------------------------------

_HEADCOUNT = re.compile(r"\b(?:team of|over|more than|nearly|about|around|~)?\s*(\d{1,4})\+?\s*(?:employees|people|team members|"
                        r"teammates|staff|engineers and designers)\b", re.I)


def headcount_from_text(text: str) -> Optional[int]:
    """'We're a team of 45 people' -> 45. Ignores customer/user counts and implausible numbers."""
    best = None
    for m in _HEADCOUNT.finditer(text or ""):
        before = (text[max(0, m.start() - 40): m.start()]).lower()
        if re.search(r"customer|user|client|million|countries|students|served|trusted", before):
            continue
        n = int(m.group(1))
        if 2 <= n <= 5000:
            best = n if best is None else max(best, n)
    return best
