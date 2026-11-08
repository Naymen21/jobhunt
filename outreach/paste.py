"""Paste people: pull names + job titles out of text the owner copied from a LinkedIn company "People" page or a
search results page. Only the pasted text is read; nothing here (or anywhere in jobhunt) connects to LinkedIn.

Typical pasted block:
    Jane Doe
    · 2nd
    Software Engineer at Acme | Python, Go
    Minneapolis, MN
    12 mutual connections
    Connect
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from finder.names import looks_like_name
from finder.titles import classify, looks_like_title

JUNK = re.compile(
    r"^(?:connect|message|follow|following|pending|view profile|view .*profile|see all.*|show more.*|load more.*|"
    r"\d+(?:st|nd|rd|th)\+?|[·•]\s*\d+(?:st|nd|rd|th)\+?|1st|2nd|3rd\+?|degree connection|\d[\d,]*\s+(?:followers?|connections?)|"
    r"\d+ mutual connections?|.*\bmutual connections?\b.*|.*\band \d+ other mutual.*|people you may know|"
    r"linkedin member|status is (?:online|offline|reachable)|premium|open to work|hiring|verified|"
    r"current:.*|past:.*|summary:.*|skills?:.*|provides services.*|\d+ results?|page \d+.*|next|previous|"
    r"people|all filters|reset|search|home|my network|jobs|messaging|notifications|me|for business)$", re.I)
DEGREE_TAIL = re.compile(r"\s*[·•]\s*(?:1st|2nd|3rd\+?)\s*$|\s+(?:1st|2nd|3rd\+?)\s*(?:degree connection)?$", re.I)
LOCATION = re.compile(r"^[A-Z][\w .'-]+,\s*(?:[A-Z]{2}|[A-Z][a-z]+(?: [A-Z][a-z]+)*)(?:,\s*[A-Z][\w ]+)?$|"
                      r"^(?:greater .* area|.* metropolitan area|.*\b(?:bay area|area|region|county)|united states|remote)$", re.I)


@dataclass
class PastedPerson:
    name: str
    title: str
    department: str
    seniority: str


def _clean(line: str) -> str:
    line = line.replace("​", "").strip()
    line = DEGREE_TAIL.sub("", line)
    return re.sub(r"\s+", " ", line).strip(" ·•|")


def _title_for(line: str, company: str) -> str:
    """'Software Engineer at Acme | Python' -> 'Software Engineer'."""
    t = re.split(r"\s+(?:at|@)\s+" + (re.escape(company) if company else r"\S+") + r"\b|\s+\|\s+|\s+[-–—]\s+(?=[A-Z])", line, maxsplit=1, flags=re.I)[0]
    return t.strip(" ,·•|")[:120]


def parse_people(text: str, company: str = "") -> list[PastedPerson]:
    lines = [_clean(l) for l in (text or "").splitlines()]
    lines = [l for l in lines if l and not JUNK.match(l) and not re.fullmatch(r"[^\w]*", l)]
    people: list[PastedPerson] = []
    seen: set[str] = set()
    i = 0
    while i < len(lines):
        line = lines[i]
        # LinkedIn sometimes repeats the name ("Jane Doe" / "View Jane Doe's profile" / "Jane Doe")
        if looks_like_name(line) and not looks_like_title(line) and not LOCATION.match(line):
            name = line
            title = ""
            for nxt in lines[i + 1: i + 4]:
                if nxt.lower() == name.lower() or LOCATION.match(nxt):
                    continue
                if looks_like_name(nxt) and not looks_like_title(nxt):
                    break  # the next person; this one had no title
                title = _title_for(nxt, company)
                break
            key = name.lower()
            if key not in seen:
                seen.add(key)
                dept, level = classify(title) if title else (None, None)
                people.append(PastedPerson(name, title, dept or "", level or ""))
        i += 1
    return people
