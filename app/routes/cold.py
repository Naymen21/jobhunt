"""Outreach > Find companies: small companies to cold-pitch with no posting. You choose the company and the person;
/draft-outreach writes the email (cold mode); it sends only after you approve it, under the same limits as all outreach."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from outreach import rules
from outreach.cold import SOURCE_LABELS, ColdFilters, filter_companies
from storage.companies_repo import CompanyRepo
from storage.outreach_repo import OutreachRepo

from ..autopilot_run import load_settings, save_settings
from ..templating import templates

router = APIRouter(prefix="/outreach/companies")
companies, out = CompanyRepo(), OutreachRepo()
SHOW_MAX = 120


def saved_filters() -> ColdFilters:
    return ColdFilters.from_dict(load_settings().cold_filters)


def list_ctx(f: ColdFilters) -> dict:
    found = filter_companies(companies.cold_rows(), f)
    return {"filters": f, "rows": found[:SHOW_MAX], "total": len(found), "SOURCE_LABELS": SOURCE_LABELS,
            "cold_per_day": load_settings().cold_per_day}


def _form_filters(form) -> ColdFilters:
    def num(name, default):
        v = str(form.get(name) or "").strip()
        return int(v) if v.isdigit() else default
    return ColdFilters.from_dict({
        "min_employees": num("min_employees", 10), "max_employees": num("max_employees", 60),
        "local_only": form.get("local_only") == "on", "keywords": str(form.get("keywords") or "")[:300],
        "location": str(form.get("location") or "")[:80], "preferred_only": form.get("preferred_only") == "on",
        "source": str(form.get("source") or "") if form.get("source") in SOURCE_LABELS else "",
        "include_unknown_size": form.get("include_unknown_size") == "on", "hide_contacted": form.get("hide_contacted") == "on",
        "show_hidden": form.get("show_hidden") == "on"})


@router.post("/list", response_class=HTMLResponse)
async def company_list(request: Request):
    """Filters are remembered (and used by the autopilot when 'cold pitches per day' is above 0)."""
    f = _form_filters(await request.form())
    s = load_settings()
    s.cold_filters = f.to_dict()
    save_settings(s)
    return templates.TemplateResponse(request, "partials/cold_list.html", list_ctx(f))


@router.post("/add", response_class=HTMLResponse)
async def add_company(request: Request):
    form = await request.form()
    try:
        row = companies.add_manual(str(form.get("name", "")), str(form.get("website", "")))
    except ValueError as e:
        return HTMLResponse(f'<div class="inline-error">{e}</div>')
    return _detail(request, row.key, notice=f"Added {row.name}. Its size is checked on the next autopilot run.")


def _detail(request: Request, key: str, **extra):
    from ..jobs import jobs as domain_searches
    row, domain = out.cold_company(key)
    if row is None:
        raise HTTPException(404)
    searching = bool(domain and domain_searches.active_for(domain))
    _, suggestions = out.cold_suggestions(key)
    picked = out.cold_contacts(key)
    taken = {c.email for c in picked} | {s.email for s in suggestions} | out.do_not_contact()
    everyone = sorted([p for p in out.people(domain) if p["email"] not in taken],
                      key=lambda p: (p["kind"] != "personal", -p["confidence"], p["email"])) if domain and not searching else []
    other = [c for c in out.contacts() if c.mode != "cold" and c.company == row.name]
    ctx = {"active": "outreach", "co": row, "key": key, "domain": domain, "searching": searching,
           "suggestions": [] if searching else suggestions, "everyone": everyone, "picked": picked, "other": other,
           "OUT_LABELS": rules.STATUS_LABELS, "SOURCE_LABELS": SOURCE_LABELS, "pasted": extra.pop("pasted", None), **extra}
    return templates.TemplateResponse(request, "partials/cold_company.html", ctx)


def _changed(resp):
    resp.headers["HX-Trigger"] = "outreachChanged"
    return resp


@router.get("/detail", response_class=HTMLResponse)
def detail(request: Request, k: str):
    return _detail(request, k)


@router.post("/find-people", response_class=HTMLResponse)
def find_people(request: Request, k: str):
    """The Finder's polite crawl of the company's own site (same as Finder > By company), in the background."""
    from finder.discovery import BadDomain
    from finder.resolve import resolve_company
    from storage.repo import SqlStore
    from ..jobs import jobs as domain_searches
    row, domain = out.cold_company(k)
    if row is None:
        raise HTTPException(404)
    if not domain:
        try:
            domain = resolve_company(row.website or row.name, SqlStore()).domain
        except BadDomain as e:
            return _detail(request, k, error=str(e))
        if not domain:
            return _detail(request, k, error=f"Couldn't work out {row.name}'s website. Add it with Add a company (name + website).")
    domain_searches.start(domain, refresh=False, github_org=None)
    return _detail(request, k)


@router.post("/pick", response_class=HTMLResponse)
async def pick(request: Request, k: str):
    form = await request.form()
    try:
        out.pick_cold(k, str(form.get("email", "")), name=str(form.get("name", "")), title=str(form.get("title", "")),
                      department=str(form.get("department", "")), confidence=int(form.get("confidence") or 0),
                      evidence=str(form.get("evidence") or ""))
    except ValueError as e:
        return _detail(request, k, error=str(e))
    return _changed(_detail(request, k))


@router.post("/paste", response_class=HTMLResponse)
async def paste(request: Request, k: str):
    """Only parses the text you pasted. Never connects to LinkedIn."""
    form = await request.form()
    people = out.paste_people_cold(k, str(form.get("text", "")))
    msg = None if people else "No names and titles found in that text. Copy the list of people (name + title lines) and try again."
    return _detail(request, k, pasted=people, error=msg)


@router.post("/dismiss", response_class=HTMLResponse)
def dismiss(request: Request, k: str):
    companies.set_cold_status(k, "dismissed")
    resp = HTMLResponse('<div class="drawer-head"><h2 class="drawer-title">Hidden</h2>'
                        '<button class="btn btn-quiet drawer-close" type="button" data-close-drawer>Close</button></div>'
                        '<p class="muted small">It won\'t show in Find companies again. Undo: tick "Show hidden" in the filters.</p>')
    resp.headers["HX-Trigger"] = "coldChanged"
    return resp


@router.post("/restore", response_class=HTMLResponse)
def restore(request: Request, k: str):
    companies.set_cold_status(k, "")
    resp = _detail(request, k, notice="Back in the list.")
    resp.headers["HX-Trigger"] = "coldChanged"
    return resp
