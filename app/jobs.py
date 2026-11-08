"""Background work: domain searches, bulk finds and job searches. Runs the engines in worker threads and
records progress in SQLite, which the pages poll. No search logic lives here."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from finder.bulk import BulkResult, find_one
from finder.config import settings as finder_settings
from finder.discovery import normalize_domain, search_domain
from finder.models import now_iso
from finder.verify import default_verifier
from jobsearch.config import settings as job_settings
from jobsearch.runner import run_search
from jobsearch.sources import ApifySource, FeedClient
from storage.db import engine
from storage.companies_repo import CompanyRepo
from storage.jobs_repo import JobRepo
from storage.repo import SqlStore
from storage.tables import BulkFindRow, BulkFindRun, JobRunRow, SearchRun

PROGRESS_WRITE_INTERVAL = 0.4  # seconds between progress writes to SQLite


class SearchJobs:
    def __init__(self, workers: int = 2):
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="search")
        self.cancels: dict[int, threading.Event] = {}
        self.store = SqlStore()

    def recover(self) -> None:
        """Runs left 'running' by a previous server process can never finish."""
        with Session(engine) as s:
            for run in s.exec(select(SearchRun).where(SearchRun.status.in_(["queued", "running"]))):
                run.status, run.error = "failed", "server restarted while this search was running"
                run.finished_at = datetime.now(timezone.utc)
                s.add(run)
            s.commit()

    def active_for(self, domain: str) -> Optional[int]:
        with Session(engine) as s:
            run = s.exec(select(SearchRun).where(SearchRun.domain == domain,
                                                 SearchRun.status.in_(["queued", "running"]))).first()
            return run.id if run else None

    def start(self, domain: str, refresh: bool, github_org: Optional[str], max_pages: Optional[int] = None) -> int:
        domain = normalize_domain(domain)  # raises BadDomain
        existing = self.active_for(domain)
        if existing:
            return existing
        with Session(engine) as s:
            run = SearchRun(domain=domain, refresh=refresh, github_org=github_org or None, message="Queued")
            s.add(run)
            s.commit()
            s.refresh(run)
            run_id = run.id
        self.cancels[run_id] = threading.Event()
        self.pool.submit(self._run, run_id, domain, github_org, max_pages)
        return run_id

    def cancel(self, run_id: int) -> None:
        ev = self.cancels.get(run_id)
        if ev:
            ev.set()

    def get(self, run_id: int) -> Optional[SearchRun]:
        with Session(engine) as s:
            return s.get(SearchRun, run_id)

    # -- worker --------------------------------------------------------------
    def _update(self, run_id: int, **fields) -> None:
        with Session(engine) as s:
            run = s.get(SearchRun, run_id)
            for k, v in fields.items():
                setattr(run, k, v)
            s.add(run)
            s.commit()

    def _run(self, run_id: int, domain: str, github_org: Optional[str], max_pages: Optional[int]) -> None:
        cancel = self.cancels[run_id]
        state = {"pages_crawled": 0, "pages_queued": 0, "emails_found": 0, "message": "Starting",
                 "current_source": None, "sources": {}}
        last_write = [0.0]

        def flush(force: bool = False) -> None:
            now = time.monotonic()
            if force or now - last_write[0] >= PROGRESS_WRITE_INTERVAL:
                last_write[0] = now
                self._update(run_id, pages_crawled=state["pages_crawled"], pages_queued=state["pages_queued"],
                             emails_found=state["emails_found"], message=state["message"][:300],
                             current_source=state["current_source"], sources_json=json.dumps(state["sources"]))

        def progress(e: dict) -> None:
            source = e.get("source")
            if source:
                state["current_source"] = source
                state["sources"][source] = {"status": e.get("source_status", "running"), "message": e.get("message", "")}
            for key in ("pages_crawled", "pages_queued"):
                if key in e:
                    state[key] = e[key]
            if "emails_found" in e:
                state["emails_found"] = max(state["emails_found"], e["emails_found"])
            if "emails_found_total" in e:
                state["emails_found"] = max(state["emails_found"], e["emails_found_total"])
            if e.get("message"):
                state["message"] = e["message"]
            flush(force=bool(e.get("source_status")))

        self._update(run_id, status="running", message="Starting")
        try:
            report = search_domain(domain, self.store, refresh=True, progress=progress, cancel=cancel,
                                   options={"github_org": github_org, "max_pages": max_pages})
            flush(force=True)
            self._update(run_id, status="cancelled" if cancel.is_set() else "done",
                         emails_found=report.unique_addresses, finished_at=datetime.now(timezone.utc),
                         message="Cancelled; partial results saved" if cancel.is_set() else "Finished")
        except Exception as e:  # report the failure on the page instead of leaving it spinning
            self._update(run_id, status="failed", error=f"{type(e).__name__}: {e}", finished_at=datetime.now(timezone.utc))
        finally:
            self.cancels.pop(run_id, None)


jobs = SearchJobs()


class BulkFindJobs:
    """Runs Find Email over a list, one row at a time, recording each result as it finishes."""

    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bulkfind")  # one list at a time
        self.cancels: dict[int, threading.Event] = {}
        self.store = SqlStore()

    def recover(self) -> None:
        with Session(engine) as s:
            for run in s.exec(select(BulkFindRun).where(BulkFindRun.status.in_(["queued", "running"]))):
                run.status, run.error = "failed", "server restarted while this list was running"
                run.finished_at = datetime.now(timezone.utc)
                s.add(run)
            s.commit()

    def start(self, items: list, search_unsearched: bool) -> int:
        with Session(engine) as s:
            run = BulkFindRun(total=len(items), search_unsearched=search_unsearched, message="Queued")
            s.add(run)
            s.commit()
            s.refresh(run)
            for i, item in enumerate(items):
                s.add(BulkFindRow(run_id=run.id, position=i, input_name=item.name, input_company=item.company))
            s.commit()
            run_id = run.id
        self.cancels[run_id] = threading.Event()
        self.pool.submit(self._run, run_id, items, search_unsearched)
        return run_id

    def cancel(self, run_id: int) -> None:
        ev = self.cancels.get(run_id)
        if ev:
            ev.set()

    def get(self, run_id: int) -> tuple[Optional[BulkFindRun], list[BulkFindRow]]:
        with Session(engine) as s:
            run = s.get(BulkFindRun, run_id)
            rows = list(s.exec(select(BulkFindRow).where(BulkFindRow.run_id == run_id).order_by(BulkFindRow.position)))
            return run, rows

    def _run(self, run_id: int, items: list, search_unsearched: bool) -> None:
        cancel = self.cancels[run_id]
        verifier = default_verifier()
        with Session(engine) as s:
            run = s.get(BulkFindRun, run_id)
            run.status, run.message = "running", "Starting"
            s.add(run)
            s.commit()
        try:
            for i, item in enumerate(items):
                if cancel.is_set():
                    break
                self._set_run(run_id, message=f"Finding {item.name} at {item.company}")
                try:
                    res = find_one(item, self.store, verifier, finder_settings,
                                   search_unsearched=search_unsearched, cancel=cancel)
                except Exception as e:  # one bad row must not stop the list
                    res = BulkResult("error", why=f"{type(e).__name__}: {e}")
                with Session(engine) as s:
                    row = s.exec(select(BulkFindRow).where(BulkFindRow.run_id == run_id, BulkFindRow.position == i)).one()
                    row.status, row.domain, row.email, row.score = res.status, res.domain, res.email, res.score
                    row.evidence, row.why = res.evidence, res.why
                    s.add(row)
                    run = s.get(BulkFindRun, run_id)
                    run.done = i + 1
                    s.add(run)
                    s.commit()
            self._set_run(run_id, status="cancelled" if cancel.is_set() else "done",
                          message="Cancelled" if cancel.is_set() else "Finished",
                          finished_at=datetime.now(timezone.utc))
        except Exception as e:
            self._set_run(run_id, status="failed", error=f"{type(e).__name__}: {e}", finished_at=datetime.now(timezone.utc))
        finally:
            self.cancels.pop(run_id, None)

    def _set_run(self, run_id: int, **fields) -> None:
        with Session(engine) as s:
            run = s.get(BulkFindRun, run_id)
            for k, v in fields.items():
                setattr(run, k, v)
            s.add(run)
            s.commit()


bulk_jobs = BulkFindJobs()


class JobSearchRuns:
    """Runs saved job searches in the background (Run now) or in the foreground (daily script).
    Progress goes to the job_run table, which the Jobs page polls."""

    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jobsearch")  # one search at a time
        self.cancels: dict[int, threading.Event] = {}
        self.repo = JobRepo()

    def recover(self) -> None:
        with Session(engine) as s:
            for run in s.exec(select(JobRunRow).where(JobRunRow.status.in_(["queued", "running"]))):
                run.status, run.error = "failed", "server restarted while this search was running"
                run.finished_at = datetime.now(timezone.utc)
                s.add(run)
            s.commit()

    def active_for(self, search_id: int) -> Optional[int]:
        with Session(engine) as s:
            run = s.exec(select(JobRunRow).where(JobRunRow.search_id == search_id,
                                                 JobRunRow.status.in_(["queued", "running"]))).first()
            return run.id if run else None

    def create(self, search_id: int, trigger: str = "manual") -> int:
        with Session(engine) as s:
            run = JobRunRow(search_id=search_id, trigger=trigger, message="Queued")
            s.add(run)
            s.commit()
            s.refresh(run)
            run_id = run.id
        self.cancels[run_id] = threading.Event()
        return run_id

    def start(self, search_id: int) -> int:
        existing = self.active_for(search_id)
        if existing:
            return existing
        run_id = self.create(search_id)
        self.pool.submit(self.execute, run_id, search_id)
        return run_id

    def cancel(self, run_id: int) -> None:
        ev = self.cancels.get(run_id)
        if ev:
            ev.set()

    def get(self, run_id: int) -> Optional[JobRunRow]:
        with Session(engine) as s:
            return s.get(JobRunRow, run_id)

    def latest(self, search_id: int) -> Optional[JobRunRow]:
        with Session(engine) as s:
            return s.exec(select(JobRunRow).where(JobRunRow.search_id == search_id)
                          .order_by(JobRunRow.id.desc())).first()

    def _update(self, run_id: int, **fields) -> None:
        with Session(engine) as s:
            run = s.get(JobRunRow, run_id)
            for k, v in fields.items():
                setattr(run, k, v)
            s.add(run)
            s.commit()

    def execute(self, run_id: int, search_id: int, transport=None) -> Optional[JobRunRow]:
        """Run one search to completion in this thread. `transport` lets tests use saved responses."""
        cancel = self.cancels.setdefault(run_id, threading.Event())
        state = {"sources": {}, "total": 0, "done": 0, "message": "Starting"}
        last_write = [0.0]

        def flush(force: bool = False) -> None:
            now = time.monotonic()
            if force or now - last_write[0] >= PROGRESS_WRITE_INTERVAL:
                last_write[0] = now
                self._update(run_id, total_sources=state["total"], done_sources=state["done"],
                             message=state["message"][:300], sources_json=json.dumps(state["sources"]))

        def progress(e: dict) -> None:
            if "total_sources" in e:
                state["total"] = e["total_sources"]
            if e.get("source"):
                prev = state["sources"].get(e["source"], {}).get("status")
                state["sources"][e["source"]] = {k: e.get(k) for k in ("label", "status", "message", "jobs")}
                if e.get("status") != "running" and prev in (None, "running"):
                    state["done"] += 1
                state["message"] = f"{e.get('label')}: {e.get('message')}"
            elif e.get("message"):
                state["message"] = e["message"]
            flush(force=e.get("status") not in (None, "running"))

        search = self.repo.search(search_id)
        if not search:
            self._update(run_id, status="failed", error="saved search not found", finished_at=datetime.now(timezone.utc))
            return self.get(run_id)
        self._update(run_id, status="running", message="Starting")
        client = FeedClient(job_settings, transport=transport)
        try:
            resume = self.repo.default_resume()
            spec = self.repo.spec_of(search)
            skip = None  # LinkedIn is limited by the daily budget instead (checked inside the run)
            report = run_search(spec, self.repo.watch_companies(), client=client,
                                apify=ApifySource(job_settings), resume_text=resume.text if resume else None,
                                progress=progress, cancel=cancel, apify_skip=skip,
                                reused_jobs=None, apify_spent_today=self.repo.apify_spent_today(),
                                discover_jobs=_discover_jobs() if "discover" in spec.sources else None,
                                describe_fn=_describe_jobs,
                                company_info=CompanyRepo().size_map())
            flush(force=True)
            self._record_usage(report, client, search_id)
            self._refresh_watchlist(report)
            new, kept = self.repo.save_results(search_id, run_id, report)
            self._update(run_id, status="cancelled" if report.cancelled else "done", fetched=report.fetched, kept=kept,
                         new_jobs=new, finished_at=datetime.now(timezone.utc),
                         message=(f"{kept} jobs match ({new} new); {report.hidden} filtered out, "
                                  f"{report.merged} duplicates merged"))
        except Exception as e:  # show the failure instead of spinning forever
            self._update(run_id, status="failed", error=f"{type(e).__name__}: {e}", finished_at=datetime.now(timezone.utc))
        finally:
            client.close()
            self.cancels.pop(run_id, None)
        return self.get(run_id)

    def _record_usage(self, report, client, search_id: Optional[int] = None) -> None:
        check = report.apify_check
        apify_requests = (1 if check and check.usage else 0) + (1 if report.apify_items or (check and check.ok) else 0)
        if client.requests - apify_requests > 0:
            self.repo.record_usage("feed_request", client.requests - apify_requests)
        if check and not check.ok and check.usage:
            self.repo.record_usage("apify_refused", 1, check.reason)
        if any(st.source == "apify" and st.status == "ok" for st in report.statuses):
            self.repo.record_usage("apify_run", 1, f"search:{search_id}")
            self.repo.record_usage("apify_results", report.apify_items)
            self.repo.record_usage("apify_cost_usd", report.apify_cost_usd, "estimate from the actor's price list")

    def _refresh_watchlist(self, report) -> None:
        """A run is also a feed check: keep each company's status and job count current."""
        by_key = {f"{c.ats}:{c.board}": c for c in self.repo.watchlist() if c.ats}
        for st in report.statuses:
            row = by_key.get(st.source)
            if row and st.status in ("ok", "error"):
                self.repo.update_company(row.id, status=st.status,
                                         job_count=st.jobs if st.status == "ok" else row.job_count,
                                         message=st.message, last_checked=now_iso())


def _discover_jobs():
    """Listings the autopilot's Discover step cached today (Simplify, Hacker News); None if there's no fresh cache."""
    from .autopilot_run import load_discover_jobs
    return load_discover_jobs()


def _describe_jobs(jobs):
    from .autopilot_run import describe_jobs
    return describe_jobs(jobs)


job_runs = JobSearchRuns()
