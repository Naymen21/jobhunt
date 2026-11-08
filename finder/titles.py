"""Job titles: recognise them, then map to a department and seniority with keyword rules (no AI)."""
from __future__ import annotations

import re
from typing import Optional

from .names import ascii_fold

DEPARTMENTS = ["recruiting", "engineering", "design", "product", "executive", "marketing", "sales", "operations", "other"]
DEPARTMENT_LABELS = {
    "recruiting": "Recruiting & HR", "engineering": "Engineering", "design": "Design", "product": "Product",
    "executive": "Leadership", "marketing": "Marketing", "sales": "Sales", "operations": "Operations", "other": "Other",
}
SENIORITIES = ["executive", "senior", "junior"]
SENIORITY_LABELS = {"executive": "Executive", "senior": "Senior", "junior": "Junior"}

# Words that make a short line read as a job title.
TITLE_WORDS = {
    "architect", "architecture", "director", "senior", "junior", "landscape", "interior", "manager", "managing",
    "engineer", "engineering", "designer", "design", "partner", "associate", "principal", "lead", "assistant",
    "intern", "consultant", "analyst", "specialist", "coordinator", "officer", "president", "vice", "chief",
    "executive", "founder", "co-founder", "cofounder", "owner", "advisor", "adviser", "counsel", "attorney",
    "lawyer", "paralegal", "accountant", "developer", "scientist", "researcher", "professor", "lecturer", "writer",
    "producer", "artist", "curator", "administrator", "secretary", "treasurer", "chair", "chairman", "chairwoman",
    "planner", "urbanist", "recruiter", "recruiting", "recruitment", "talent", "hr", "people", "marketing", "sales",
    "account", "product", "operations", "ops", "head", "vp", "ceo", "cto", "cfo", "coo", "cmo", "cpo", "chro", "cio",
    "ciso", "svp", "evp", "avp", "editor", "strategist", "technician", "programmer", "devops", "sre", "qa",
    "tester", "support", "success", "representative", "rep", "bdr", "sdr", "staff", "fellow", "trainee",
    "graduate", "apprentice", "controller", "bookkeeper", "receptionist", "communications", "pr", "brand",
    "content", "copywriter", "illustrator", "animator", "modeler", "artist", "photographer", "generalist",
    "business", "development", "growth", "partnerships", "finance", "legal", "compliance", "security", "it",
    "data", "software", "hardware", "systems", "network", "infrastructure", "platform", "frontend", "backend",
    "full-stack", "fullstack", "mobile", "web", "cloud", "machine", "learning", "ml", "ai", "ux", "ui",
}

# (department, keywords). Keywords match whole words/phrases; a trailing * matches a word prefix
# ("recruit*" -> recruiter, recruiting). First matching department wins, so order matters:
# recruiting before operations ("people operations"), engineering before design ("software architect").
_DEPT_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("recruiting", ("recruit*", "talent*", "human resources", "hr", "hrbp", "people partner", "people operations",
                    "people ops", "people & culture", "people and culture", "chief people", "head of people",
                    "sourcer", "hiring", "chro", "employer brand", "learning and development", "l&d",
                    "director of people", "people director", "people & strategy", "people and strategy")),
    ("executive", ("chief executive", "ceo", "co-founder", "cofounder", "founder", "president", "managing director",
                   "managing partner", "general manager", "owner", "chairman", "chairwoman", "chair of the board",
                   "board member", "coo", "chief operating")),
    ("engineering", ("software", "engineer*", "developer*", "programmer*", "devops", "sre", "site reliability",
                     "frontend", "front-end", "backend", "back-end", "full stack", "full-stack", "fullstack",
                     "mobile", "ios", "android", "data scientist", "machine learning", "ml", "ai", "qa",
                     "quality assurance", "test automation", "infrastructure", "platform", "cto",
                     "chief technology", "technical director", "tech lead", "technical lead", "solutions architect",
                     "software architect", "cloud architect", "systems architect", "it", "sysadmin", "network",
                     "gameplay", "code contributor")),
    ("design", ("design*", "architect*", "ux", "ui", "user experience", "user interface", "illustrat*", "animat*",
                "artist*", "art director", "creative director", "visual", "graphic*", "3d", "modeler", "modeller",
                "landscape", "interior*", "urban*", "planner", "curator", "photograph*")),
    ("product", ("product manager", "product owner", "product lead", "head of product", "chief product", "cpo",
                 "vp product", "vp of product", "product director", "program manager", "producer", "game director")),
    ("marketing", ("marketing", "brand", "communication*", "pr", "public relations", "content", "seo",
                   "social media", "community", "growth", "copywriter", "editor*", "press", "cmo", "events")),
    ("sales", ("sales", "account executive", "account manager", "business development", "bdr", "sdr",
               "partnerships", "customer success", "client*", "revenue", "commercial", "bid manager", "bids")),
    ("operations", ("operations", "ops", "finance", "financial", "accountant", "accounting", "controller", "cfo",
                    "legal", "counsel", "attorney", "lawyer", "paralegal", "compliance", "office manager",
                    "administrat*", "assistant", "receptionist", "facilities", "procurement", "logistics",
                    "it support", "workplace", "secretary", "project manager", "project coordinator",
                    "accounts manager", "quality manager", "quality assurance manager", "site lead", "studio manager")),
]

_EXEC_SENIORITY = ("chief", "ceo", "cto", "cfo", "coo", "cmo", "cpo", "chro", "cio", "ciso", "vp", "vice president",
                   "svp", "evp", "head of", "founder", "co-founder", "cofounder", "president", "managing partner",
                   "founding partner", "senior partner", "equity partner", "name partner", "owner",
                   "managing director", "general manager", "chair", "chairman", "chairwoman", "board member")
_SENIOR_SENIORITY = ("senior", "sr", "lead", "principal", "staff", "manager", "director", "iii", "expert")
_JUNIOR_SENIORITY = ("junior", "jr", "intern", "internship", "graduate", "trainee", "apprentice", "entry level",
                     "entry-level", "assistant", "working student", "werkstudent")

_RX_CACHE: dict[str, re.Pattern] = {}


def _norm(text: str) -> str:
    text = ascii_fold(text).lower()
    text = re.sub(r"[^a-z0-9&+.\- ]", " ", text)
    return " ".join(text.split())


def _has(text: str, key: str) -> bool:
    rx = _RX_CACHE.get(key)
    if rx is None:
        if key.endswith("*"):
            rx = re.compile(r"(?<![a-z0-9])" + re.escape(key[:-1]))
        else:
            rx = re.compile(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])")
        _RX_CACHE[key] = rx
    return rx.search(text) is not None


def looks_like_title(text: Optional[str]) -> bool:
    """A short line that reads as a job title: 'Senior Architect', 'Head of Talent', 'CTO'."""
    if not text:
        return False
    text = " ".join(text.split()).strip(" -–—|:•·,")
    if not 2 <= len(text) <= 70 or "@" in text or text.count(",") > 2 or re.search(r"\d{3,}", text):
        return False
    words = re.findall(r"[a-z][a-z\-]*", ascii_fold(text).lower())
    if not words or len(words) > 9:
        return False
    return any(w in TITLE_WORDS for w in words)


def classify(title: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """(department, seniority) for a title. Department is 'other' when the title is unclear;
    seniority is None unless the title makes it clear."""
    if not title:
        return None, None
    t = _norm(title)
    department = next((dept for dept, keys in _DEPT_RULES if any(_has(t, k) for k in keys)), "other")
    if any(_has(t, k) for k in _EXEC_SENIORITY):
        seniority = "executive"
    elif any(_has(t, k) for k in _JUNIOR_SENIORITY):
        seniority = "junior"
    elif any(_has(t, k) for k in _SENIOR_SENIORITY):
        seniority = "senior"
    else:
        seniority = None
    # "Senior Director" etc. stay senior; "Associate Director" is senior, not junior.
    if seniority == "junior" and "director" in t:
        seniority = "senior"
    return department, seniority


# Credentials and degrees people append to titles ("Architect, NCARB, AIA"): dropped when cleaning.
_CREDENTIALS = {"ncarb", "aia", "faia", "leed", "ap", "bd+c", "phd", "ph.d", "ma", "march", "m.arch", "msc", "bsc", "ba",
                "bs", "ms", "mba", "mfa", "di", "dipl", "ing", "riba", "friba", "int", "frsa", "raar", "pe", "ra", "cpa",
                "shrm-cp", "shrm-scp", "phr", "sphr", "pmp", "cfa", "esq", "jd", "md", "rn", "assoc", "ceng", "fcca",
                "acca", "pgcert", "mnal", "mnla", "asla", "fasla", "rla", "cla", "mrtpi", "mrics"}


def _is_credential(segment: str) -> bool:
    words = re.findall(r"[a-z0-9+.\-]+", ascii_fold(segment).lower())
    return bool(words) and all(w.strip(".") in _CREDENTIALS for w in words)


def clean_title(text: Optional[str]) -> Optional[str]:
    """'Partner, Discipline Director - Architecture; Architect, NCARB, AIA New York'
    -> 'Partner, Discipline Director - Architecture, Architect'. Drops credentials and trailing city noise."""
    if not text:
        return None
    parts = [p.strip() for p in re.split(r"[;,]", " ".join(text.split())) if p.strip()]
    kept = []
    for p in parts:
        if _is_credential(p):
            continue
        # "AIA New York": credential followed by a place -> drop the whole piece
        first = re.findall(r"[a-z0-9+.\-]+", ascii_fold(p).lower())[:1]
        if first and first[0] in _CREDENTIALS and p.split()[0].isupper():
            continue
        # "Founding Partner Architect FAIA": strip trailing credential words
        words = p.split()
        while len(words) > 1 and words[-1].strip(".").lower() in _CREDENTIALS:
            words.pop()
        if words:
            kept.append(" ".join(words))
    return ", ".join(kept[:3]) or None
