"""One job model for every source, plus the small records that travel with it."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

WORK_MODES = ["remote", "hybrid", "onsite"]
WORK_MODE_LABELS = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site"}
JOB_TYPES = ["internship", "part_time", "full_time", "contract"]
JOB_TYPE_LABELS = {"internship": "Internship", "part_time": "Part-time", "full_time": "Full-time", "contract": "Contract"}
SOURCE_LABELS = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "apify": "LinkedIn (Apify)",
                 "simplify": "Simplify list", "hn": "Hacker News"}
ATS_TYPES = ["greenhouse", "lever", "ashby"]


@dataclass
class Pay:
    min: Optional[float] = None
    max: Optional[float] = None
    currency: str = "USD"
    period: str = "year"  # year | month | hour
    text: str = ""  # as written, e.g. "$70K - $90K"

    @property
    def yearly_min(self) -> Optional[float]:
        return _yearly(self.min or self.max, self.period)

    @property
    def yearly_max(self) -> Optional[float]:
        return _yearly(self.max or self.min, self.period)


def _yearly(value: Optional[float], period: str) -> Optional[float]:
    if value is None:
        return None
    return value * {"hour": 2080, "month": 12, "week": 52}.get(period, 1)


@dataclass
class SourceRef:
    source: str  # greenhouse | lever | ashby | apify
    source_id: str
    url: str


@dataclass
class Flag:
    key: str  # enrolled | grad_year | age | degree | work_auth | citizenship | experience
    label: str  # short tag text, e.g. "Grad year 2027"
    snippet: str  # the sentence that triggered it


@dataclass
class Job:
    source: str
    source_id: str
    company: str
    title: str
    url: str  # the posting
    apply_url: str = ""
    location: str = ""
    work_mode: Optional[str] = None  # remote | hybrid | onsite | None (not stated)
    job_type: Optional[str] = None  # internship | part_time | full_time | contract | None
    pay: Optional[Pay] = None
    posted_at: Optional[str] = None  # ISO 8601
    description: str = ""  # plain text
    department: str = ""
    sources: list[SourceRef] = field(default_factory=list)  # every source this job was seen in, after dedupe
    flags: list[Flag] = field(default_factory=list)

    def __post_init__(self):
        if not self.sources:
            self.sources = [SourceRef(self.source, self.source_id, self.url)]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        d = dict(d)
        d["pay"] = Pay(**d["pay"]) if d.get("pay") else None
        d["sources"] = [SourceRef(**s) for s in d.get("sources", [])]
        d["flags"] = [Flag(**f) for f in d.get("flags", [])]
        return cls(**d)


@dataclass
class WatchCompany:
    name: str
    ats: Optional[str] = None  # greenhouse | lever | ashby | None (no public feed known)
    board: Optional[str] = None
    domain: Optional[str] = None
    careers_url: Optional[str] = None
    id: Optional[int] = None


@dataclass
class SourceStatus:
    source: str  # e.g. "greenhouse:riotgames" or "apify"
    label: str  # e.g. "Riot Games (Greenhouse)"
    status: str  # ok | error | skipped | not_configured | refused
    message: str = ""
    jobs: int = 0
