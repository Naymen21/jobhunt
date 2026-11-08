"""Who to suggest for an application, from the addresses the Finder already found at that company:
1 recruiter/HR person + 1-2 people on the team that matches the role (+ the founder/CEO for small startups).
Shared inboxes (info@, careers@) only when no person exists, clearly marked."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

ROLE_AREAS = [
    ("data", r"\bdata\b|analytic|machine learning|\bml\b|\bai\b|scien"),
    ("it", r"\bit\b|information technology|help ?desk|support|technician|network|systems admin"),
    ("design", r"design|ux|ui\b"),
    ("product", r"product|program manag|project manag|operations"),
    ("engineering", r"engineer|developer|software|programm|devops|security|qa\b|test"),
]
# Finder departments that fit each role area (best first), and title words that make someone a closer match.
TEAM_DEPARTMENTS = {
    "engineering": (["engineering"], r"engineer|develop|software|cto|technical"),
    "data": (["engineering", "product"], r"data|analytic|machine learning|\bml\b|\bai\b|scien"),
    "it": (["engineering", "operations"], r"\bit\b|information technology|support|systems|network|infrastructure"),
    "design": (["design", "product"], r"design|ux|ui\b|creative"),
    "product": (["product", "operations", "engineering"], r"product|program|project|operations"),
}
LEADER = re.compile(r"\b(?:founder|co-?founder|ceo|chief executive|owner|president)\b", re.I)


@dataclass
class Suggestion:
    email: str
    name: str
    title: str
    department: str
    kind: str
    confidence: int
    evidence: str
    source_url: str
    reason: str  # recruiter | team | founder | shared inbox


def role_area(job_title: str) -> str:
    t = job_title or ""
    for area, pat in ROLE_AREAS:
        if re.search(pat, t, re.I):
            return area
    return "engineering"


def suggest(people: list[dict], job_title: str, small_startup: bool = False, team_size: int = 2,
            exclude: Optional[set[str]] = None) -> list[Suggestion]:
    """people: [{"email", "name", "title", "department", "kind", "confidence", "evidence", "source_url"}]"""
    exclude = {e.lower() for e in (exclude or set())}
    pool = [p for p in people if p["email"].lower() not in exclude]
    persons = [p for p in pool if p["kind"] == "personal" and p.get("name")]
    by_score = lambda p: (-p["confidence"], p["email"])  # noqa: E731
    out: list[Suggestion] = []
    taken: set[str] = set()

    def add(p, reason):
        taken.add(p["email"])
        out.append(Suggestion(p["email"], p.get("name") or "", p.get("title") or "", p.get("department") or "",
                              p["kind"], p["confidence"], p.get("evidence") or "", p.get("source_url") or "", reason))

    recruiters = sorted([p for p in persons if p.get("department") == "recruiting"], key=by_score)
    if recruiters:
        add(recruiters[0], "recruiter")
    area = role_area(job_title)
    depts, words = TEAM_DEPARTMENTS[area]
    team = [p for p in persons if p["email"] not in taken and p.get("department") in depts]
    team.sort(key=lambda p: (not re.search(words, p.get("title") or "", re.I), depts.index(p["department"]), *by_score(p)))
    for p in team[:team_size]:
        add(p, "team")
    if small_startup:
        leaders = sorted([p for p in persons if p["email"] not in taken and LEADER.search(p.get("title") or "")], key=by_score)
        if leaders:
            add(leaders[0], "founder")
    if not out:  # nobody with a name: fall back to a shared inbox, clearly marked
        shared = sorted([p for p in pool if p["kind"] == "role"],
                        key=lambda p: (not re.search(r"career|jobs|recruit|talent|hr|people", p["email"]), p["email"]))
        if shared:
            add(shared[0], "shared inbox")
    return out
