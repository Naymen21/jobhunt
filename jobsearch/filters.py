"""Saved search criteria and how a job is checked against them.

Hard filters hide a job only when the job clearly fails (known value that doesn't match). Unknown values
(no pay listed, work mode not stated) pass, but count as half a point in the filter-fit part of the match score."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

from .flags import blocks_high_schooler
from .models import Job
from .normalize import age_days, norm_words

DEFAULT_EXCLUDE_EXPERIENCE = ["senior", "sr", "staff", "principal", "lead", "director", "head of", "vp", "5+ years"]

_PLACE_ALIASES = [
    {"usa", "united states", "us", "u s", "united states of america"},
    {"uk", "united kingdom", "england", "great britain"},
    {"uae", "united arab emirates"},
    {"sf", "san francisco", "sf bay area", "bay area"},
    {"nyc", "new york", "new york city"},
    {"la", "los angeles"},
]


@dataclass
class SearchSpec:
    include_keywords: list[str] = field(default_factory=list)  # title contains any of these
    require_keywords: list[str] = field(default_factory=list)  # ...and also any of these (e.g. intern, co-op)
    high_school_only: bool = False  # keep only postings that say high school students / incoming freshmen can apply
    no_degree_only: bool = False  # hide postings that require college enrollment, a degree program, or a grad year
    min_employees: Optional[int] = None  # company size range (people); companies of unknown size stay unless
    max_employees: Optional[int] = None  # ...hide_unknown_size is on
    hide_unknown_size: bool = False
    exclude_keywords: list[str] = field(default_factory=list)  # title contains none of these
    locations: list[str] = field(default_factory=list)  # "Los Angeles", "USA", "Remote"
    work_modes: list[str] = field(default_factory=list)  # remote | hybrid | onsite
    job_types: list[str] = field(default_factory=list)  # internship | part_time | full_time | contract
    min_pay: Optional[int] = None  # per year; hourly pay is counted as x2080
    posted_within_days: Optional[int] = None
    exclude_experience: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_EXPERIENCE))
    sources: list[str] = field(default_factory=lambda: ["watchlist"])  # watchlist | apify
    companies: list[int] = field(default_factory=list)  # watchlist ids; empty = every company with a feed
    apify_max_results: int = 25
    apify_runs_per_day: int = 1  # LinkedIn runs allowed per day for this search (0 = no limit)
    apify_query: str = ""  # LinkedIn search text; empty = built from the title keywords

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SearchSpec":
        known = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known)

    @property
    def max_years(self) -> Optional[int]:
        """'5+ years' in the experience excludes hides jobs asking for 5 or more years."""
        years = [int(m.group(1)) for e in self.exclude_experience if (m := re.match(r"\s*(\d+)\s*\+?\s*y", e))]
        return min(years) if years else None

    @property
    def experience_words(self) -> list[str]:
        return [e for e in self.exclude_experience if not re.match(r"\s*\d", e)]

    def apify_keywords(self) -> str:
        """LinkedIn search text: the first title keyword, plus the first "must also include" word (e.g. "software intern")."""
        if self.apify_query.strip():
            return self.apify_query.strip()
        from .student import current
        if self.high_school_only and current().high_school:  # "Software Engineering intern" only finds college internships
            return "high school internship"
        words = self.include_keywords[:1] + self.require_keywords[:1]
        return " ".join(words) or "software engineer"


@dataclass
class Evaluation:
    passes: bool
    hidden_because: list[str]
    fit: float  # 0..1 share of the search's criteria this job meets (unknown = half)
    fit_notes: list[str]


def _has_word(text: str, phrase: str) -> bool:
    p = norm_words(phrase)
    return bool(p) and re.search(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])", norm_words(text)) is not None


def _title_matches(title: str, keyword: str) -> bool:
    """Loose match for interest areas: "Software Engineering" matches "Software Engineer Intern", "Data Science" matches
    "Data Scientist", "AI / Machine Learning" matches either half. Every word of one alternative must appear, by its stem."""
    if _has_word(title, keyword):
        return True
    words = norm_words(title).split()
    for alt in keyword.split("/"):
        parts = [w for w in norm_words(alt).split() if w not in ("and", "of", "the")]
        if parts and all(any(t == p if len(p) < 5 else t.startswith(p[:max(4, len(p) - 4)]) for t in words) for p in parts):
            return True
    return False


US_STATES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california", "CO": "colorado",
    "CT": "connecticut", "DE": "delaware", "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho",
    "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky", "LA": "louisiana", "ME": "maine",
    "MD": "maryland", "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi", "MO": "missouri",
    "MT": "montana", "NE": "nebraska", "NV": "nevada", "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico",
    "NY": "new york", "NC": "north carolina", "ND": "north dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon",
    "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina", "SD": "south dakota", "TN": "tennessee",
    "TX": "texas", "UT": "utah", "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "west virginia",
    "WI": "wisconsin", "WY": "wyoming", "DC": "district of columbia",
}
_US_STATE_CODE = re.compile(r",\s*(" + "|".join(US_STATES) + r")\b")  # "Redmond, WA" (case-sensitive on purpose)


def _place_match(location: str, wanted: str) -> bool:
    loc = norm_words(location)
    w = norm_words(wanted)
    group = next((g for g in _PLACE_ALIASES if w in g), {w})
    if any(re.search(r"(?<![a-z0-9])" + re.escape(a) + r"(?![a-z0-9])", loc) for a in group):
        return True
    if "usa" in group:  # "United States" also matches "Redmond, WA" and "Austin, Texas"
        return bool(_US_STATE_CODE.search(location or "")) or any(
            re.search(r"(?<![a-z])" + s + r"(?![a-z])", loc) for s in US_STATES.values())
    code = next((c for c, name in US_STATES.items() if name == w), None)  # "California" matches "Irvine, CA"
    return bool(code and re.search(r",\s*" + code + r"\b", location or ""))


def evaluate(job: Job, spec: SearchSpec, years_required: Optional[int] = None,
             now: Optional[datetime] = None, employees: Optional[int] = None) -> Evaluation:
    hidden, notes, points = [], [], []

    if spec.include_keywords and not any(_title_matches(job.title, k) for k in spec.include_keywords):
        hidden.append("title doesn't contain any of the keywords")
    from .student import current
    if spec.high_school_only and current().high_school and not any(f.key in ("hs_ok", "pitch") for f in job.flags):  # pitch candidates stay (small companies)
        hidden.append("doesn't say high school students can apply")
    keys = {f.key for f in job.flags}
    if spec.no_degree_only and not keys & {"hs_ok", "pitch"} and any(blocks_high_schooler(f) for f in job.flags):
        hidden.append("requires college enrollment or a degree")
    if spec.min_employees or spec.max_employees:
        if employees is None:
            if spec.hide_unknown_size:
                hidden.append("company size unknown")
        elif spec.min_employees and employees < spec.min_employees:
            hidden.append(f"company has about {employees} people (under {spec.min_employees})")
        elif spec.max_employees and employees > spec.max_employees:
            hidden.append(f"company has about {employees} people (over {spec.max_employees})")
    if spec.require_keywords and not any(_has_word(job.title, k) for k in spec.require_keywords):
        hidden.append("title doesn't contain any of: " + ", ".join(spec.require_keywords))
    bad = next((k for k in spec.exclude_keywords if _has_word(job.title, k)), None)
    if bad:
        hidden.append(f"title contains '{bad}'")
    level = next((k for k in spec.experience_words if _has_word(job.title, k)), None)
    if level:
        hidden.append(f"title says '{level}'")
    if spec.max_years and years_required and years_required >= spec.max_years:
        hidden.append(f"asks for {years_required}+ years")

    if spec.posted_within_days:
        age = age_days(job.posted_at, now)
        if age is not None and job.posted_at.endswith("T00:00:00+00:00"):
            age = max(0.0, age - 1)  # a date with no time (LinkedIn) could be any time that day
        if age is None:
            points.append(0.5); notes.append("posted date unknown")
        elif age > spec.posted_within_days:
            hidden.append(f"posted {age:.0f} days ago"); points.append(0)
        else:
            points.append(1)

    if spec.work_modes:
        if job.work_mode is None:
            points.append(0.5); notes.append("work mode not stated")
        elif job.work_mode in spec.work_modes:
            points.append(1)
        else:
            hidden.append(f"work mode is {job.work_mode}"); points.append(0)

    if spec.job_types:
        if job.job_type is None:
            points.append(0.5); notes.append("job type not stated")
        elif job.job_type in spec.job_types:
            points.append(1)
        else:
            hidden.append(f"job type is {job.job_type.replace('_', '-')}"); points.append(0)

    if spec.locations:
        wants_remote = any(norm_words(l) == "remote" for l in spec.locations)
        places = [l for l in spec.locations if norm_words(l) != "remote"]
        if (wants_remote and job.work_mode == "remote") or any(_place_match(job.location, p) for p in places):
            points.append(1)
        elif not job.location:
            points.append(0.5); notes.append("location not stated")
        else:
            hidden.append(f"location is {job.location}"); points.append(0)

    if spec.min_pay:
        top = job.pay.yearly_max if job.pay else None
        if top is None:
            points.append(0.5); notes.append("pay not listed")
        elif top >= spec.min_pay:
            points.append(1)
        else:
            hidden.append(f"pay tops out below {spec.min_pay:,}"); points.append(0)

    fit = sum(points) / len(points) if points else 1.0
    return Evaluation(not hidden, hidden, fit, notes)
