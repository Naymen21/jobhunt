"""Run one saved search: read every chosen source, merge duplicates, flag requirements, filter, score.
Saving is the caller's job (through a store), so this runs the same from the website, a script, or a test."""
from __future__ import annotations

import dataclasses
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from .dedupe import dedupe
from .filters import Evaluation, SearchSpec, evaluate
from .dedupe import norm_company
from .flags import add_pitch, find_flags
from .match import MatchResult, match, skills_in
from .models import SOURCE_LABELS, Job, SourceStatus, WatchCompany
from .sources.apify import ApifyQuery, ApifySource, BudgetCheck
from .sources.base import FeedClient, FeedError
from .sources.feeds import FEEDS

FEED_WORKERS = 4


@dataclass
class ScoredJob:
    job: Job
    evaluation: Evaluation
    match: MatchResult


@dataclass
class RunReport:
    statuses: list[SourceStatus] = field(default_factory=list)
    fetched: int = 0  # postings read, before merging duplicates
    merged: int = 0  # postings folded into another source's copy
    kept: list[ScoredJob] = field(default_factory=list)  # pass the search's filters
    hidden: int = 0  # read but filtered out
    apify_check: Optional[BudgetCheck] = None
    apify_items: int = 0
    apify_cost_usd: float = 0.0
    cancelled: bool = False


def years_required(job: Job) -> Optional[int]:
    flag = next((f for f in job.flags if f.key == "experience"), None)
    return int(re.match(r"\d+", flag.label).group()) if flag else None


def run_search(spec: SearchSpec, companies: list[WatchCompany], *, client: FeedClient,
               apify: Optional[ApifySource] = None, resume_text: Optional[str] = None,
               progress: Callable[[dict], None] = lambda e: None, cancel: Optional[threading.Event] = None,
               now: Optional[datetime] = None, apify_skip: Optional[str] = None,
               reused_jobs: Optional[list[Job]] = None, apify_spent_today: float = 0.0,
               discover_jobs: Optional[list[Job]] = None, company_info: Optional[dict] = None,
               describe_fn: Optional[Callable[[list[Job]], int]] = None) -> RunReport:
    """`discover_jobs`: internship listings from Discover (Simplify, Hacker News), used when the search includes
    the "discover" source. `company_info`: norm company name -> {"employees", "local"} for pitch tagging.
    `describe_fn`: fills in the full posting text for Discover listings that pass the other filters (title, location...),
    so degree requirements are read from the real posting, not the listing summary. Returns how many it filled."""
    """`apify_skip`: a reason not to run LinkedIn this time (e.g. today's run already happened).
    `reused_jobs`: LinkedIn jobs from that earlier run, filtered again with the current criteria instead of dropped."""
    cancel = cancel or threading.Event()
    report = RunReport()
    jobs: list[Job] = []
    lock = threading.Lock()

    def status(s: SourceStatus) -> None:
        with lock:
            report.statuses.append(s)
        progress({"source": s.source, "label": s.label, "status": s.status, "message": s.message, "jobs": s.jobs})

    # -- company feeds ----------------------------------------------------------------------
    if "watchlist" in spec.sources:
        chosen = [c for c in companies if c.ats in FEEDS and c.board and (not spec.companies or c.id in spec.companies)]
        progress({"total_sources": len(chosen) + ("apify" in spec.sources)})

        def read(c: WatchCompany) -> None:
            key, label = f"{c.ats}:{c.board}", f"{c.name} ({FEEDS[c.ats].label})"
            if cancel.is_set():
                status(SourceStatus(key, label, "skipped", "stopped"))
                return
            progress({"source": key, "label": label, "status": "running", "message": "reading feed"})
            try:
                found = FEEDS[c.ats].fetch(c, client)
            except FeedError as e:
                status(SourceStatus(key, label, "error", str(e)))
                return
            with lock:
                jobs.extend(found)
            status(SourceStatus(key, label, "ok", f"{len(found)} open jobs", len(found)))

        with ThreadPoolExecutor(max_workers=FEED_WORKERS, thread_name_prefix="feed") as pool:
            list(pool.map(read, chosen))
    else:
        progress({"total_sources": int("apify" in spec.sources)})

    # -- LinkedIn through Apify ---------------------------------------------------------------
    if "apify" in spec.sources:
        label = SOURCE_LABELS["apify"]
        if apify is None or not apify.configured:
            status(SourceStatus("apify", label, "not_configured", "not configured: add APIFY_TOKEN to .env"))
        elif cancel.is_set():
            status(SourceStatus("apify", label, "skipped", "stopped"))
        elif apify_skip:
            reused = list(reused_jobs or [])
            jobs.extend(reused)
            status(SourceStatus("apify", label, "skipped",
                                apify_skip + (f"; reused {len(reused)} LinkedIn job{'s' if len(reused) != 1 else ''} from today's earlier run"
                                              if reused else ""), len(reused)))
        else:
            progress({"source": "apify", "label": label, "status": "running", "message": "checking Apify usage"})
            check = apify.check_budget(spec.apify_max_results, client, spent_today=apify_spent_today)
            report.apify_check = check
            if not check.ok:
                status(SourceStatus("apify", label, "refused", check.reason))
            else:
                places = [l for l in spec.locations if l.strip().lower() != "remote"]
                query = ApifyQuery(spec.apify_keywords(), places[0] if places else "", spec.posted_within_days,
                                   check.max_results)
                progress({"source": "apify", "label": label, "status": "running",
                          "message": f"running LinkedIn search (max {check.max_results} results, cap ${check.cap_usd:.2f})"})
                try:
                    run = apify.search(query, client, check)
                    jobs.extend(run.jobs)
                    report.apify_items, report.apify_cost_usd = run.items, run.estimate_usd
                    status(SourceStatus("apify", label, "ok", f"{run.items} results (about ${run.estimate_usd:.3f})", run.items))
                except FeedError as e:
                    status(SourceStatus("apify", label, "error", str(e)))

    if "discover" in spec.sources:
        found = list(discover_jobs or [])
        jobs.extend(found)
        message = f"{len(found)} internship listings" if discover_jobs is not None else "not loaded this run"
        if found and describe_fn and not cancel.is_set():
            # cheap filters first (they don't need the posting text), then fetch full postings only for what's left
            loose = dataclasses.replace(spec, high_school_only=False, no_degree_only=False, min_employees=None,
                                        max_employees=None, hide_unknown_size=False)
            candidates = [j for j in found if evaluate(j, loose, None, now).passes]
            progress({"source": "discover", "label": "Discover (Simplify, Hacker News)", "status": "running",
                      "message": f"reading {len(candidates)} full postings"})
            filled = describe_fn(candidates)
            message += f"; full posting text for {filled} of {len(candidates)} that match"
        status(SourceStatus("discover", "Discover (Simplify, Hacker News)", "ok" if discover_jobs is not None else "skipped",
                            message, len(found)))

    report.cancelled = cancel.is_set()
    report.fetched = len(jobs)
    progress({"message": "merging duplicates and scoring"})

    # -- merge, flag, filter, score ---------------------------------------------------------------
    merged = dedupe(jobs)
    report.merged = len(jobs) - len(merged)
    resume_skills = skills_in(resume_text) if resume_text else None
    for job in merged:
        job.flags = find_flags(job.description, title=job.title)
        info = (company_info or {}).get(norm_company(job.company))
        if info:
            job.flags = add_pitch(job.flags, info.get("employees"))
        ev = evaluate(job, spec, years_required(job), now, employees=info.get("employees") if info else None)
        if not ev.passes:
            report.hidden += 1
            continue
        report.kept.append(ScoredJob(job, ev, match(job.title, job.description, ev, resume_skills, job.flags)))
    report.kept.sort(key=lambda s: -s.match.score)
    return report
