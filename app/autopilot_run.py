"""The daily autopilot: discover -> sizes -> boards -> searches -> AI scores -> auto-save -> application drafts ->
contacts -> outreach drafts -> maintenance. Every step is logged; AI steps go through Claude Code on the owner's
subscription with daily caps, and stop cleanly at a usage limit (the afternoon catch-up run retries them).
Nothing is sent or submitted: approved items only, as always."""
from __future__ import annotations

import gzip
import json
import shutil
import sqlite3
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import httpx
from sqlmodel import Session, select

from autopilot import claude as claude_mod
from autopilot.rules import AutopilotSettings, Candidate, due_weekly, pick_saves, remaining
from finder.config import PROJECT_ROOT
from finder.models import now_iso
from jobsearch import discover
from jobsearch.config import settings as job_settings
from jobsearch.dedupe import norm_company
from jobsearch.flags import eligible_for_high_schooler, is_pitch, open_to_high_school
from storage.apps_repo import AppRepo
from storage.companies_repo import CompanyRepo
from storage.db import DB_PATH, engine
from storage.jobs_repo import JobRepo
from storage.outreach_repo import OutreachRepo
from storage.tables import (ApplicationRow, AutopilotRunRow, ContactRow, FoundEmailRow, JobRow, JobSourceRow, MetaRow,
                            SearchJobRow)

CACHE_DIR = PROJECT_ROOT / "data" / "discover_cache"
UA = "JobHuntFinder/0.3 (personal job-search tool; respects robots.txt)"
AI_STEPS = ["redrafts", "scores", "app_drafts", "outreach_drafts"]


# --- settings + daily counters (stored in app_meta) ------------------------------------------------------------------

def _meta(key: str) -> Optional[str]:
    with Session(engine) as s:
        row = s.get(MetaRow, key)
        return row.value if row else None


def _set_meta(key: str, value: str) -> None:
    with Session(engine) as s:
        row = s.get(MetaRow, key) or MetaRow(key=key)
        row.value = value
        s.add(row)
        s.commit()


def load_settings() -> AutopilotSettings:
    return AutopilotSettings.from_dict(json.loads(_meta("autopilot_settings") or "{}"))


def save_settings(s: AutopilotSettings) -> None:
    _set_meta("autopilot_settings", json.dumps(s.to_dict()))


def today_key() -> str:
    return f"autopilot_day_{datetime.now().date().isoformat()}"


def counts_today() -> dict:
    return json.loads(_meta(today_key()) or "{}")


def bump(name: str, n: int) -> None:
    c = counts_today()
    c[name] = c.get(name, 0) + max(0, n)
    _set_meta(today_key(), json.dumps(c))


def redraft_queue() -> list[dict]:
    return json.loads(_meta("autopilot_redrafts") or "[]")


def queue_redraft(kind: str, target_id: int, note: str) -> None:
    q = redraft_queue()
    q.append({"kind": kind, "id": target_id, "note": note.strip()[:400], "at": now_iso()})
    _set_meta("autopilot_redrafts", json.dumps(q))


def last_run() -> Optional[AutopilotRunRow]:
    with Session(engine) as s:
        return s.exec(select(AutopilotRunRow).order_by(AutopilotRunRow.id.desc())).first()


# --- the run ------------------------------------------------------------------------------------------------------------

class Autopilot:
    def __init__(self, settings: Optional[AutopilotSettings] = None, claude: Optional[Callable] = None,
                 client: Optional[httpx.Client] = None, page_fn: Optional[Callable] = None,
                 detect_fn: Optional[Callable] = None, finder_fn: Optional[Callable] = None, run_searches: bool = True):
        self.s = settings or load_settings()
        self.claude = claude  # injectable: (command, args) -> ClaudeResult
        self.client = client
        self.page_fn = page_fn
        self.detect_fn = detect_fn
        self.finder_fn = finder_fn
        self.run_searches = run_searches
        self.jobs, self.apps, self.out, self.companies = JobRepo(), AppRepo(), OutreachRepo(), CompanyRepo()
        self.log: list[dict] = []
        self.claude_status = "not_needed"
        self.claude_reset = ""
        self.pending_ai: list[str] = []
        self.ai_blocked = False

    # -- logging -----------------------------------------------------------------------------------------------------
    def _log(self, step: str, status: str, detail: str = "", **counts) -> None:
        self.log.append({"step": step, "status": status, "detail": detail[:600], "counts": counts, "at": now_iso()})

    def _save_run(self, run_id: int, status: str) -> None:
        with Session(engine) as s:
            r = s.get(AutopilotRunRow, run_id)
            r.status, r.log_json = status, json.dumps(self.log)
            r.claude_status, r.claude_reset, r.pending_ai_json = self.claude_status, self.claude_reset, json.dumps(self.pending_ai)
            r.finished_at = now_iso() if status != "running" else None
            s.add(r)
            s.commit()

    def run(self, kind: str = "morning") -> AutopilotRunRow:
        with Session(engine) as s:
            row = AutopilotRunRow(kind=kind, started_at=now_iso())
            s.add(row)
            s.commit()
            s.refresh(row)
            run_id = row.id
        key = claude_mod.api_key_present()
        if key:
            self.claude_status = "refused"
            self._log("safety", "refused", f"{key} is set in the environment. The autopilot won't run while an Anthropic API "
                                          f"credential exists (it could bill the API). Remove it, then run again.")
            self._save_run(run_id, "refused")
            return self._get(run_id)
        if not self.s.enabled:
            self._log("autopilot", "skipped", "turned off in Settings > Autopilot")
            self._save_run(run_id, "done")
            return self._get(run_id)
        steps = [("discover", self.step_discover), ("sizes", self.step_sizes), ("boards", self.step_boards),
                 ("searches", self.step_searches)] if kind != "catch-up" else []
        steps += [("redrafts", self.step_redrafts), ("scores", self.step_scores), ("auto_save", self.step_auto_save),
                  ("app_drafts", self.step_app_drafts), ("contacts", self.step_contacts), ("cold", self.step_cold),
                  ("outreach_drafts", self.step_outreach_drafts), ("maintenance", self.step_maintenance)]
        for name, fn in steps:
            try:
                fn()
            except Exception as e:  # one broken step mustn't stop the rest
                self._log(name, "failed", f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=2)}")
            self._save_run(run_id, "running")
        status = "stopped" if self.ai_blocked else "done"
        self._save_run(run_id, status)
        return self._get(run_id)

    def _get(self, run_id: int) -> AutopilotRunRow:
        with Session(engine) as s:
            return s.get(AutopilotRunRow, run_id)

    # -- 1. discover ---------------------------------------------------------------------------------------------------
    def _http(self) -> httpx.Client:
        if self.client is None:
            self.client = httpx.Client(timeout=45, headers={"User-Agent": UA}, follow_redirects=True)
        return self.client

    def step_discover(self) -> None:
        if not any([self.s.source_yc, self.s.source_yc_small, self.s.source_builtin, self.s.source_hn, self.s.source_simplify]):
            self._log("discover", "skipped", "all discovery sources are off")
            return
        c = self._http()
        found: list[discover.DiscoveredCompany] = []
        jobs = []
        notes = []
        if self.s.source_yc:
            try:
                found += discover.parse_yc(c.get(discover.YC_URL).json())
                notes.append(f"YC: {sum(1 for x in found if x.source == 'yc')} hiring companies with 30-100 people")
            except Exception as e:
                notes.append(f"YC failed: {type(e).__name__}")
        if self.s.source_yc_small:
            try:
                n0 = len(found)
                small = discover.parse_yc_all(c.get(discover.YC_ALL_URL).json())
                have = {x.name for x in found}
                found += [x for x in small if x.name not in have]
                notes.append(f"YC small companies (5-60 people, for cold pitches): {len(found) - n0}")
            except Exception as e:
                notes.append(f"YC small companies failed: {type(e).__name__}")
        from jobsearch.student import current
        state = current().home_state
        if self.s.source_builtin and not state:
            notes.append("Built In skipped: set your home state in Settings > Profile")
        if self.s.source_builtin and state:
            n0 = len(found)
            for page in range(1, self.s.builtin_pages + 1):
                try:
                    r = c.get(discover.builtin_url(state) + (f"?page={page}" if page > 1 else ""))
                    if r.status_code != 200:
                        break
                    found += [x for x in discover.parse_builtin(r.text, state)
                              if x.employees is None or discover.in_size_range(x.employees, 10, 150)]
                except Exception as e:
                    notes.append(f"Built In page {page} failed: {type(e).__name__}")
                    break
            notes.append(f"Built In {state}: {len(found) - n0} companies")
        if self.s.source_hn:
            try:
                n0 = len(found)
                user = c.get(discover.HN_USER).json()
                thread = None
                for item_id in user.get("submitted", [])[:6]:
                    item = c.get(discover.HN_ITEM.format(item_id)).json() or {}
                    if "who is hiring" in (item.get("title") or "").lower():
                        thread = item
                        break
                if thread:
                    from concurrent.futures import ThreadPoolExecutor
                    kids = (thread.get("kids") or [])[:400]
                    with ThreadPoolExecutor(max_workers=8) as pool:
                        items = list(pool.map(lambda k: c.get(discover.HN_ITEM.format(k)).json(), kids))
                    for item in items:
                        comp, job = discover.parse_hn_comment(item, thread_url="")
                        if comp:
                            found.append(comp)
                        if job:
                            jobs.append(job)
                notes.append(f"Hacker News: {len(found) - n0} companies, {len(jobs)} posts mentioning interns")
            except Exception as e:
                notes.append(f"Hacker News failed: {type(e).__name__}")
        if self.s.source_simplify:
            try:
                data = c.get(discover.SIMPLIFY_URL).json()
                sjobs = discover.parse_simplify(data)
                jobs += sjobs
                notes.append(f"Simplify: {len(sjobs)} active internship listings")
            except Exception as e:
                notes.append(f"Simplify failed: {type(e).__name__}")
        new = 0
        for comp in found:
            _, is_new = self.companies.upsert(comp)
            new += is_new
        if jobs:  # a failed fetch never wipes yesterday's listings
            save_discover_jobs(jobs)
        local = sum(1 for x in found if x.local)
        self._log("discover", "ok", "; ".join(notes), companies=len(found), new=new, local=local, listings=len(jobs))

    # -- 2. sizes -------------------------------------------------------------------------------------------------------
    def step_sizes(self) -> None:
        added = self.companies.ensure_from_watchlist()
        checked = sized = 0
        page_fn = self.page_fn
        for row in self.companies.needing_size(self.s.size_checks_per_day):
            checked += 1
            n, src, url = None, "", ""
            if row.domain or row.website:
                if page_fn is None:
                    from jobsearch.detect import default_page_fn
                    page_fn = self.page_fn = default_page_fn()
                base = row.website if row.website.startswith("http") else f"https://{row.domain or row.website}"
                for path in ("", "/about", "/team", "/about-us", "/company"):
                    page = page_fn(base.rstrip("/") + path)
                    if page:
                        from bs4 import BeautifulSoup
                        n = discover.headcount_from_text(" ".join(BeautifulSoup(page[1], "lxml").get_text(" ").split()))
                        if n:
                            src, url = "company site", page[0]
                            break
            if not n and row.domain:
                people = self.companies.finder_people(row.domain)
                if people:
                    n, src = people, "Finder (people found)"
            if n:
                self.companies.set_size(row.name, n, src, url)
                sized += 1
        self._log("sizes", "ok", f"{added} watchlist companies added to profiles", checked=checked, sized=sized)

    # -- 3. boards ------------------------------------------------------------------------------------------------------
    def step_boards(self) -> None:
        from jobsearch.detect import detect_ats
        from jobsearch.sources import FEEDS, FeedClient
        from storage.repo import SqlStore
        detect = self.detect_fn or (lambda q, client: detect_ats(q, client, store=SqlStore()))
        client = FeedClient(job_settings)
        added = none = 0
        watch = {(w.ats, w.board) for w in self.jobs.watchlist()}
        try:
            for row in self.companies.needing_board(self.s.board_checks_per_day):
                det = detect(row.website or row.name, client)
                if det.status == "found":
                    if (det.ats, det.board) not in watch:
                        w = self.jobs.add_company(name=row.name, ats=det.ats, board=det.board, domain=det.domain or row.domain,
                                                  status="ok", job_count=det.jobs, detected_how=f"Discover ({row.source}): {det.how}",
                                                  message=f"{det.jobs} open jobs", last_checked=now_iso())
                        watch.add((det.ats, det.board))
                        self.companies.set_board(row.name, "found", w.id, det.domain or "")
                        added += 1
                    else:
                        self.companies.set_board(row.name, "found")
                else:
                    self.companies.set_board(row.name, "none", domain=det.domain or "")
                    none += 1
        finally:
            client.close()
        self._log("boards", "ok", "companies with a Greenhouse/Lever/Ashby feed join the watchlist", added=added, no_feed=none)

    # -- 4. searches -----------------------------------------------------------------------------------------------------
    def step_searches(self) -> None:
        if not self.run_searches:
            self._log("searches", "skipped", "searches off for this run")
            return
        from .jobs import job_runs
        ran = kept = new = 0
        problems = []
        for s in self.jobs.searches():
            if not s.auto_run:
                continue
            run = job_runs.execute(job_runs.create(s.id, trigger="autopilot"), s.id)
            ran += 1
            if run.status == "done":
                kept, new = kept + run.kept, new + run.new_jobs
                li = json.loads(run.sources_json or "{}").get("apify")
                if li and li.get("status") not in ("ok", None):
                    problems.append(f"{s.name}: LinkedIn {li['status']} ({li.get('message', '')[:80]})")
            else:
                problems.append(f"{s.name}: {run.status} {run.error or ''}")
        self._log("searches", "ok", "; ".join(problems), searches=ran, jobs=kept, new=new)

    # -- AI steps (Claude Code) -------------------------------------------------------------------------------------------
    def _claude(self, command: str, args: str):
        if self.claude:
            return self.claude(command, args)
        return claude_mod.run_command(command, args, PROJECT_ROOT)

    def _ai(self, step: str, command: str, args: str) -> bool:
        """Run one Claude command. False when the AI steps must stop (limit / refused / missing)."""
        if self.ai_blocked:
            self.pending_ai.append(step)
            self._log(step, "waiting", "Claude usage limit reached earlier in this run; the catch-up run retries this")
            return False
        res = self._claude(command, args)
        if res.status == "ok":
            self.claude_status = "ok"
            return True
        if res.status == "limit":
            self.ai_blocked, self.claude_status, self.claude_reset = True, "limit", res.reset
            self.pending_ai.append(step)
            self._log(step, "limit", f"Claude usage limit reached{' (resets ' + res.reset + ')' if res.reset else ''}. "
                                     f"Stopping AI steps cleanly; no extra usage is used. The catch-up run retries.")
            return False
        self.claude_status = res.status
        if res.status in ("refused", "missing"):
            self.ai_blocked = True
        self._log(step, "failed", res.output[-500:])
        return False

    def step_redrafts(self) -> None:
        q = redraft_queue()
        room = remaining(self.s.cap_redrafts, counts_today().get("redrafts", 0))
        if not q:
            return
        done, left = 0, []
        for item in q:
            if done >= room or self.ai_blocked:
                left.append(item)
                continue
            cmd = "redraft" if item["kind"] == "app" else "redraft-outreach"
            if self._ai("redrafts", cmd, f"{item['id']} {item['note']}"):
                done += 1
            else:
                left.append(item)
        _set_meta("autopilot_redrafts", json.dumps(left))
        bump("redrafts", done)
        self._log("redrafts", "ok" if not left else "partial", f"{len(left)} left in the queue", done=done)

    def step_scores(self) -> None:
        room = remaining(self.s.cap_scores, counts_today().get("scores", 0))
        before = self.apps.pending_scores(1)["pending_total"]
        if room == 0 or before == 0:
            self._log("scores", "skipped", "daily cap reached" if room == 0 else "nothing to score", pending=before)
            return
        if self._ai("scores", "score-jobs", str(min(room, before))):
            after = self.apps.pending_scores(1)["pending_total"]
            bump("scores", before - after)
            self._log("scores", "ok", "", scored=before - after, still_pending=after)

    def step_auto_save(self) -> None:
        saved_today = counts_today().get("saves", 0)
        info = self.companies.size_map()
        cands = []
        with Session(engine) as s:
            for j in s.exec(select(JobRow).where(JobRow.ai_score != None, JobRow.state == "")):  # noqa: E711
                flags = json.loads(j.flags_json or "[]")
                ci = info.get(norm_company(j.company), {})
                cands.append(Candidate(j.id, j.ai_score, eligible_for_high_schooler(flags), is_pitch(flags),
                                       open_to_high_school(flags), bool(ci.get("local")), ci.get("employees"), j.state))
        chosen = pick_saves(cands, self.s, saved_today)
        for c in chosen:
            self.jobs.set_state(c.job_id, "saved")
            app = self.apps.ensure(c.job_id)
            if c.pitch:
                job = self.jobs.job(c.job_id)
                self.out.set_prefs(job.company, reach_out_first=True)  # pitches go out before any application
        bump("saves", len(chosen))
        self._log("auto_save", "ok", f"rules: AI score >= {self.s.min_ai_score}, eligible or pitch candidate, "
                                    f"max {self.s.max_saves_per_day}/day", saved=len(chosen), pitches=sum(c.pitch for c in chosen))

    def step_app_drafts(self) -> None:
        room = remaining(self.s.cap_app_drafts, counts_today().get("app_drafts", 0))
        before = self.apps.pending_drafts(1)["pending_total"]
        if room == 0 or before == 0:
            self._log("app_drafts", "skipped", "daily cap reached" if room == 0 else "no saved jobs waiting for drafts "
                                                                                    "(pitch candidates wait for a yes)")
            return
        if self._ai("app_drafts", "draft-applications", str(min(room, before))):
            after = self.apps.pending_drafts(1)["pending_total"]
            bump("app_drafts", before - after)
            self._log("app_drafts", "ok", "", drafted=before - after, still_pending=after)

    def step_contacts(self) -> None:
        searched = picked = needs = 0
        with Session(engine) as s:
            apps = [(a, s.get(JobRow, a.job_id)) for a in s.exec(select(ApplicationRow).where(
                ApplicationRow.status.in_(["saved", "drafts_ready", "approved", "applied"])))]
        have = {c.application_id for c in self.out.contacts()}
        for app, job in apps:
            if app.id in have or app.needs_person:
                continue
            pitch = is_pitch(json.loads(job.flags_json or "[]"))
            domain = self.out.company_domain(job.company)
            if not domain or not self._finder_done(domain):
                if searched >= self.s.finder_searches_per_day:
                    continue
                domain = self._find_people(job.company, domain)
                searched += 1
                if not domain:
                    with Session(engine) as s:
                        a = s.get(ApplicationRow, app.id)
                        a.needs_person = True
                        s.add(a)
                        s.commit()
                    needs += 1
                    continue
            got = self.out.auto_pick(app.id, pitch)
            picked += len(got)
            needs += not got
        self._log("contacts", "ok", "no good match -> 'Needs a person' (never a guess)", finder_searches=searched,
                  picked=picked, needs_person=needs)

    def step_cold(self) -> None:
        """Cold pitches: up to cold_per_day companies from your Find companies filters get a founder / tech lead whose
        address was seen in a public source. Never a guess; drafting happens in the outreach drafts step."""
        from outreach.cold import ColdFilters, auto_choice, filter_companies
        room = remaining(self.s.cold_per_day, counts_today().get("cold_picks", 0))
        if room == 0:
            self._log("cold", "skipped", "cold pitches per day is 0 (Settings > Autopilot)" if not self.s.cold_per_day
                      else "today's cold pitches already started")
            return
        f = ColdFilters.from_dict({**self.s.cold_filters, "hide_contacted": True, "show_hidden": False})
        searched = picked = skipped = 0
        dnc = self.out.do_not_contact()
        for co in filter_companies(self.companies.cold_rows(), f):
            if picked >= room:
                break
            if _meta(f"cold_tried_{co['key']}"):
                continue  # already looked here and found no one good
            domain = co["domain"] or self.out.company_domain(co["name"])
            if not domain or not self._finder_done(domain):
                if searched >= self.s.finder_searches_per_day:
                    break
                domain = self._find_people(co["website"] or co["name"], domain)
                searched += 1
            choice = auto_choice(self.out.people(domain), exclude=dnc) if domain else None
            if not choice:
                _set_meta(f"cold_tried_{co['key']}", now_iso())
                skipped += 1
                continue
            try:
                self.out.pick_cold(co["key"], choice.email, how=f"autopilot ({choice.reason})")
            except ValueError:
                skipped += 1
                continue
            picked += 1
        bump("cold_picks", picked)
        self._log("cold", "ok", "founder / tech lead with an address seen in public only; drafts come next",
                  finder_searches=searched, picked=picked, no_good_person=skipped)

    def _finder_done(self, domain: str) -> bool:
        with Session(engine) as s:
            return s.exec(select(FoundEmailRow).where(FoundEmailRow.domain == domain)).first() is not None

    def _find_people(self, company: str, domain: Optional[str]) -> Optional[str]:
        if self.finder_fn:
            return self.finder_fn(company, domain)
        from finder.discovery import search_domain
        from finder.resolve import resolve_company
        from storage.repo import SqlStore
        store = SqlStore()
        if not domain:
            res = resolve_company(company, store)
            domain = res.domain
            if not domain:
                return None
        search_domain(domain, store, refresh=False)
        return domain

    def step_outreach_drafts(self) -> None:
        room = remaining(self.s.cap_outreach_drafts, counts_today().get("outreach_drafts", 0))
        before = self.out.pending(1)["pending_total"]
        if room == 0 or before == 0:
            self._log("outreach_drafts", "skipped", "daily cap reached" if room == 0 else "no picked contacts waiting")
            return
        if self._ai("outreach_drafts", "draft-outreach", str(min(room, before))):
            after = self.out.pending(1)["pending_total"]
            bump("outreach_drafts", before - after)
            self._log("outreach_drafts", "ok", "", drafted=before - after, still_pending=after)

    # -- maintenance -------------------------------------------------------------------------------------------------------
    def step_maintenance(self) -> None:
        notes = []
        path = backup_database(Path(self.s.backup_dir), self.s.backups_keep)
        notes.append(f"backup: {path.name}" if path else "backup: already done today")
        removed = cleanup_dismissed(self.s.dismissed_cleanup_days)
        notes.append(f"removed {removed} dismissed jobs older than {self.s.dismissed_cleanup_days} days")
        now = datetime.now()
        if due_weekly(_meta("autopilot_stale_checked"), now):
            stale = mark_stale_contacts(self.s.stale_contact_days)
            _set_meta("autopilot_stale_checked", now.isoformat(timespec="seconds"))
            notes.append(f"{len(stale)} contacts last seen 6+ months ago flagged stale")
        self._log("maintenance", "ok", "; ".join(notes))


# --- discover listings cache (used by searches with the "discover" source) ----------------------------------------------

def save_discover_jobs(jobs: list) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / "listings.json"
    path.write_text(json.dumps({"saved_at": now_iso(), "jobs": [j.to_dict() for j in jobs]}), encoding="utf-8")
    return path


def load_discover_jobs(max_age_hours: int = 30) -> Optional[list]:
    from jobsearch.models import Job
    path = CACHE_DIR / "listings.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    age = datetime.now(timezone.utc) - datetime.fromisoformat(data["saved_at"])
    if age > timedelta(hours=max_age_hours):
        return None
    return [Job.from_dict(d) for d in data["jobs"]]


def describe_jobs(jobs: list, limit: int = 200) -> int:
    """Full posting text for Discover listings (robots.txt respected), cached by URL in data/discover_cache."""
    from jobsearch.describe import enrich, polite_getters
    path = CACHE_DIR / "descriptions.json"
    try:
        cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except ValueError:
        cache = {}
    get_json, get_page = polite_getters()
    got = enrich(jobs, get_json, get_page, cache, limit=limit)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if len(cache) > 6000:  # keep the newest entries; dicts keep insertion order
        cache = dict(list(cache.items())[-6000:])
    path.write_text(json.dumps(cache), encoding="utf-8")
    return got


# --- maintenance helpers ---------------------------------------------------------------------------------------------------

def backup_database(folder: Path, keep: int = 14, db_path: Path = DB_PATH, today: Optional[str] = None) -> Optional[Path]:
    """SQLite's own backup API (safe while the site is open), gzip-compressed. One per day; keeps the newest `keep`."""
    folder.mkdir(parents=True, exist_ok=True)
    stamp = today or datetime.now().strftime("%Y-%m-%d")
    target = folder / f"jobhunt-{stamp}.db.gz"
    if target.exists():
        return None
    tmp = folder / f".jobhunt-{stamp}.tmp"
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(tmp))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    with open(tmp, "rb") as f_in, gzip.open(target, "wb", compresslevel=9) as f_out:
        shutil.copyfileobj(f_in, f_out)
    tmp.unlink(missing_ok=True)
    backups = sorted(folder.glob("jobhunt-*.db.gz"))
    for old in backups[:-keep]:
        old.unlink(missing_ok=True)
    return target


def cleanup_dismissed(days: int = 60) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    n = 0
    with Session(engine) as s:
        apps = {a.job_id for a in s.exec(select(ApplicationRow))}
        for j in s.exec(select(JobRow).where(JobRow.state == "dismissed", JobRow.last_seen < cutoff)):
            if j.id in apps:
                continue
            for row in s.exec(select(SearchJobRow).where(SearchJobRow.job_id == j.id)):
                s.delete(row)
            for row in s.exec(select(JobSourceRow).where(JobSourceRow.job_id == j.id)):
                s.delete(row)
            s.delete(j)
            n += 1
        s.commit()
    return n


def mark_stale_contacts(days: int = 182) -> list[int]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    stale = []
    with Session(engine) as s:
        for c in s.exec(select(ContactRow)):
            sightings = list(s.exec(select(FoundEmailRow).where(FoundEmailRow.email == c.email)))
            if sightings and max(r.last_seen for r in sightings) < cutoff:
                stale.append(c.id)
    _set_meta("stale_contacts", json.dumps(stale))
    return stale


def stale_contacts() -> set[int]:
    return set(json.loads(_meta("stale_contacts") or "[]"))
