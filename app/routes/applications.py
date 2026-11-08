"""Applications: the pipeline board, the detail panel, cover letter editing and export.
Drafts come from Claude Code commands; nothing here calls AI or submits anything."""
from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from applications.export import Letterhead, file_name, to_docx, to_pdf
from applications.status import COLUMNS, NEXT, STATUS_LABELS, StatusError, follow_up_due
from applications.validate import words
from jobsearch.models import JOB_TYPE_LABELS, WORK_MODE_LABELS, Pay
from jobsearch.normalize import pay_label
from storage.apps_repo import AppRepo

from ..templating import templates

router = APIRouter(prefix="/applications")
repo = AppRepo()


def _card(app, job, best: dict, today: date) -> dict:
    flags = json.loads(job.flags_json or "[]")
    return {"app": app, "job": job, "flags": flags, "basic": best.get(job.id), "due": follow_up_due(app.follow_up_at, app.status, today)}


def _board_ctx() -> dict:
    today = date.today()
    best = repo.best_basic()
    from storage.outreach_repo import OutreachRepo
    out = OutreachRepo()
    referred = out.referred_apps()
    cards = [_card(a, j, best, today) for a, j in repo.board()]
    for c in cards:
        c["referred"] = c["app"].id in referred
        c["apply_by"] = out.apply_by(c["app"].id) if c["app"].status in ("saved", "drafts_ready", "approved") else None
        c["pitch"] = any(f["key"] == "pitch" for f in c["flags"])
    columns = [{"key": key, "label": label, "cards": [c for c in cards if c["app"].status in statuses]}
               for key, label, statuses in COLUMNS]
    counts = {k: sum(1 for c in cards if c["app"].status == k) for k in STATUS_LABELS}
    return {"columns": columns, "summary": repo.summary(today), "counts": counts, "total": len(cards)}


def _common() -> dict:
    return {"active": "applications", "STATUS_LABELS": STATUS_LABELS, "WORK_MODE_LABELS": WORK_MODE_LABELS,
            "JOB_TYPE_LABELS": JOB_TYPE_LABELS}


@router.get("", response_class=HTMLResponse)
def page(request: Request):
    return templates.TemplateResponse(request, "applications.html", {**_common(), **_board_ctx()})


@router.get("/board", response_class=HTMLResponse)
def board(request: Request):
    return templates.TemplateResponse(request, "partials/app_board.html", {**_common(), **_board_ctx()})


def _detail(request: Request, app_id: int, **extra):
    app = repo.get(app_id)
    if not app:
        raise HTTPException(404)
    job = repo.jobs.job(app.job_id)
    pay = Pay(**json.loads(job.pay_json)) if job.pay_json else None
    basic = repo.best_basic().get(job.id)
    ctx = {**_common(), "a": app, "j": job, "pay": pay_label(pay), "flags": json.loads(job.flags_json or "[]"),
           "answers": json.loads(app.answers_json or "[]"), "events": repo.events(app.id), "basic": basic,
           "table": json.loads(app.cover_table_json) if app.cover_table_json else None,
           "next": NEXT.get(app.status, []), "words": words(app.cover_letter),
           "due": follow_up_due(app.follow_up_at, app.status), "history": json.loads(app.drafts_history_json or "[]"),
           "pdf_name": file_name(repo.jobs.profile().name, job.company, "pdf"),
           "docx_name": file_name(repo.jobs.profile().name, job.company, "docx"), **extra}
    return templates.TemplateResponse(request, "partials/app_detail.html", ctx)


@router.get("/{app_id}", response_class=HTMLResponse)
def detail(request: Request, app_id: int):
    return _detail(request, app_id)


@router.post("/{app_id}/move", response_class=HTMLResponse)
async def move(request: Request, app_id: int):
    form = await request.form()
    try:
        repo.move(app_id, str(form.get("status", "")), note=str(form.get("note", "")))
    except StatusError as e:
        return _detail(request, app_id, error=str(e))
    resp = _detail(request, app_id)
    resp.headers["HX-Trigger"] = "appsChanged"
    return resp


@router.post("/{app_id}/cover", response_class=HTMLResponse)
async def save_cover(request: Request, app_id: int):
    form = await request.form()
    app = repo.update(app_id, cover_letter=str(form.get("cover_letter", "")))
    n = words(app.cover_letter)
    label = "Edited by you" if app.cover_edited else ("AI draft, unedited" if app.cover_letter_ai else "Saved")
    return HTMLResponse(f'<span class="saved-note">Saved</span> · <span class="{"pop-text" if app.cover_edited else "muted"}">'
                        f'{label}</span> · <span class="muted">{n} words</span>')


@router.post("/{app_id}/notes", response_class=HTMLResponse)
async def save_notes(request: Request, app_id: int):
    form = await request.form()
    repo.update(app_id, notes=str(form.get("notes", "")))
    return HTMLResponse('<span class="saved-note">Saved</span>')


@router.post("/{app_id}/follow-up", response_class=HTMLResponse)
async def follow_up(request: Request, app_id: int):
    form = await request.form()
    try:
        if form.get("done"):
            repo.update(app_id, clear_follow_up=True)
        else:
            repo.update(app_id, follow_up_at=str(form.get("follow_up_at", "")))
    except ValueError:
        return _detail(request, app_id, error="That isn't a valid date.")
    resp = _detail(request, app_id)
    resp.headers["HX-Trigger"] = "appsChanged"
    return resp


def _export(app_id: int, kind: str, fmt: str = "table") -> Response:
    app = repo.get(app_id)
    table = json.loads(app.cover_table_json) if app and app.cover_table_json and fmt != "plain" else None
    if not app or not (app.cover_letter.strip() or table):
        raise HTTPException(404, "No cover letter to export yet.")
    job = repo.jobs.job(app.job_id)
    p = repo.jobs.profile()
    head = Letterhead(p.name or "Your Name", p.email, p.phone, p.location, p.linkedin_url.replace("https://", "") if p.linkedin_url else "")
    if kind == "pdf":
        data, mime = to_pdf(app.cover_letter, head, table=table), "application/pdf"
    else:
        data, mime = to_docx(app.cover_letter, head, table=table), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    name = file_name(p.name, job.company, kind)
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/{app_id}/cover-letter.pdf")
def export_pdf(app_id: int, format: str = "table"):
    return _export(app_id, "pdf", format)


@router.get("/{app_id}/cover-letter.docx")
def export_docx(app_id: int, format: str = "table"):
    return _export(app_id, "docx", format)


@router.post("/{app_id}/cover-table", response_class=HTMLResponse)
async def save_table(request: Request, app_id: int):
    form = await request.form()
    rows = [{"need": n.strip(), "bring": b.strip()} for n, b in zip(form.getlist("need"), form.getlist("bring"))]
    app = repo.update_table(app_id, {"opening": str(form.get("opening", "")).strip(), "rows": rows,
                                     "closing": str(form.get("closing", "")).strip()})
    label = '<span class="pop-text">Edited by you</span>' if app.table_edited else '<span class="muted">AI draft, unedited</span>'
    return HTMLResponse(f'<span class="saved-note">Saved</span> · {label}')


# -- outreach section inside an application ------------------------------------------------------------

def _outreach(request: Request, app_id: int, **extra):
    from outreach.rules import STATUS_LABELS as OUT_LABELS
    from storage.outreach_repo import OutreachRepo
    from ..jobs import jobs as domain_searches
    out = OutreachRepo()
    app = repo.get(app_id)
    job = repo.jobs.job(app.job_id)
    domain, suggestions = out.suggestions(app_id)
    searching = bool(domain and domain_searches.active_for(domain))
    taken = {c.email for c in out.contacts(app_id)} | {s.email for s in suggestions} | out.do_not_contact()
    everyone = sorted([p for p in out.people(domain) if p["email"] not in taken], key=lambda p: (p["kind"] != "personal", -p["confidence"], p["email"])) if domain and not searching else []
    ctx = {"a": app, "company": job.company, "domain": domain, "suggestions": [] if searching else suggestions, "everyone": everyone,
           "picked": out.contacts(app_id), "prefs": out.prefs(job.company), "apply_by": out.apply_by(app_id),
           "searching": searching, "OUT_LABELS": OUT_LABELS, "pasted": extra.pop("pasted", None), **extra}
    return templates.TemplateResponse(request, "partials/app_outreach.html", ctx)


@router.get("/{app_id}/outreach", response_class=HTMLResponse)
def outreach_section(request: Request, app_id: int):
    return _outreach(request, app_id)


@router.post("/{app_id}/pick", response_class=HTMLResponse)
async def pick_contact(request: Request, app_id: int):
    from storage.outreach_repo import OutreachRepo
    form = await request.form()
    try:
        OutreachRepo().pick(app_id, str(form.get("email", "")))
    except ValueError as e:
        return _outreach(request, app_id, error=str(e))
    return _outreach(request, app_id)


@router.post("/{app_id}/prefs", response_class=HTMLResponse)
async def company_prefs(request: Request, app_id: int):
    from storage.outreach_repo import OutreachRepo
    form = await request.form()
    job = repo.jobs.job(repo.get(app_id).job_id)
    OutreachRepo().set_prefs(job.company, reach_out_first=form.get("reach_out_first") == "on",
                             small_startup=form.get("small_startup") == "on", ok_unpaid=form.get("ok_unpaid") == "on")
    resp = _outreach(request, app_id)
    resp.headers["HX-Trigger"] = "appsChanged"
    return resp


@router.post("/{app_id}/find-people", response_class=HTMLResponse)
def find_people(request: Request, app_id: int):
    """Runs the Finder's domain search for this company in the background (same as Finder > By company)."""
    from finder.discovery import BadDomain
    from finder.resolve import resolve_company
    from storage.repo import SqlStore
    from ..jobs import jobs as domain_searches
    job = repo.jobs.job(repo.get(app_id).job_id)
    from storage.outreach_repo import OutreachRepo
    domain = OutreachRepo().company_domain(job.company)
    if not domain:
        try:
            res = resolve_company(job.company, SqlStore())
        except BadDomain as e:
            return _outreach(request, app_id, error=str(e))
        domain = res.domain
        if not domain:
            return _outreach(request, app_id, error=f"Couldn't work out {job.company}'s website. Search it in the Finder by domain.")
    domain_searches.start(domain, refresh=False, github_org=None)
    return _outreach(request, app_id)



@router.post("/{app_id}/paste-people", response_class=HTMLResponse)
async def paste_people(request: Request, app_id: int):
    """Only parses the text you pasted. Never connects to LinkedIn."""
    from storage.outreach_repo import OutreachRepo
    form = await request.form()
    people = OutreachRepo().paste_people(app_id, str(form.get("text", "")))
    msg = None if people else "No names and titles found in that text. Copy the list of people (name + title lines) and try again."
    return _outreach(request, app_id, pasted=people, error=msg)


@router.post("/{app_id}/add-person", response_class=HTMLResponse)
async def add_person(request: Request, app_id: int):
    from storage.outreach_repo import OutreachRepo
    form = await request.form()
    try:
        OutreachRepo().pick_manual(app_id, str(form.get("email", "")), name=str(form.get("name", "")),
                                   title=str(form.get("title", "")), department=str(form.get("department", "")),
                                   confidence=int(form.get("confidence") or 0), evidence=str(form.get("evidence") or "typed"))
    except ValueError as e:
        return _outreach(request, app_id, error=str(e))
    resp = _outreach(request, app_id)
    resp.headers["HX-Trigger"] = "appsChanged"
    return resp
