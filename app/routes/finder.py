"""Email Finder page. Thin layer: calls the finder engine, stores results, renders templates."""
from __future__ import annotations

import csv
import io
from collections import Counter

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlmodel import Session, select

from finder import verify_email
from finder.bulk import parse_table
from finder.config import settings
from finder.discovery import BadDomain, normalize_domain
from finder.find import BadName, find_email
from finder.names import parse_name
from finder.patterns import match_templates
from finder.resolve import confirm_company, resolve_company
from finder.titles import DEPARTMENT_LABELS, DEPARTMENTS, SENIORITIES
from storage.db import get_session
from storage.repo import SqlStore
from storage.tables import Verification

from ..jobs import bulk_jobs, jobs
from ..templating import templates

router = APIRouter(prefix="/finder")

HISTORY_LIMIT = 100


def _history(session: Session) -> list[Verification]:
    stmt = select(Verification).order_by(Verification.created_at.desc()).limit(HISTORY_LIMIT)
    return list(session.exec(stmt))


def _verify_and_store(email: str, session: Session) -> Verification:
    # Sync def routes run in FastAPI's threadpool, so the rate limiter's sleep
    # blocks only this request, not the server.
    row = Verification.from_result(verify_email(email))
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.get("", response_class=HTMLResponse)
def page(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(request, "finder.html", {
        "active": "finder",
        "history": _history(session),
        "smtp_transport": settings.smtp_transport,
        "known_domains": SqlStore().list_domains(12),
        "max_pages": settings.crawl_max_pages,
        "github_token": bool(settings.github_token),
        "min_interval": settings.smtp_min_interval,
    })


@router.post("/verify", response_class=HTMLResponse)
def verify_single(request: Request, email: str = Form(...), session: Session = Depends(get_session)):
    row = _verify_and_store(email, session)
    response = templates.TemplateResponse(request, "partials/result_card.html", {"r": row.result()})
    response.headers["HX-Trigger"] = "historyChanged"
    return response


@router.post("/verify-row", response_class=HTMLResponse)
def verify_row(request: Request, email: str = Form(...), session: Session = Depends(get_session)):
    """Used by bulk mode: verifies one address and returns a table row."""
    row = _verify_and_store(email, session)
    return templates.TemplateResponse(request, "partials/bulk_row.html", {"v": row, "r": row.result()})


@router.get("/history", response_class=HTMLResponse)
def history(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(request, "partials/history_rows.html", {"history": _history(session)})


@router.get("/history/{vid}", response_class=HTMLResponse)
def history_item(vid: int, request: Request, session: Session = Depends(get_session)):
    row = session.get(Verification, vid)
    if row is None:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "partials/result_card.html", {"r": row.result(), "from_history": True})


@router.post("/history/clear", response_class=HTMLResponse)
def clear_history(request: Request, session: Session = Depends(get_session)):
    for row in session.exec(select(Verification)):
        session.delete(row)
    session.commit()
    return templates.TemplateResponse(request, "partials/history_rows.html", {"history": []})


# --- Company name -> domain ----------------------------------------------------------

def _pick_domain(request: Request, company: str, action: str, extra: dict, company_query: str = ""):
    """Return (domain, None), or (None, response) when the owner has to choose or nothing matched.
    `company_query` is set when the owner just clicked a candidate in the picker."""
    store = SqlStore()
    if company_query:
        return confirm_company(company_query, company, store).domain, None
    try:
        res = resolve_company(company, store)
    except BadDomain as e:
        return None, _error(request, str(e))
    if res.domain:
        return res.domain, None
    return None, templates.TemplateResponse(request, "partials/company_picker.html",
                                            {"res": res, "action": action, "extra": extra})


# --- Domain Search -------------------------------------------------------------------

def _results(request: Request, domain: str, cached: bool = False):
    report = SqlStore().load_report(domain)
    if report is None:
        raise HTTPException(404, f"{domain} has not been searched")
    rows: dict[str, dict] = {}  # one row per address, with every source that mentioned it
    for e in report.emails:
        row = rows.setdefault(e.email, {"e": e, "sources": [], "last_seen": e.last_seen})
        row["sources"].append(e)
        if e.full_name and not row["e"].full_name:
            row["e"] = e
        if e.title and e.title != "Code contributor" and (not row["e"].title or row["e"].title == "Code contributor"):
            row["e"].title, row["e"].department, row["e"].seniority = e.title, e.department, e.seniority
        elif e.department and not row["e"].department:
            row["e"].department = e.department
        if e.last_seen and (not row["last_seen"] or e.last_seen > row["last_seen"]):
            row["last_seen"] = e.last_seen
    for row in rows.values():
        row["stale"] = bool(row["last_seen"]) and row["last_seen"] < report.searched_at
    ordered = sorted(rows.values(), key=lambda r: (r["e"].kind != "personal", r["stale"], r["e"].full_name is None,
                                                   r["e"].email))
    personal = [r for r in ordered if r["e"].kind == "personal"]
    dept_counts = Counter(r["e"].department or "unknown" for r in personal)
    seniority_counts = Counter(r["e"].seniority or "unknown" for r in personal)
    return templates.TemplateResponse(request, "partials/domain_results.html", {
        "report": report, "rows": ordered, "cached": cached,
        "personal_count": len(personal), "role_count": len(ordered) - len(personal),
        "stale_count": sum(1 for r in ordered if r["stale"]),
        "dept_counts": [(d, dept_counts[d]) for d in DEPARTMENTS + ["unknown"] if dept_counts[d]],
        "seniority_counts": [(s, seniority_counts[s]) for s in SENIORITIES + ["unknown"] if seniority_counts[s]],
        "faces": [r["e"].full_name for r in personal if r["e"].full_name][:4],
    })


def _error(request: Request, message: str):
    return templates.TemplateResponse(request, "partials/inline_error.html", {"message": message})


@router.post("/domain-search", response_class=HTMLResponse)
def domain_search(request: Request, domain: str = Form(...), github_org: str = Form(""),
                  refresh: bool = Form(False), company_query: str = Form("")):
    extra = {"github_org": github_org, **({"refresh": "true"} if refresh else {})}
    domain, picker = _pick_domain(request, domain, "/finder/domain-search", extra, company_query)
    if picker is not None:
        return picker
    if not refresh and SqlStore().load_report(domain) is not None and not jobs.active_for(domain):
        return _results(request, domain, cached=True)
    run_id = jobs.start(domain, refresh=refresh, github_org=github_org.strip() or None)
    return templates.TemplateResponse(request, "partials/domain_progress.html", {"run": jobs.get(run_id), "max_pages": settings.crawl_max_pages})


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_status(run_id: int, request: Request):
    run = jobs.get(run_id)
    if run is None:
        raise HTTPException(404)
    if run.status in ("queued", "running"):
        return templates.TemplateResponse(request, "partials/domain_progress.html", {"run": run, "max_pages": settings.crawl_max_pages})
    if run.status == "failed":
        return _error(request, f"Search for {run.domain} failed: {run.error}")
    response = _results(request, run.domain)
    response.headers["HX-Trigger"] = "domainsChanged"
    return response


@router.post("/runs/{run_id}/cancel", response_class=HTMLResponse)
def run_cancel(run_id: int, request: Request):
    jobs.cancel(run_id)
    return run_status(run_id, request)


@router.get("/domains", response_class=HTMLResponse)
def domain_chips(request: Request):
    return templates.TemplateResponse(request, "partials/domain_chips.html", {"known_domains": SqlStore().list_domains(12)})


@router.get("/domains/{domain}", response_class=HTMLResponse)
def domain_cached(domain: str, request: Request):
    return _results(request, normalize_domain(domain), cached=True)


@router.get("/domains/{domain}/emails.csv")
def domain_csv(domain: str, kind: str = "all", dept: str = "", seniority: str = "", fresh: bool = False):
    domain = normalize_domain(domain)
    report = SqlStore().load_report(domain)
    if report is None:
        raise HTTPException(404)
    depts = {d for d in dept.split(",") if d}
    levels = {s for s in seniority.split(",") if s}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["email", "name", "title", "department", "seniority", "type", "source_type", "source_url",
                "first_seen", "last_seen", "pattern_match"])
    for e in sorted(report.emails, key=lambda e: (e.kind, e.email)):
        if kind in ("personal", "role") and e.kind != kind:
            continue
        if depts and (e.department or "unknown") not in depts:
            continue
        if levels and (e.seniority or "unknown") not in levels:
            continue
        if fresh and report.is_stale(e.email):
            continue
        parsed = parse_name(e.full_name) if e.full_name else None
        fits = ",".join(match_templates(parsed, e.email.split("@")[0])) if parsed else ""
        w.writerow([e.email, e.full_name or "", e.title or "", DEPARTMENT_LABELS.get(e.department or "", ""),
                    e.seniority or "", e.kind, e.source_type, e.source_url, e.found_at, e.last_seen or "", fits])
    suffix = "" if kind == "all" else f"-{kind}"
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{domain}{suffix}-emails.csv"'})


# --- Find Email ------------------------------------------------------------------------

@router.post("/find", response_class=HTMLResponse)
def find(request: Request, name: str = Form(...), domain: str = Form(...), company_query: str = Form("")):
    domain, picker = _pick_domain(request, domain, "/finder/find", {"name": name}, company_query)
    if picker is not None:
        return picker
    try:
        result = find_email(name, domain, SqlStore())
    except (BadName, BadDomain) as e:
        return _error(request, str(e))
    return templates.TemplateResponse(request, "partials/find_result.html", {"r": result})


# --- Bulk find ---------------------------------------------------------------------------

@router.post("/bulk-find/preview", response_class=HTMLResponse)
def bulk_find_preview(request: Request, table: str = Form("")):
    parsed = parse_table(table)
    return templates.TemplateResponse(request, "partials/bulk_find_preview.html", {"p": parsed})


@router.post("/bulk-find", response_class=HTMLResponse)
def bulk_find_start(request: Request, table: str = Form(""), search_unsearched: bool = Form(False)):
    parsed = parse_table(table)
    if parsed.error:
        return _error(request, parsed.error)
    run_id = bulk_jobs.start(parsed.rows, search_unsearched)
    return bulk_find_status(run_id, request)


@router.get("/bulk-find/{run_id}", response_class=HTMLResponse)
def bulk_find_status(run_id: int, request: Request):
    run, rows = bulk_jobs.get(run_id)
    if run is None:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "partials/bulk_find_run.html", {"run": run, "rows": rows})


@router.post("/bulk-find/{run_id}/cancel", response_class=HTMLResponse)
def bulk_find_cancel(run_id: int, request: Request):
    bulk_jobs.cancel(run_id)
    return bulk_find_status(run_id, request)


@router.get("/bulk-find/{run_id}/results.csv")
def bulk_find_csv(run_id: int):
    run, rows = bulk_jobs.get(run_id)
    if run is None:
        raise HTTPException(404)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "company", "domain", "email", "score", "evidence", "status", "notes"])
    for r in rows:
        w.writerow([r.input_name, r.input_company, r.domain or "", r.email or "", "" if r.score is None else r.score,
                    r.evidence or "", r.status, r.why])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="bulk-find-{run_id}.csv"'})
