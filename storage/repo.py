"""SQLite implementation of finder.store.Store."""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Optional

from sqlalchemy import Engine
from sqlmodel import Session, delete, func, select

from finder.models import FoundEmail
from finder.patterns import detect_pattern
from finder.store import DomainReport, SourceStatus, pattern_from_dict

from .db import engine as default_engine
from .tables import CompanyDomainRow, DomainPatternRow, DomainRow, FoundEmailRow


class SqlStore:
    def __init__(self, engine: Optional[Engine] = None):
        self.engine = engine or default_engine

    def load_report(self, domain: str) -> Optional[DomainReport]:
        with Session(self.engine) as s:
            d = s.get(DomainRow, domain)
            if d is None:
                return None
            rows = s.exec(select(FoundEmailRow).where(FoundEmailRow.domain == domain)
                          .order_by(FoundEmailRow.kind.desc(), FoundEmailRow.email)).all()
            p = s.get(DomainPatternRow, domain)
        emails = [FoundEmail(email=r.email, domain=r.domain, kind=r.kind, source_type=r.source_type,
                             source_url=r.source_url, full_name=r.full_name, name_source=r.name_source,
                             context=r.context, found_at=r.first_seen, title=r.title, department=r.department,
                             seniority=r.seniority, last_seen=r.last_seen) for r in rows]
        pattern = pattern_from_dict(json.loads(p.result_json)) if p else pattern_from_dict({"domain": domain, "template": None})
        sources = [SourceStatus(**x) for x in json.loads(d.sources_json or "[]")]
        return DomainReport(domain=domain, emails=emails, pattern=pattern, sources=sources,
                            searched_at=d.last_searched_at, github_org=d.github_org)

    def save_report(self, report: DomainReport) -> None:
        with Session(self.engine) as s:
            existing = {(r.email, r.source_url): r for r in
                        s.exec(select(FoundEmailRow).where(FoundEmailRow.domain == report.domain)).all()}
            current = set()
            for e in report.emails:
                key = (e.email, e.source_url)
                current.add(key)
                row = existing.get(key)
                if row is None:
                    s.add(FoundEmailRow(domain=report.domain, email=e.email, kind=e.kind, full_name=e.full_name,
                                        name_source=e.name_source, source_type=e.source_type, source_url=e.source_url,
                                        context=e.context, first_seen=e.found_at, last_seen=report.searched_at,
                                        title=e.title, department=e.department, seniority=e.seniority))
                else:  # keep first_seen, refresh everything else
                    row.kind, row.full_name, row.name_source = e.kind, e.full_name or row.full_name, e.name_source or row.name_source
                    row.context, row.last_seen = e.context or row.context, report.searched_at
                    if e.title:
                        row.title, row.department, row.seniority = e.title, e.department, e.seniority
                    elif e.department and not row.department:
                        row.department = e.department
                    s.add(row)
            # Sightings from earlier searches that weren't seen again are kept (pages move,
            # commits age out), so evidence accumulates across refreshes.
            d = s.get(DomainRow, report.domain)
            if d is None:
                d = DomainRow(domain=report.domain, last_searched_at=report.searched_at)
            elif report.complete:  # a cancelled/partial search doesn't define what is current
                d.last_searched_at = report.searched_at
            d.github_org = report.github_org
            d.robots_blocked = report.robots_blocked
            d.sources_json = json.dumps([asdict(x) for x in report.sources])
            s.add(d)
            s.commit()

        # Pattern is recomputed over everything stored for the domain, old sightings included.
        full = self.load_report(report.domain)
        pattern = detect_pattern(report.domain, full.emails)
        report.pattern = pattern
        report.emails = full.emails
        with Session(self.engine) as s:
            row = s.get(DomainPatternRow, report.domain) or DomainPatternRow(domain=report.domain, result_json="{}",
                                                                              computed_at=pattern.computed_at)
            row.template, row.support, row.contradict = pattern.template, pattern.support, pattern.contradict
            row.total, row.confidence = pattern.total, pattern.confidence
            row.result_json, row.computed_at = json.dumps(asdict(pattern)), pattern.computed_at
            s.add(row)
            s.commit()

    def list_domains(self, limit: int = 30) -> list[tuple[DomainRow, int]]:
        """Recently searched domains with their distinct address count."""
        with Session(self.engine) as s:
            rows = list(s.exec(select(DomainRow).order_by(DomainRow.last_searched_at.desc()).limit(limit)))
            counts = dict(s.exec(select(FoundEmailRow.domain, func.count(func.distinct(FoundEmailRow.email)))
                                 .group_by(FoundEmailRow.domain)).all())
        return [(d, counts.get(d.domain, 0)) for d in rows]

    # --- company name -> domain cache (finder.resolve) ---------------------------------
    def load_company(self, query: str) -> Optional[dict]:
        with Session(self.engine) as s:
            row = s.get(CompanyDomainRow, query)
            if row is None:
                return None
            data = json.loads(row.result_json or "{}")
            data.update(domain=row.domain, confirmed=row.confirmed, resolved_at=row.resolved_at)
            return data

    def save_company(self, query: str, domain: Optional[str], confirmed: bool, result: dict, resolved_at: str) -> None:
        with Session(self.engine) as s:
            row = s.get(CompanyDomainRow, query) or CompanyDomainRow(query=query, resolved_at=resolved_at)
            row.domain, row.confirmed, row.resolved_at = domain, confirmed, resolved_at
            row.result_json = json.dumps(result)
            s.add(row)
            s.commit()

    def pattern_rows(self) -> dict[str, DomainPatternRow]:
        with Session(self.engine) as s:
            return {p.domain: p for p in s.exec(select(DomainPatternRow))}

    def forget_domain(self, domain: str) -> None:
        with Session(self.engine) as s:
            s.exec(delete(FoundEmailRow).where(FoundEmailRow.domain == domain))
            s.exec(delete(DomainPatternRow).where(DomainPatternRow.domain == domain))
            s.exec(delete(DomainRow).where(DomainRow.domain == domain))
            s.commit()
