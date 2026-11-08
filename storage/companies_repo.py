"""Company profiles: size (with source + date), Local, how we found them, and whether a job board feed exists."""
from __future__ import annotations

import re
from typing import Optional

from sqlmodel import Session, select

from finder.models import now_iso
from jobsearch.dedupe import norm_company
from jobsearch.discover import SMALL_MAX, DiscoveredCompany

from .db import engine
from .tables import CompanyProfileRow, ContactRow, FoundEmailRow, WatchCompanyRow

# A better source replaces a worse one; equal sources refresh the number.
SIZE_SOURCE_RANK = {"YC directory": 4, "Built In": 3, "company site": 2, "Finder (people found)": 1, "": 0}


class CompanyRepo:
    def get(self, name: str) -> Optional[CompanyProfileRow]:
        with Session(engine) as s:
            return s.get(CompanyProfileRow, norm_company(name))

    def all(self) -> list[CompanyProfileRow]:
        with Session(engine) as s:
            return list(s.exec(select(CompanyProfileRow)))

    def size_map(self) -> dict[str, dict]:
        """norm name -> {"employees", "local", "source"} for templates and the runner."""
        return {r.key: {"employees": r.employees, "local": r.local, "source": r.employees_source, "name": r.name}
                for r in self.all()}

    def upsert(self, c: DiscoveredCompany) -> tuple[CompanyProfileRow, bool]:
        key = norm_company(c.name)
        with Session(engine) as s:
            row = s.get(CompanyProfileRow, key)
            new = row is None
            if new:
                row = CompanyProfileRow(key=key, name=c.name, source=c.source, source_url=c.source_url, discovered_at=now_iso())
            row.website = row.website or c.website
            if c.website and not row.domain:
                from finder.resolve import registrable
                from urllib.parse import urlsplit
                host = urlsplit(c.website if "://" in c.website else "https://" + c.website).hostname or ""
                row.domain = registrable(host)
            row.location = row.location or c.location
            row.local = row.local or c.local
            row.industry = row.industry or c.industry
            row.one_liner = row.one_liner or c.one_liner
            row.about = row.about or c.about
            row.preferred = row.preferred or c.preferred
            row.hiring = c.hiring
            if c.employees and SIZE_SOURCE_RANK.get(c.employees_source, 0) >= SIZE_SOURCE_RANK.get(row.employees_source, 0):
                row.employees, row.employees_source = c.employees, c.employees_source
                row.employees_url, row.employees_at = c.source_url, now_iso()
            row.updated_at = now_iso()
            s.add(row)
            s.commit()
            s.refresh(row)
            return row, new

    def set_size(self, name: str, employees: int, source: str, url: str = "") -> None:
        key = norm_company(name)
        with Session(engine) as s:
            row = s.get(CompanyProfileRow, key) or CompanyProfileRow(key=key, name=name, source="jobs", discovered_at=now_iso())
            if SIZE_SOURCE_RANK.get(source, 0) >= SIZE_SOURCE_RANK.get(row.employees_source, 0):
                row.employees, row.employees_source, row.employees_url, row.employees_at = employees, source, url, now_iso()
            row.updated_at = now_iso()
            s.add(row)
            s.commit()

    def set_board(self, name: str, status: str, watch_id: Optional[int] = None, domain: str = "") -> None:
        with Session(engine) as s:
            row = s.get(CompanyProfileRow, norm_company(name))
            if row:
                row.board_status, row.watch_id = status, watch_id or row.watch_id
                row.domain = row.domain or domain
                s.add(row)
                s.commit()

    def needing_size(self, limit: int) -> list[CompanyProfileRow]:
        rows = [r for r in self.all() if r.employees is None or r.employees_source == "Finder (people found)"]
        return rows[:limit]

    def needing_board(self, limit: int) -> list[CompanyProfileRow]:
        rows = sorted([r for r in self.all() if r.board_status == "unchecked" and r.source in ("yc", "builtin", "hn")],
                      key=lambda r: (not r.local, not r.preferred, abs((r.employees or 999) - 50)))
        return rows[:limit]

    def finder_people(self, domain: str) -> int:
        with Session(engine) as s:
            return len({r.email for r in s.exec(select(FoundEmailRow).where(FoundEmailRow.domain == domain,
                                                                             FoundEmailRow.kind == "personal"))})

    def ensure_from_watchlist(self) -> int:
        """Every watchlist company gets a profile row (so sizes can be filled in)."""
        n = 0
        with Session(engine) as s:
            for w in s.exec(select(WatchCompanyRow)):
                key = norm_company(w.name)
                if not s.get(CompanyProfileRow, key):
                    s.add(CompanyProfileRow(key=key, name=w.name, domain=w.domain or "", website=w.careers_url or "",
                                            source="watchlist", watch_id=w.id, board_status="found" if w.ats else "none",
                                            discovered_at=now_iso(), updated_at=now_iso()))
                    n += 1
            s.commit()
        return n

    # -- cold pitches (Outreach > Find companies) ----------------------------------------------------------
    def cold_rows(self) -> list[dict]:
        """Every profile as a dict for outreach.cold, with whether you've already picked someone there."""
        with Session(engine) as s:
            contacted = {c.company_key for c in s.exec(select(ContactRow).where(ContactRow.mode == "cold"))}
            contacted |= {norm_company(c.company) for c in s.exec(select(ContactRow))}
            rows = list(s.exec(select(CompanyProfileRow)))
        return [{**r.model_dump(), "contacted": r.key in contacted} for r in rows]

    def set_cold_status(self, key: str, status: str) -> None:
        with Session(engine) as s:
            row = s.get(CompanyProfileRow, key)
            if row:
                row.cold_status, row.updated_at = status, now_iso()
                s.add(row)
                s.commit()

    def add_manual(self, name: str, website: str) -> CompanyProfileRow:
        """A company you (or a friend) found. Its size is checked on the next autopilot run."""
        name, website = name.strip(), website.strip()
        if not name:
            raise ValueError("add the company's name")
        if website and not re.match(r"^(?:https?://)?[\w-]+(?:\.[\w-]+)+(?:/.*)?$", website):
            raise ValueError("that website doesn't look like an address (example: acme.com)")
        row, _ = self.upsert(DiscoveredCompany(name=name, source="you", source_url="", website=website))
        if row.cold_status:
            self.set_cold_status(row.key, "")
        return self.get(name)

    @staticmethod
    def is_small(employees: Optional[int]) -> bool:
        return employees is not None and employees <= SMALL_MAX
