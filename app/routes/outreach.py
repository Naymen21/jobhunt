"""Outreach: contacts per application, drafts you review, the send queue, replies. Nothing sends without approval;
sending happens in the background worker under the CLAUDE.md caps."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from applications.status import STATUS_LABELS as APP_STATUS_LABELS
from outreach import rules
from outreach.mail import GmailConfig
from storage.apps_repo import AppRepo
from storage.outreach_repo import OutreachRepo

from ..templating import templates

router = APIRouter(prefix="/outreach")
repo = OutreachRepo()
apps = AppRepo()


def _common() -> dict:
    return {"active": "outreach", "OUT_LABELS": rules.STATUS_LABELS, "APP_STATUS_LABELS": APP_STATUS_LABELS}


def _board_ctx() -> dict:
    contacts = repo.contacts()
    jobs = {a.id: (a, j) for a, j in apps.board()}
    cards = []
    for c in contacts:
        app, job = jobs.get(c.application_id, (None, None))
        cards.append({"c": c, "app": app, "job": job,
                      "queue_ok": rules.queueable(c.confidence, c.kind, c.checked_ok)[0]})
    needs_me = [x for x in cards if x["c"].status == "replied"]
    columns = [{"key": k, "label": label, "cards": [x for x in cards if x["c"].status in sts]}
               for k, label, sts in rules.COLUMNS]
    return {"columns": columns, "needs_me": needs_me, "today": repo.today(), "total": len(cards),
            "test": repo.test_status(), "gmail": GmailConfig()}


@router.get("", response_class=HTMLResponse)
def page(request: Request, view: str = "people"):
    if view == "companies":
        from .cold import list_ctx, saved_filters
        return templates.TemplateResponse(request, "outreach.html", {**_common(), "view": "companies",
                                                                     **list_ctx(saved_filters())})
    return templates.TemplateResponse(request, "outreach.html", {**_common(), "view": "people", **_board_ctx()})


@router.get("/board", response_class=HTMLResponse)
def board(request: Request):
    return templates.TemplateResponse(request, "partials/out_board.html", {**_common(), **_board_ctx()})


def _detail(request: Request, contact_id: int, **extra):
    c = repo.contact(contact_id)
    if not c:
        raise HTTPException(404)
    app = apps.get(c.application_id)
    job = apps.jobs.job(app.job_id) if app else None
    ok, why = rules.queueable(c.confidence, c.kind, c.checked_ok)
    ctx = {**_common(), "c": c, "app": app, "job": job, "queue_ok": ok, "queue_why": why, "events": repo.events(c.id),
           "outbox": repo.outbox(c.id), "history": json.loads(c.history_json or "[]"), "signature": repo.signature(),
           "prefs": repo.prefs(c.company), **extra}
    return templates.TemplateResponse(request, "partials/out_detail.html", ctx)


@router.get("/{contact_id}", response_class=HTMLResponse)
def detail(request: Request, contact_id: int):
    return _detail(request, contact_id)


def _changed(resp):
    resp.headers["HX-Trigger"] = "outreachChanged"
    return resp


@router.post("/{contact_id}/edit", response_class=HTMLResponse)
async def edit(request: Request, contact_id: int):
    form = await request.form()
    fields = {k: str(form[k]) for k in ("subject", "body", "followup1", "followup2") if k in form}
    c = repo.update_drafts(contact_id, **fields)
    label = '<span class="pop-text">Edited by you</span>' if c.edited else '<span class="muted">AI draft, unedited</span>'
    return HTMLResponse(f'<span class="saved-note">Saved</span> · {label}')


@router.post("/{contact_id}/approve", response_class=HTMLResponse)
def approve(request: Request, contact_id: int):
    try:
        repo.approve(contact_id)
    except ValueError as e:
        return _detail(request, contact_id, error=str(e))
    return _changed(_detail(request, contact_id))


@router.post("/{contact_id}/confirm", response_class=HTMLResponse)
def confirm(request: Request, contact_id: int):
    repo.set_checked(contact_id, True)
    return _changed(_detail(request, contact_id))


@router.post("/{contact_id}/status", response_class=HTMLResponse)
async def status(request: Request, contact_id: int):
    form = await request.form()
    new = str(form.get("status", ""))
    if new not in ("replied", "call_scheduled", "referred", "no_response", "do_not_contact"):
        raise HTTPException(400)
    if new == "do_not_contact":
        repo.add_dnc(repo.contact(contact_id).email, "marked by you")
    else:
        repo.set_status(contact_id, new)
    return _changed(_detail(request, contact_id))


@router.post("/{contact_id}/remove", response_class=HTMLResponse)
def remove(request: Request, contact_id: int):
    c = repo.contact(contact_id)
    if not repo.unpick(contact_id):
        return _detail(request, contact_id, error="Only contacts that haven't been approved can be removed.")
    resp = HTMLResponse(f'<div class="empty-list">Removed {c.name or c.email}.</div>')
    return _changed(resp)


@router.post("/pause", response_class=HTMLResponse)
def pause_toggle(request: Request):
    if repo.paused():
        repo.resume()
    else:
        repo.pause("Paused by you.")
    return _changed(board(request))


@router.post("/test-email", response_class=HTMLResponse)
def test_email(request: Request):
    if not GmailConfig().configured:
        return HTMLResponse('<div class="inline-error">Gmail isn\'t set up yet: add GMAIL_ADDRESS and GMAIL_APP_PASSWORD to .env (see Settings → Keys).</div>')
    o = repo.send_test()
    if o.status == "sent":
        return HTMLResponse(f'<div class="notice">Sent a test email to {o.to_addr}. Reply to it from Gmail, then press '
                            f'<strong>Check replies now</strong>.</div>')
    return HTMLResponse(f'<div class="inline-error">Couldn\'t send: {o.error or "unknown error"}</div>')


@router.post("/check-now", response_class=HTMLResponse)
def check_now(request: Request):
    if not GmailConfig().configured:
        return HTMLResponse('<div class="inline-error">Gmail isn\'t set up yet.</div>')
    from outreach.mail import Inbox
    try:
        n = repo.check_replies(Inbox(GmailConfig()))
    except Exception as e:
        return HTMLResponse(f'<div class="inline-error">Couldn\'t read the inbox: {type(e).__name__}: {e}</div>')
    t = repo.test_status()
    extra = " The reply to your test email was detected." if t["reply_seen"] else ""
    resp = HTMLResponse(f'<div class="notice">Checked: {n} new repl{"y" if n == 1 else "ies"} to outreach threads.{extra}</div>')
    return _changed(resp)
