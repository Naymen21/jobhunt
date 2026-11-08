"""Read-only numbers for the Dashboard: one query pass over what Finder and Jobs already stored."""
from __future__ import annotations

import json
import os

from sqlmodel import Session, col, func, select

from .db import engine
from .jobs_repo import JobRepo
from .repo import SqlStore
from .tables import DomainRow, FoundEmailRow, JobRow, SavedSearchRow, SearchJobRow, Verification, WatchCompanyRow


def top_matches(limit: int = 6) -> list[dict]:
    """Best-scoring jobs from each search's latest run, skipping dismissed ones. One row per job."""
    with Session(engine) as s:
        latest = {r.id: r.last_run_id for r in s.exec(select(SavedSearchRow)) if r.last_run_id}
        best: dict[int, tuple[JobRow, SearchJobRow, int]] = {}
        for job, link in s.exec(select(JobRow, SearchJobRow).join(SearchJobRow, SearchJobRow.job_id == JobRow.id)
                                .where(JobRow.state != "dismissed")):
            if latest.get(link.search_id) != link.last_run_id:
                continue
            if job.id not in best or link.match_score > best[job.id][1].match_score:
                best[job.id] = (job, link, link.search_id)
    ranked = sorted(best.values(), key=lambda x: x[0].posted_at or "", reverse=True)  # newest first among equal scores
    ranked.sort(key=lambda x: -x[1].match_score)
    return [{"job": j, "score": l.match_score, "search_id": sid, "new": l.first_run_id == latest.get(sid),
             "flags": json.loads(j.flags_json or "[]")} for j, l, sid in ranked[:limit]]


def saved_jobs(limit: int = 6) -> tuple[list[JobRow], int]:
    with Session(engine) as s:
        rows = list(s.exec(select(JobRow).where(JobRow.state == "saved").order_by(col(JobRow.id).desc())))
    return rows[:limit], len(rows)


def summary() -> dict:
    repo = JobRepo()
    with Session(engine) as s:
        searches = list(s.exec(select(SavedSearchRow).order_by(SavedSearchRow.id)))
        watch = list(s.exec(select(WatchCompanyRow)))
        people = s.exec(select(func.count(func.distinct(FoundEmailRow.email))).where(FoundEmailRow.kind == "personal")).one()
        domains = s.exec(select(func.count()).select_from(DomainRow)).one()
        verifications = s.exec(select(func.count()).select_from(Verification)).one()
    profile = repo.profile()
    resume = repo.default_resume()
    saved, saved_total = saved_jobs()
    setup = [
        {"done": bool(profile.name), "label": "Fill in your profile", "href": "/settings"},
        {"done": resume is not None, "label": "Upload a resume (match scores use it)", "href": "/settings?tab=resumes"},
        {"done": bool(searches), "label": "Create a saved search", "href": "/jobs?edit=new"},
        {"done": any(r.last_run_id for r in searches), "label": "Run a search", "href": "/jobs"},
        {"done": bool(os.environ.get("APIFY_TOKEN")), "label": "Optional: add APIFY_TOKEN for LinkedIn", "href": "/settings?tab=keys"},
    ]
    return {
        "first_name": (profile.name or "").split(" ")[0],
        "searches": searches,
        "new_jobs": sum(r.last_new for r in searches),
        "jobs_listed": sum(r.last_total for r in searches),
        "saved": saved, "saved_total": saved_total,
        "top": top_matches(),
        "watch_feeds": sum(1 for w in watch if w.ats),
        "watch_ok": sum(1 for w in watch if w.ats and w.status == "ok"),
        "watch_failed": [w for w in watch if w.ats and w.status == "error"],
        "watch_manual": sum(1 for w in watch if not w.ats),
        "open_jobs": sum(w.job_count or 0 for w in watch if w.ats and w.status == "ok"),
        "people": people, "domains": domains, "verifications": verifications,
        "recent_domains": SqlStore().list_domains(5),
        "usage": repo.usage_this_month(),
        "setup": setup, "setup_left": sum(1 for x in setup if not x["done"]),
        "has_resume": resume is not None,
    }
