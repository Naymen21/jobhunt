"""SQLite persistence for Settings and Job Search: profile, resumes, watchlist, saved searches, jobs, usage."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, col, select

from finder.models import now_iso
from jobsearch.config import RESUME_DIR
from jobsearch.dedupe import match_key
from jobsearch.filters import SearchSpec, evaluate
from jobsearch.dedupe import norm_company
from jobsearch.flags import add_pitch, find_flags
from jobsearch.match import match, skills_in
from jobsearch.models import Job, WatchCompany
from jobsearch.runner import RunReport, years_required
from jobsearch.watchlist import STARTER

from .db import engine
from .tables import (JobRow, JobSourceRow, MetaRow, ProfileRow, ResumeRow, SavedSearchRow, SearchJobRow, UsageEventRow,
                     WatchCompanyRow)

URL_FIELDS = {"linkedin_url", "github_url", "portfolio_url", "resume_link"}


def normalize_url(value: str) -> str:
    """'www.linkedin.com/in/x' -> 'https://www.linkedin.com/in/x'. Empty stays empty."""
    value = (value or "").strip()
    if value and not value.lower().startswith(("http://", "https://")):
        value = "https://" + value.lstrip("/")
    return value


PROFILE_FIELDS = ["name", "email", "phone", "location", "school", "grad_year", "linkedin_url", "github_url",
                  "portfolio_url", "work_authorization", "about", "resume_link", "student_level", "degree_program",
                  "home_state", "home_cities"]


def month_start(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


class JobRepo:
    # -- meta / seeding ---------------------------------------------------------------------
    def seed_watchlist_once(self) -> bool:
        with Session(engine) as s:
            if s.get(MetaRow, "watchlist_seeded"):
                return False
            for c in STARTER:
                s.add(WatchCompanyRow(name=c.name, ats=c.ats, board=c.board, domain=c.domain, careers_url=c.careers_url,
                                      status="unchecked" if c.ats else "no_feed",
                                      detected_how="starter list (confirmed 2026-11-05)" if c.ats else "",
                                      message="" if c.ats else "No public Greenhouse, Lever or Ashby feed found: check manually",
                                      created_at=now_iso()))
            s.add(MetaRow(key="watchlist_seeded", value=now_iso()))
            s.commit()
            return True

    # -- profile ------------------------------------------------------------------------------
    def profile(self) -> ProfileRow:
        with Session(engine) as s:
            return s.get(ProfileRow, 1) or ProfileRow()

    def save_profile(self, values: dict) -> ProfileRow:
        with Session(engine) as s:
            row = s.get(ProfileRow, 1) or ProfileRow(id=1)
            for f in PROFILE_FIELDS:
                if f in values:
                    value = (values[f] or "").strip()
                    setattr(row, f, normalize_url(value) if f in URL_FIELDS else value)
            row.updated_at = now_iso()
            s.add(row)
            s.commit()
            s.refresh(row)
        self.apply_student(row)
        return row

    def student_summary(self) -> dict:
        """For the Claude Code commands: who you are, so prompts never assume one person."""
        from jobsearch.student import class_year
        who = self.apply_student()
        return {"level": who.level, "label": who.label, "class_year": class_year(who), "grad_year": who.grad_year,
                "home_area": who.area_label, "high_school": who.high_school}

    def apply_student(self, row: Optional[ProfileRow] = None):
        from jobsearch.student import set_student
        p = row or self.profile()
        return set_student(p.student_level or "high_school", p.grad_year, p.home_state, p.home_cities)

    # -- resumes ------------------------------------------------------------------------------
    def resumes(self) -> list[ResumeRow]:
        with Session(engine) as s:
            return list(s.exec(select(ResumeRow).order_by(col(ResumeRow.is_default).desc(), col(ResumeRow.id).desc())))

    def resume(self, resume_id: int) -> Optional[ResumeRow]:
        with Session(engine) as s:
            return s.get(ResumeRow, resume_id)

    def default_resume(self) -> Optional[ResumeRow]:
        with Session(engine) as s:
            return s.exec(select(ResumeRow).where(ResumeRow.is_default == True)).first()  # noqa: E712

    def add_resume(self, label: str, filename: str, kind: str, data: bytes, text: str) -> ResumeRow:
        RESUME_DIR.mkdir(parents=True, exist_ok=True)
        stored = f"{uuid.uuid4().hex}.{kind}"
        (RESUME_DIR / stored).write_bytes(data)
        with Session(engine) as s:
            first = s.exec(select(ResumeRow)).first() is None
            row = ResumeRow(label=label or filename, filename=filename, stored_name=stored, kind=kind, size=len(data),
                            text=text, is_default=first, uploaded_at=now_iso())
            s.add(row)
            s.commit()
            s.refresh(row)
            return row

    def set_default_resume(self, resume_id: int) -> None:
        with Session(engine) as s:
            for row in s.exec(select(ResumeRow)):
                row.is_default = row.id == resume_id
                s.add(row)
            s.commit()

    def delete_resume(self, resume_id: int) -> None:
        with Session(engine) as s:
            row = s.get(ResumeRow, resume_id)
            if not row:
                return
            was_default = row.is_default
            (RESUME_DIR / row.stored_name).unlink(missing_ok=True)
            s.delete(row)
            s.commit()
            if was_default:
                newest = s.exec(select(ResumeRow).order_by(col(ResumeRow.id).desc())).first()
                if newest:
                    newest.is_default = True
                    s.add(newest)
                    s.commit()

    # -- watchlist ----------------------------------------------------------------------------
    def watchlist(self) -> list[WatchCompanyRow]:
        with Session(engine) as s:
            rows = list(s.exec(select(WatchCompanyRow)))
        return sorted(rows, key=lambda r: (r.ats is None, r.name.lower()))

    def watch_companies(self) -> list[WatchCompany]:
        return [WatchCompany(r.name, r.ats, r.board, r.domain, r.careers_url, r.id) for r in self.watchlist()]

    def company(self, company_id: int) -> Optional[WatchCompanyRow]:
        with Session(engine) as s:
            return s.get(WatchCompanyRow, company_id)

    def add_company(self, **fields) -> WatchCompanyRow:
        with Session(engine) as s:
            row = WatchCompanyRow(created_at=now_iso(), **fields)
            s.add(row)
            s.commit()
            s.refresh(row)
            return row

    def update_company(self, company_id: int, **fields) -> Optional[WatchCompanyRow]:
        with Session(engine) as s:
            row = s.get(WatchCompanyRow, company_id)
            if not row:
                return None
            for k, v in fields.items():
                setattr(row, k, v)
            s.add(row)
            s.commit()
            s.refresh(row)
            return row

    def delete_company(self, company_id: int) -> None:
        with Session(engine) as s:
            row = s.get(WatchCompanyRow, company_id)
            if row:
                s.delete(row)
                s.commit()

    # -- saved searches -----------------------------------------------------------------------
    def searches(self) -> list[SavedSearchRow]:
        with Session(engine) as s:
            return list(s.exec(select(SavedSearchRow).order_by(SavedSearchRow.id)))

    def search(self, search_id: int) -> Optional[SavedSearchRow]:
        with Session(engine) as s:
            return s.get(SavedSearchRow, search_id)

    def save_search(self, search_id: Optional[int], name: str, spec: SearchSpec, auto_run: bool) -> SavedSearchRow:
        with Session(engine) as s:
            row = s.get(SavedSearchRow, search_id) if search_id else None
            row = row or SavedSearchRow(name=name, created_at=now_iso())
            row.name, row.spec_json, row.auto_run = name, json.dumps(spec.to_dict()), auto_run
            s.add(row)
            s.commit()
            s.refresh(row)
            return row

    def delete_search(self, search_id: int) -> None:
        with Session(engine) as s:
            for link in s.exec(select(SearchJobRow).where(SearchJobRow.search_id == search_id)):
                s.delete(link)
            row = s.get(SavedSearchRow, search_id)
            if row:
                s.delete(row)
            s.commit()

    @staticmethod
    def spec_of(row: SavedSearchRow) -> SearchSpec:
        return SearchSpec.from_dict(json.loads(row.spec_json or "{}"))

    # -- jobs -----------------------------------------------------------------------------------
    def save_results(self, search_id: int, run_id: int, report: RunReport, complete: bool = True) -> tuple[int, int]:
        """Store every kept job, link it to the search, and return (new for this search, total kept)."""
        now = now_iso()
        new = 0
        with Session(engine) as s:
            for scored in report.kept:
                job = scored.job
                job_row = self._find_job(s, job)
                if job_row is None:
                    job_row = JobRow(match_key=match_key(job), company=job.company, title=job.title, first_seen=now)
                self._fill(job_row, job, now)
                s.add(job_row)
                s.flush()
                for ref in job.sources:
                    exists = s.exec(select(JobSourceRow).where(JobSourceRow.source == ref.source,
                                                               JobSourceRow.source_id == ref.source_id)).first()
                    if not exists:
                        s.add(JobSourceRow(job_id=job_row.id, source=ref.source, source_id=ref.source_id, url=ref.url))
                link = s.exec(select(SearchJobRow).where(SearchJobRow.search_id == search_id,
                                                         SearchJobRow.job_id == job_row.id)).first()
                if link is None:
                    link = SearchJobRow(search_id=search_id, job_id=job_row.id, first_run_id=run_id, last_run_id=run_id)
                    new += 1
                link.last_run_id = run_id
                link.match_score = scored.match.score
                link.match_json = json.dumps(scored.match.to_dict())
                s.add(link)
            search = s.get(SavedSearchRow, search_id)
            if search and complete:
                search.last_run_id, search.last_run_at = run_id, now
                search.last_new, search.last_total = new, len(report.kept)
                s.add(search)
            s.commit()
        return new, len(report.kept)

    def _find_job(self, s: Session, job: Job) -> Optional[JobRow]:
        for ref in job.sources:
            src = s.exec(select(JobSourceRow).where(JobSourceRow.source == ref.source,
                                                    JobSourceRow.source_id == ref.source_id)).first()
            if src:
                return s.get(JobRow, src.job_id)
        # same company + title + city seen from a *different* source: the same job
        mine = {r.source for r in job.sources}
        for row in s.exec(select(JobRow).where(JobRow.match_key == match_key(job))):
            theirs = {x["source"] for x in json.loads(row.sources_json or "[]")}
            if not theirs & mine:
                return row
        return None

    @staticmethod
    def _fill(row: JobRow, job: Job, now: str) -> None:
        known = {(x["source"], x["source_id"]): x for x in json.loads(row.sources_json or "[]")}
        for ref in job.sources:
            known[(ref.source, ref.source_id)] = {"source": ref.source, "source_id": ref.source_id, "url": ref.url}
        # keep the richer copy's text when a LinkedIn listing joins a company-feed job
        richer = len(job.description) >= len(row.description or "") or job.source != "apify"
        if richer:
            row.company, row.title, row.description = job.company, job.title, job.description
            row.url, row.apply_url = job.url, job.apply_url or job.url
            row.location, row.department = job.location or row.location, job.department or row.department
        row.work_mode = job.work_mode or row.work_mode
        row.job_type = job.job_type or row.job_type
        if job.pay:
            row.pay_json = json.dumps(job.to_dict()["pay"])
            row.pay_year_max = job.pay.yearly_max
        row.posted_at = job.posted_at or row.posted_at
        row.sources_json = json.dumps(list(known.values()))
        row.flags_json = json.dumps([f.__dict__ for f in job.flags])
        row.match_key = match_key(job)
        row.last_seen = now

    def results(self, search_id: int) -> list[tuple[JobRow, SearchJobRow]]:
        """Jobs from the search's latest run, plus any saved job it ever found."""
        with Session(engine) as s:
            search = s.get(SavedSearchRow, search_id)
            if not search or not search.last_run_id:
                return []
            stmt = select(JobRow, SearchJobRow).join(SearchJobRow, SearchJobRow.job_id == JobRow.id).where(
                SearchJobRow.search_id == search_id)
            out = []
            for job, link in s.exec(stmt):
                if link.last_run_id == search.last_run_id or job.state == "saved":
                    out.append((job, link))
            return out

    def job(self, job_id: int) -> Optional[JobRow]:
        with Session(engine) as s:
            return s.get(JobRow, job_id)

    def link(self, search_id: int, job_id: int) -> Optional[SearchJobRow]:
        with Session(engine) as s:
            return s.exec(select(SearchJobRow).where(SearchJobRow.search_id == search_id,
                                                     SearchJobRow.job_id == job_id)).first()

    def set_state(self, job_id: int, state: str) -> Optional[JobRow]:
        assert state in ("", "saved", "dismissed")
        with Session(engine) as s:
            row = s.get(JobRow, job_id)
            if row:
                row.state = state
                s.add(row)
                s.commit()
                s.refresh(row)
            return row

    def rescore(self, resume_text: Optional[str]) -> int:
        """Recompute every stored flag and match score, e.g. after the default resume or the flag rules change."""
        skills = skills_in(resume_text) if resume_text else None
        n = 0
        with Session(engine) as s:
            specs = {r.id: self.spec_of(r) for r in s.exec(select(SavedSearchRow))}
            flags_cache: dict = {}
            from .companies_repo import CompanyRepo
            sizes = CompanyRepo().size_map()
            for link in s.exec(select(SearchJobRow)):
                row = s.get(JobRow, link.job_id)
                spec = specs.get(link.search_id)
                if not row or not spec:
                    continue
                job = row_to_job(row)
                if row.id not in flags_cache:  # rules may have changed
                    info = sizes.get(norm_company(job.company))
                    flags_cache[row.id] = add_pitch(find_flags(job.description, title=job.title), info["employees"] if info else None)
                job.flags = flags_cache[row.id]
                row.flags_json = json.dumps([f.__dict__ for f in job.flags])
                s.add(row)
                info = sizes.get(norm_company(job.company))
                result = match(job.title, job.description, evaluate(job, spec, years_required(job),
                               employees=info["employees"] if info else None), skills, job.flags)
                link.match_score, link.match_json = result.score, json.dumps(result.to_dict())
                s.add(link)
                n += 1
            s.commit()
        return n

    # -- usage ----------------------------------------------------------------------------------
    def record_usage(self, kind: str, amount: float = 1, note: str = "") -> None:
        with Session(engine) as s:
            s.add(UsageEventRow(at=now_iso(), kind=kind, amount=amount, note=note[:300]))
            s.commit()

    def apify_runs_today(self, search_id: int) -> int:
        """LinkedIn runs this saved search made since local midnight."""
        midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        start = midnight.astimezone(timezone.utc).isoformat(timespec="seconds")
        with Session(engine) as s:
            rows = s.exec(select(UsageEventRow).where(UsageEventRow.kind == "apify_run", UsageEventRow.at >= start,
                                                      UsageEventRow.note == f"search:{search_id}"))
            return sum(1 for _ in rows)

    def linkedin_jobs_today(self, search_id: int) -> list[Job]:
        """LinkedIn jobs this search stored since local midnight (reused when the daily LinkedIn limit is reached)."""
        midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        start = midnight.astimezone(timezone.utc).isoformat(timespec="seconds")
        with Session(engine) as s:
            rows = s.exec(select(JobRow).join(SearchJobRow, SearchJobRow.job_id == JobRow.id)
                          .where(SearchJobRow.search_id == search_id, JobRow.last_seen >= start))
            return [row_to_job(r) for r in rows if any(x.get("source") == "apify" for x in json.loads(r.sources_json or "[]"))]

    def apify_spent_today(self) -> float:
        """Apify cost estimates recorded since local midnight (all searches)."""
        midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        start = midnight.astimezone(timezone.utc).isoformat(timespec="seconds")
        with Session(engine) as s:
            return sum(e.amount for e in s.exec(select(UsageEventRow).where(UsageEventRow.kind == "apify_cost_usd",
                                                                            UsageEventRow.at >= start)))

    def usage_this_month(self) -> dict[str, float]:
        start = month_start()
        totals: dict[str, float] = {}
        with Session(engine) as s:
            for e in s.exec(select(UsageEventRow).where(UsageEventRow.at >= start)):
                totals[e.kind] = totals.get(e.kind, 0) + e.amount
        return totals


def row_to_job(row: JobRow) -> Job:
    from jobsearch.models import Flag, Pay, SourceRef
    sources = [SourceRef(**x) for x in json.loads(row.sources_json or "[]")]
    first = sources[0] if sources else SourceRef("", "", row.url)
    return Job(source=first.source, source_id=first.source_id, company=row.company, title=row.title, url=row.url,
               apply_url=row.apply_url, location=row.location, work_mode=row.work_mode, job_type=row.job_type,
               pay=Pay(**json.loads(row.pay_json)) if row.pay_json else None, posted_at=row.posted_at,
               description=row.description, department=row.department, sources=sources,
               flags=[Flag(**f) for f in json.loads(row.flags_json or "[]")])
