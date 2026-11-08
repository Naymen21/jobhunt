"""Job Search page: saved searches, runs, results, job details, and the company watchlist."""
from __future__ import annotations

import json
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from finder.models import now_iso
from jobsearch.config import settings as job_settings
from jobsearch.detect import boards_in, detect_ats
from applications.status import REMOVABLE, STATUS_LABELS
from jobsearch.filters import DEFAULT_EXCLUDE_EXPERIENCE, SearchSpec
from jobsearch.flags import POSITIVE, eligible_for_high_schooler, open_to_high_school
from jobsearch.models import ATS_TYPES, JOB_TYPE_LABELS, WORK_MODE_LABELS, Pay
from jobsearch.normalize import pay_label
from jobsearch.sources import FEEDS, ApifySource, FeedClient
from storage.apps_repo import AppRepo
from storage.jobs_repo import JobRepo
from storage.repo import SqlStore

from ..jobs import job_runs
from ..templating import templates

router = APIRouter(prefix="/jobs")
repo = JobRepo()
apps = AppRepo()

POSTED_CHOICES = [(None, "Any time"), (1, "Past day"), (3, "Past 3 days"), (7, "Past week"), (14, "Past 2 weeks"),
                  (30, "Past month")]


def _split(value: str) -> list[str]:
    return [v.strip() for v in (value or "").replace("\n", ",").split(",") if v.strip()]


def _int(value) -> Optional[int]:
    try:
        return int(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def _common() -> dict:
    return {"active": "jobs", "WORK_MODE_LABELS": WORK_MODE_LABELS, "JOB_TYPE_LABELS": JOB_TYPE_LABELS, "STATUS_LABELS": STATUS_LABELS,
            "apify_configured": ApifySource(job_settings).configured, "FEEDS": FEEDS}


def _search_cards() -> list[dict]:
    cards = []
    for row in repo.searches():
        latest = job_runs.latest(row.id)
        cards.append({"row": row, "spec": repo.spec_of(row), "running": latest and latest.status in ("queued", "running"),
                      "latest": latest})
    return cards


# -- page -------------------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
def page(request: Request, search: Optional[int] = None, tab: str = "searches", edit: Optional[str] = None):
    cards = _search_cards()
    selected = None
    if tab == "searches":
        if search:
            selected = repo.search(search)
        elif cards and edit != "new":
            selected = cards[0]["row"]
    ctx = {**_common(), "tab": tab, "cards": cards, "selected": selected, "editing": None}
    if tab == "watchlist":
        ctx.update(_watchlist_ctx())
    elif edit == "new" or (not cards and tab == "searches"):
        ctx["editing"] = _editor_ctx(None)
    elif selected:
        ctx.update(_results_ctx(selected.id))
        run = job_runs.latest(selected.id)
        ctx["run"] = run if run and run.status in ("queued", "running") else None
    return templates.TemplateResponse(request, "jobs.html", ctx)


@router.get("/searches/list", response_class=HTMLResponse)
def search_list(request: Request, selected: Optional[int] = None):
    return templates.TemplateResponse(request, "partials/job_search_list.html",
                                      {**_common(), "cards": _search_cards(), "selected": repo.search(selected) if selected else None})


# -- saved searches ---------------------------------------------------------------------------

def _editor_ctx(search_id: Optional[int]) -> dict:
    row = repo.search(search_id) if search_id else None
    spec = repo.spec_of(row) if row else SearchSpec()
    feed_companies = [c for c in repo.watchlist() if c.ats]
    return {"row": row, "spec": spec, "posted_choices": POSTED_CHOICES, "feed_companies": feed_companies,
            "default_exclude": DEFAULT_EXCLUDE_EXPERIENCE, "max_ceiling": job_settings.apify_max_results_ceiling}


@router.get("/searches/{search_id}/edit", response_class=HTMLResponse)
def edit_search(request: Request, search_id: int):
    if not repo.search(search_id):
        raise HTTPException(404)
    return templates.TemplateResponse(request, "partials/job_search_form.html", {**_common(), "editing": _editor_ctx(search_id)})


@router.post("/searches", response_class=HTMLResponse)
@router.post("/searches/{search_id}", response_class=HTMLResponse)
async def save_search(request: Request, search_id: Optional[int] = None):
    form = await request.form()
    spec = SearchSpec(
        include_keywords=_split(form.get("include_keywords", "")),
        require_keywords=_split(form.get("require_keywords", "")),
        high_school_only=form.get("eligibility") == "hs_only",
        no_degree_only=form.get("eligibility") == "no_degree",
        min_employees=_int(form.get("min_employees")),
        max_employees=_int(form.get("max_employees")),
        hide_unknown_size=form.get("hide_unknown_size") == "on",
        apify_query=str(form.get("apify_query", "")).strip()[:120],
        exclude_keywords=_split(form.get("exclude_keywords", "")),
        locations=_split(form.get("locations", "")),
        work_modes=[m for m in form.getlist("work_modes") if m in WORK_MODE_LABELS],
        job_types=[t for t in form.getlist("job_types") if t in JOB_TYPE_LABELS],
        min_pay=_int(form.get("min_pay")),
        posted_within_days=_int(form.get("posted_within_days")),
        exclude_experience=_split(form.get("exclude_experience", "")),
        sources=[s for s in form.getlist("sources") if s in ("watchlist", "apify", "discover")] or ["watchlist"],
        companies=[c for c in (_int(x) for x in form.getlist("companies")) if c] if form.get("company_scope") == "some" else [],
        apify_max_results=max(1, min(_int(form.get("apify_max_results")) or job_settings.apify_max_results,
                                     job_settings.apify_max_results_ceiling)),
        apify_runs_per_day=max(0, min(_int(form.get("apify_runs_per_day")) if form.get("apify_runs_per_day") not in (None, "") else 1, 10)),
    )
    name = (form.get("name") or "").strip() or (", ".join(spec.include_keywords[:2]) or "My search")
    row = repo.save_search(search_id, name, spec, auto_run=form.get("auto_run") == "on")
    if search_id:
        default = repo.default_resume()
        repo.rescore(default.text if default else None)
    return Response(status_code=204, headers={"HX-Redirect": f"/jobs?search={row.id}"})


@router.post("/searches/{search_id}/delete", response_class=HTMLResponse)
def delete_search(search_id: int):
    repo.delete_search(search_id)
    return Response(status_code=204, headers={"HX-Redirect": "/jobs"})


# -- runs ---------------------------------------------------------------------------------------

def _run_fragment(request: Request, run_id: int):
    run = job_runs.get(run_id)
    if not run:
        raise HTTPException(404)
    resp = templates.TemplateResponse(request, "partials/job_run_progress.html",
                                      {**_common(), "run": run, "sources": json.loads(run.sources_json or "{}")})
    if run.status not in ("queued", "running"):
        resp.headers["HX-Trigger"] = json.dumps({"searchesChanged": {"search": run.search_id}})
    return resp


@router.post("/searches/{search_id}/run", response_class=HTMLResponse)
def run_search_now(request: Request, search_id: int):
    if not repo.search(search_id):
        raise HTTPException(404)
    run_id = job_runs.start(search_id)
    if "hx-request" not in request.headers:  # plain form post from another search's Run button
        return RedirectResponse(f"/jobs?search={search_id}", status_code=303)
    return _run_fragment(request, run_id)


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_status(request: Request, run_id: int):
    return _run_fragment(request, run_id)


@router.post("/runs/{run_id}/cancel", response_class=HTMLResponse)
def cancel_run(request: Request, run_id: int):
    job_runs.cancel(run_id)
    return _run_fragment(request, run_id)


# -- results ------------------------------------------------------------------------------------

def _row_view(job, link, search, app_status: Optional[str] = None) -> dict:
    pay = Pay(**json.loads(job.pay_json)) if job.pay_json else None
    sources = json.loads(job.sources_json or "[]")
    flags = json.loads(job.flags_json or "[]")
    return {"job": job, "link": link, "pay": pay_label(pay), "flags": flags,
            "warn_flags": [f for f in flags if f["key"] not in POSITIVE], "eligible": eligible_for_high_schooler(flags),
            "hs_ok": open_to_high_school(flags),
            "app_status": app_status,
            "match": json.loads(link.match_json or "{}"), "sources": sources,
            "source_names": sorted({s["source"] for s in sources}),
            "new": search.last_run_id is not None and link.first_run_id == search.last_run_id}


def _results_ctx(search_id: int) -> dict:
    search = repo.search(search_id)
    statuses = apps.statuses_by_job()
    from storage.companies_repo import CompanyRepo
    from jobsearch.dedupe import norm_company
    sizes = CompanyRepo().size_map()
    rows = [_row_view(j, l, search, statuses.get(j.id)) for j, l in repo.results(search_id)]
    for r in rows:
        info = sizes.get(norm_company(r["job"].company), {})
        r["size"], r["local"] = info.get("employees"), bool(info.get("local"))
    rows.sort(key=lambda r: (-r["link"].match_score, r["job"].company.lower()))
    return {"selected": search, "spec": repo.spec_of(search), "rows": rows,
            "counts": {"new": sum(r["new"] for r in rows), "saved": sum(r["job"].state == "saved" for r in rows),
                       "dismissed": sum(r["job"].state == "dismissed" for r in rows),
                       "pay": sum(bool(r["pay"]) for r in rows), "remote": sum(r["job"].work_mode == "remote" for r in rows),
                       "no_flags": sum(not r["warn_flags"] for r in rows),
                       "eligible": sum(r["eligible"] for r in rows), "hs_ok": sum(r["hs_ok"] for r in rows),
                       "small": sum(1 for r in rows if r.get("size") and r["size"] <= 100),
                       "local": sum(1 for r in rows if r.get("local"))},
            "companies": sorted({r["job"].company for r in rows}, key=str.lower),
            "last_run": job_runs.latest(search_id)}


@router.get("/searches/{search_id}/results", response_class=HTMLResponse)
def results(request: Request, search_id: int):
    if not repo.search(search_id):
        raise HTTPException(404)
    return templates.TemplateResponse(request, "partials/job_results.html", {**_common(), **_results_ctx(search_id)})


def finder_link(company: str) -> str:
    """Finder for this company, with Recruiting & HR and Engineering ticked. Uses the watchlist domain when known."""
    domain = next((c.domain for c in repo.watchlist() if c.domain and c.name.lower() == (company or "").lower()), None)
    return "/finder?" + urlencode({"company": domain or company, "depts": "recruiting,engineering"}) + "#company"


@router.get("/job/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int, search: Optional[int] = None):
    return _job_detail(request, job_id, search)


def _job_detail(request: Request, job_id: int, search: Optional[int] = None, **extra):
    job = repo.job(job_id)
    if not job:
        raise HTTPException(404)
    link = repo.link(search, job_id) if search else None
    s = repo.search(search) if search else None
    app = apps.for_job(job_id)
    status = app.status if app else None
    if link and s:
        view = _row_view(job, link, s, status)
    else:
        flags = json.loads(job.flags_json or "[]")
        view = {"job": job, "link": None, "pay": pay_label(Pay(**json.loads(job.pay_json)) if job.pay_json else None),
                "flags": flags, "warn_flags": [f for f in flags if f["key"] not in POSITIVE],
                "eligible": eligible_for_high_schooler(flags), "app_status": status, "match": {},
                "sources": json.loads(job.sources_json or "[]"), "new": False}
    return templates.TemplateResponse(request, "partials/job_detail.html",
                                      {**_common(), "v": view, "search_id": search, "finder_url": finder_link(job.company),
                                       "app": app, **extra})


@router.post("/job/{job_id}/state", response_class=HTMLResponse)
async def set_state(request: Request, job_id: int, search: Optional[int] = None):
    form = await request.form()
    state = form.get("state", "")
    if state not in ("", "saved", "dismissed"):
        raise HTTPException(400)
    app = apps.for_job(job_id)
    if app and state != "saved" and app.status not in REMOVABLE:
        return _job_detail(request, job_id, search, error=f"This job is in Applications as {STATUS_LABELS[app.status]}. "
                                                          "Withdraw it there instead.")
    if not repo.set_state(job_id, state):
        raise HTTPException(404)
    if state == "saved":
        apps.ensure(job_id)  # saving a job puts it in the Applications pipeline
    else:
        apps.unsave(job_id)
    resp = _job_detail(request, job_id, search)
    resp.headers["HX-Trigger"] = json.dumps({"jobStateChanged": {"id": job_id, "state": state}})
    return resp


# -- watchlist ------------------------------------------------------------------------------------

def _watchlist_ctx(**extra) -> dict:
    rows = repo.watchlist()
    return {"companies": rows, "feeds_count": sum(1 for r in rows if r.ats), "manual_count": sum(1 for r in rows if not r.ats),
            "ATS_TYPES": ATS_TYPES, **extra}


def _watchlist(request: Request, **extra):
    return templates.TemplateResponse(request, "partials/watchlist.html", {**_common(), **_watchlist_ctx(**extra)})


@router.get("/watchlist", response_class=HTMLResponse)
def watchlist(request: Request):
    return _watchlist(request)


@router.post("/watchlist", response_class=HTMLResponse)
async def add_company(request: Request):
    form = await request.form()
    query = (form.get("query") or "").strip()
    if not query:
        return _watchlist(request, error="Enter a company name, careers page or job board link.")
    client = FeedClient(job_settings)
    try:
        det = detect_ats(query, client, store=SqlStore())
    finally:
        client.close()
    repo.record_usage("feed_request", client.requests)
    name = det.company_name or (query if not ("/" in query or "." in query) else (det.domain or query).split(".")[0].title())
    if det.status == "found":
        dupe = next((c for c in repo.watchlist() if c.ats == det.ats and c.board == det.board), None)
        if dupe:
            return _watchlist(request, notice=f"{dupe.name} is already on the watchlist ({FEEDS[det.ats].label}: {det.board}).",
                              highlight=dupe.id)
        row = repo.add_company(name=name, ats=det.ats, board=det.board, domain=det.domain, careers_url=det.careers_url,
                               status="ok", job_count=det.jobs, detected_how=det.how, message=f"{det.jobs} open jobs",
                               last_checked=now_iso())
        return _watchlist(request, notice=f"Added {row.name}: {FEEDS[det.ats].label} board '{det.board}' with {det.jobs} open jobs ({det.how}).",
                          highlight=row.id)
    row = repo.add_company(name=name, domain=det.domain, careers_url=det.careers_url, status="no_feed",
                           message="No public feed detected: check manually or enter the board below",
                           last_checked=now_iso())
    return _watchlist(request, highlight=row.id, open_manual=row.id,
                      warning=f"No Greenhouse, Lever or Ashby board found for {query}. Tried: " + "; ".join(det.tried) +
                              ". It's on the list as \"check manually\"; if you know the board, enter it below.")


@router.post("/watchlist/test-all", response_class=HTMLResponse)
def test_all_feeds(request: Request):
    """One light request per company with a feed (about 10-20 seconds for the starter list)."""
    client = FeedClient(job_settings)
    ok = failed = 0
    try:
        for row in repo.watchlist():
            if not row.ats:
                continue
            probe = FEEDS[row.ats].probe(row.board, client)
            ok, failed = ok + probe.ok, failed + (not probe.ok)
            repo.update_company(row.id, status="ok" if probe.ok else "error",
                                job_count=probe.jobs if probe.ok else row.job_count, message=probe.message, last_checked=now_iso())
    finally:
        client.close()
    repo.record_usage("feed_request", client.requests)
    msg = f"Checked {ok + failed} feeds: {ok} working" + (f", {failed} failed (see below)" if failed else "") + "."
    return _watchlist(request, **({"warning": msg} if failed else {"notice": msg}))


@router.post("/watchlist/{company_id}/test", response_class=HTMLResponse)
def test_feed(request: Request, company_id: int):
    row = repo.company(company_id)
    if not row:
        raise HTTPException(404)
    if not row.ats:
        return _watchlist(request, highlight=row.id, open_manual=row.id,
                          warning=f"{row.name} has no feed to test. Enter a board below, or check their careers page by hand.")
    client = FeedClient(job_settings)
    try:
        probe = FEEDS[row.ats].probe(row.board, client)
    finally:
        client.close()
    repo.record_usage("feed_request", client.requests)
    repo.update_company(row.id, status="ok" if probe.ok else "error", job_count=probe.jobs if probe.ok else row.job_count,
                        message=probe.message, last_checked=now_iso())
    msg = f"{row.name}: feed works, {probe.jobs} open jobs." if probe.ok else f"{row.name}: feed failed ({probe.message})."
    return _watchlist(request, highlight=row.id, **({"notice": msg} if probe.ok else {"warning": msg}))


@router.post("/watchlist/{company_id}/manual", response_class=HTMLResponse)
async def set_manual(request: Request, company_id: int):
    row = repo.company(company_id)
    if not row:
        raise HTTPException(404)
    form = await request.form()
    ats, board = form.get("ats") or "", (form.get("board") or "").strip()
    found = boards_in(board)  # a pasted board link works too
    if found:
        ats, board = found[0]
    if ats not in FEEDS or not board:
        if ats == "none":
            repo.update_company(row.id, ats=None, board=None, status="no_feed", job_count=None,
                                message="Marked as no public feed: check manually", detected_how="set by you")
            return _watchlist(request, highlight=row.id, notice=f"{row.name} is now \"check manually\".")
        return _watchlist(request, highlight=row.id, open_manual=row.id, warning="Pick a job board and enter the board name.")
    client = FeedClient(job_settings)
    try:
        probe = FEEDS[ats].probe(board, client)
    finally:
        client.close()
    repo.update_company(row.id, ats=ats, board=board, detected_how="entered by you",
                        status="ok" if probe.ok else "error", job_count=probe.jobs if probe.ok else None,
                        message=probe.message, last_checked=now_iso())
    if probe.ok:
        return _watchlist(request, highlight=row.id, notice=f"{row.name}: {FEEDS[ats].label} board '{board}' works, {probe.jobs} open jobs.")
    return _watchlist(request, highlight=row.id, open_manual=row.id,
                      warning=f"Saved, but {FEEDS[ats].label} board '{board}' didn't respond ({probe.message}). Check the spelling.")


@router.post("/watchlist/{company_id}/delete", response_class=HTMLResponse)
def delete_company(request: Request, company_id: int):
    row = repo.company(company_id)
    repo.delete_company(company_id)
    return _watchlist(request, notice=f"Removed {row.name}." if row else None)
