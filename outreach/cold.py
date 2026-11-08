"""Cold pitches: small companies to email with no posting ("I like what you build; could a student intern help?").
Plain functions over company dicts and Finder people, so the web app, the autopilot and tests all share them."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Optional

from .contacts import LEADER, Suggestion

SOURCE_LABELS = {"yc": "YC directory", "builtin": "Built In", "hn": "Hacker News", "watchlist": "Watchlist",
                 "jobs": "From a job", "you": "Added by you"}

# founders, CTOs and engineering leads decide on interns at a 10-60 person company; never HR
TECH_LEAD = re.compile(r"\b(?:cto|chief technology|vp (?:of )?engineering|head of (?:engineering|product|ai|ml|data)|"
                       r"engineering manager|tech(?:nical)? lead|team lead)\b", re.I)


@dataclass
class ColdFilters:
    min_employees: int = 10
    max_employees: int = 60
    local_only: bool = False  # your home area (Settings > Profile)
    keywords: str = ""  # comma separated: "ai, edtech, games"; any one matches name, one-liner, industry or description
    location: str = ""  # text the location must contain, e.g. "Remote", "San Francisco"
    preferred_only: bool = False  # software, AI/ML, data, gaming, edtech, dev tools...
    source: str = ""  # "" = any, else a SOURCE_LABELS key
    include_unknown_size: bool = False
    hide_contacted: bool = True
    show_hidden: bool = False  # companies you marked Not interested

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "ColdFilters":
        f = cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})
        f.min_employees = max(1, int(f.min_employees or 1))
        f.max_employees = max(f.min_employees, int(f.max_employees or f.min_employees))
        return f


def keyword_list(text: str) -> list[str]:
    return [k.strip().lower() for k in re.split(r"[,;\n]", text or "") if k.strip()]


def _has_word(text: str, word: str) -> bool:
    """'ai' matches "AI" but not "said"; 'game' matches "games" and "gaming" (stem prefix)."""
    stem = word[:-1] if len(word) > 4 and word.endswith("s") else word
    stem = stem[:-3] if len(stem) > 6 and stem.endswith("ing") else stem
    stem = stem[:-1] if len(stem) >= 4 and stem.endswith("e") else stem  # game -> gam(e|es|ing)
    return re.search(r"(?<![a-z0-9])" + re.escape(stem), text, re.I) is not None


def matches(company: dict, f: ColdFilters) -> bool:
    """company: {"name", "employees", "local", "location", "industry", "one_liner", "about", "preferred", "source",
    "cold_status", "contacted"}"""
    if company.get("cold_status") == "dismissed" and not f.show_hidden:
        return False
    if f.hide_contacted and company.get("contacted"):
        return False
    n = company.get("employees")
    if n is None:
        if not f.include_unknown_size:
            return False
    elif not f.min_employees <= n <= f.max_employees:
        return False
    if f.local_only and not company.get("local"):
        return False
    if f.preferred_only and not company.get("preferred"):
        return False
    if f.source and company.get("source") != f.source:
        return False
    if f.location and f.location.strip().lower() not in (company.get("location") or "").lower():
        return False
    words = keyword_list(f.keywords)
    if words:
        text = " ".join(company.get(k) or "" for k in ("name", "one_liner", "industry", "about"))
        if not any(_has_word(text, w) for w in words):
            return False
    return True


def rank(company: dict) -> tuple:
    """Local first, then your preferred fields, then the ones a person can most easily say yes to (smaller)."""
    return (not company.get("local"), not company.get("preferred"), company.get("employees") or 999,
            (company.get("name") or "").lower())


def filter_companies(companies: list[dict], f: ColdFilters) -> list[dict]:
    return sorted([c for c in companies if matches(c, f)], key=rank)


def suggest_cold(people: list[dict], exclude: Optional[set[str]] = None, limit: int = 3) -> list[Suggestion]:
    """Who to pitch at a small company: founders/CEO, then CTO / engineering leads, then other named engineers.
    A shared inbox only when no one with a name was found (clearly marked)."""
    exclude = {e.lower() for e in (exclude or set())}
    pool = [p for p in people if p["email"].lower() not in exclude]
    persons = [p for p in pool if p["kind"] == "personal" and p.get("name")]
    by_score = lambda p: (-p["confidence"], p["email"])  # noqa: E731

    def as_s(p, reason):
        return Suggestion(p["email"], p.get("name") or "", p.get("title") or "", p.get("department") or "", p["kind"],
                          p["confidence"], p.get("evidence") or "", p.get("source_url") or "", reason)
    founders = sorted([p for p in persons if LEADER.search(p.get("title") or "")], key=by_score)
    leads = sorted([p for p in persons if p not in founders and TECH_LEAD.search(p.get("title") or "")], key=by_score)
    team = sorted([p for p in persons if p not in founders and p not in leads
                   and p.get("department") in ("engineering", "data", "product", "design")], key=by_score)
    out = [as_s(p, "founder") for p in founders] + [as_s(p, "tech lead") for p in leads] + [as_s(p, "team") for p in team]
    if not out:
        shared = sorted([p for p in pool if p["kind"] == "role"],
                        key=lambda p: (not re.search(r"hello|hi@|founders|team|contact|info|careers|jobs", p["email"]), p["email"]))
        out = [as_s(p, "shared inbox") for p in shared[:1]]
    return out[:limit]


def auto_choice(people: list[dict], exclude: Optional[set[str]] = None) -> Optional[Suggestion]:
    """Autopilot: one founder or tech lead whose address was seen in a public source (85+). Never a guess."""
    for s in suggest_cold(people, exclude):
        if s.kind == "personal" and s.confidence >= 85 and s.reason in ("founder", "tech lead"):
            return s
    return None
