"""Persistence interface the engine uses. storage/repo.py implements it with SQLite;
MemoryStore is for tests and one-off CLI runs."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional, Protocol

from .models import FoundEmail, PatternResult, TemplateScore, now_iso


@dataclass
class SourceStatus:
    source: str
    status: str
    message: str = ""
    stats: dict = field(default_factory=dict)


@dataclass
class DomainReport:
    domain: str
    emails: list[FoundEmail]
    pattern: PatternResult
    sources: list[SourceStatus] = field(default_factory=list)
    searched_at: str = field(default_factory=now_iso)
    github_org: Optional[str] = None
    cached: bool = False
    # False when cancelled or a source errored: such a search must not make older sightings look stale
    complete: bool = True

    @property
    def personal(self) -> list[FoundEmail]:
        return [e for e in self.emails if e.kind == "personal"]

    @property
    def unique_addresses(self) -> int:
        return len({e.email for e in self.emails})

    def is_stale(self, email: str) -> bool:
        """True when no sighting of this address came from the most recent search."""
        seen = [e.last_seen for e in self.emails if e.email == email and e.last_seen]
        return bool(seen) and max(seen) < self.searched_at

    @property
    def robots_blocked(self) -> bool:
        return any(s.source == "website" and s.status == "blocked" for s in self.sources)

    def to_dict(self) -> dict:
        return asdict(self)


def pattern_from_dict(d: dict) -> PatternResult:
    d = dict(d)
    d["breakdown"] = [TemplateScore(**b) for b in d.get("breakdown", [])]
    return PatternResult(**d)


class Store(Protocol):
    def load_report(self, domain: str) -> Optional[DomainReport]: ...

    def save_report(self, report: DomainReport) -> None: ...

    # company name -> domain cache (finder.resolve)
    def load_company(self, query: str) -> Optional[dict]: ...

    def save_company(self, query: str, domain: Optional[str], confirmed: bool, result: dict, resolved_at: str) -> None: ...


class MemoryStore:
    def __init__(self):
        self.reports: dict[str, DomainReport] = {}
        self.companies: dict[str, dict] = {}

    def load_report(self, domain: str) -> Optional[DomainReport]:
        return self.reports.get(domain)

    def save_report(self, report: DomainReport) -> None:
        for e in report.emails:
            e.last_seen = report.searched_at if e.last_seen is None else e.last_seen
        self.reports[report.domain] = report

    def load_company(self, query: str) -> Optional[dict]:
        return self.companies.get(query)

    def save_company(self, query: str, domain: Optional[str], confirmed: bool, result: dict, resolved_at: str) -> None:
        self.companies[query] = dict(result, domain=domain, confirmed=confirmed, resolved_at=resolved_at)
