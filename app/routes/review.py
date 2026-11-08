"""Review: the morning page. One card per job the autopilot prepared, best first. You approve, copy, submit.
Nothing is sent or submitted without your click."""
from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from applications.status import STATUS_LABELS, StatusError, follow_up_due
from applications.validate import words
from autopilot.rules import review_priority
from jobsearch.dedupe import norm_company
from jobsearch.flags import is_pitch
from jobsearch.models import JOB_TYPE_LABELS, WORK_MODE_LABELS, Pay
from jobsearch.normalize import pay_label
from outreach import rules as out_rules
from storage.apps_repo import AppRepo
from storage.companies_repo import CompanyRepo
from storage.outreach_repo import OutreachRepo

from ..autopilot_run import counts_today, last_run, load_settings, queue_redraft, redraft_queue
from ..templating import templates

router = APIRouter(prefix="/review")
apps, out, companies = AppRepo(), OutreachRepo(), CompanyRepo()
REVIEW_STATUSES = ("saved", "drafts_ready", "approved")


def _card(app, job, best: dict, sizes: dict) -> dict:
    flags = json.loads(job.flags_json or "[]")
    pitch = is_pitch(flags)
    info = sizes.get(norm_company(job.company), {})
    contacts = [c for c in out.contacts(app.id) if c.status not in ("do_not_contact",)]
    pay = Pay(**json.loads(job.pay_json)) if job.pay_json else None
    ready = (app.status == "drafts_ready") or any(c.status == "draft_ready" for c in contacts)
    return {"a": app, "j": job, "flags": flags, "pitch": pitch, "size": info.get("employees"), "local": info.get("local"),
            "size_source": info.get("source"), "pay": pay_label(pay), "basic": best.get(job.id),
            "table": json.loads(app.cover_table_json) if app.cover_table_json else None,
            "answers": json.loads(app.answers_json or "[]"), "words": words(app.cover_letter),
            "contacts": contacts, "ready": ready, "apply_by": out.apply_by(app.id),
            "queue_ok": {c.id: out_rules.queueable(c.confidence, c.kind, c.checked_ok) for c in contacts},
            "priority": review_priority(pitch, job.ai_score, best.get(job.id), None, app.needs_person)}


def _ctx() -> dict:
    best = apps.best_basic()
    sizes = companies.size_map()
    cards = [_card(a, j, best, sizes) for a, j in apps.board() if a.status in REVIEW_STATUSES]
    cards.sort(key=lambda c: c["priority"])
    contacts = out.contacts()
    today = out.today()
    run = last_run()
    pitch_apps = {c["a"].id for c in cards if c["pitch"]}
    strip = {
        "ready": sum(1 for c in cards if c["ready"]),
        "pitches_sent": sum(1 for c in contacts if c.sent_at and _is_pitch_app(c.application_id, pitch_apps)),
        "applied_week": apps.summary()["applied_this_week"],
        "sent_today": today["sent_today"], "cap": today["cap"], "replies": today["replies"],
        "follow_ups": apps.summary()["follow_ups_due"],
        "claude": run.claude_status if run else "", "claude_reset": run.claude_reset if run else "",
        "run_status": run.status if run else "", "run_at": run.started_at if run else "",
        "counts": counts_today(), "redrafts_queued": len(redraft_queue()),
    }
    cold = [_cold(c) for c in out.cold_contacts() if c.status in ("picked", "draft_ready")]
    strip["ready"] += sum(1 for x in cold if x["ct"].status == "draft_ready")
    return {"active": "review", "cards": cards, "cold": cold, "strip": strip, "settings": load_settings(), "STATUS_LABELS": STATUS_LABELS,
            "OUT_LABELS": out_rules.STATUS_LABELS, "WORK_MODE_LABELS": WORK_MODE_LABELS, "JOB_TYPE_LABELS": JOB_TYPE_LABELS}


def _cold(ct) -> dict:
    co, _ = out.cold_company(ct.company_key)
    return {"ct": ct, "co": co, "queue_ok": out_rules.queueable(ct.confidence, ct.kind, ct.checked_ok)}


def _is_pitch_app(app_id: int, pitch_apps: set) -> bool:
    if not app_id:
        return True  # a cold pitch (no posting) counts as a pitch
    if app_id in pitch_apps:
        return True
    a = apps.get(app_id)
    job = apps.jobs.job(a.job_id) if a else None
    return bool(job and is_pitch(json.loads(job.flags_json or "[]")))


@router.get("", response_class=HTMLResponse)
def page(request: Request):
    return templates.TemplateResponse(request, "review.html", _ctx())


def _one(request: Request, app_id: int, **extra):
    a = apps.get(app_id)
    if not a:
        raise HTTPException(404)
    if a.status not in REVIEW_STATUSES:
        msg = extra.get("notice") or f"Moved to {STATUS_LABELS[a.status]}."
        return HTMLResponse(f'<div class="card review-done">{msg} <a href="/applications">Applications</a> · '
                            f'<a href="/outreach">Outreach</a></div>')
    job = apps.jobs.job(a.job_id)
    card = _card(a, job, apps.best_basic(), companies.size_map())
    return templates.TemplateResponse(request, "partials/review_card.html", {**_ctx(), "c": card, **extra})


@router.post("/{app_id}/looks-good", response_class=HTMLResponse)
def looks_good(request: Request, app_id: int):
    """Approve the application and its outreach in one click. Pitch: approve the pitch emails only (they go first)."""
    a = apps.get(app_id)
    job = apps.jobs.job(a.job_id)
    pitch = is_pitch(json.loads(job.flags_json or "[]"))
    notes = []
    if not pitch and a.status in ("saved", "drafts_ready"):
        try:
            apps.move(app_id, "approved", note="Looks good (review page)")
            notes.append("application approved")
        except StatusError as e:
            notes.append(str(e))
    approved = 0
    for c in out.contacts(app_id):
        if c.status != "draft_ready":
            continue
        try:
            out.approve(c.id)
            approved += 1
        except ValueError as e:
            notes.append(f"{c.name or c.email}: {e}")
    if approved:
        notes.append(f"{approved} outreach email{'s' if approved != 1 else ''} approved"
                     + (" (pitch emails go out in the next sending window)" if pitch else " (they go out 1-2 days after you mark it applied)"))
    resp = _one(request, app_id, notice="; ".join(notes) or "Nothing to approve yet.")
    resp.headers["HX-Trigger"] = "reviewChanged"
    return resp


@router.post("/{app_id}/applied", response_class=HTMLResponse)
def applied(request: Request, app_id: int):
    try:
        apps.move(app_id, "applied", note="marked applied on the review page")
    except StatusError as e:
        return _one(request, app_id, error=str(e))
    n = out.schedule_ready()
    resp = _one(request, app_id, notice=f"Marked applied. Follow-up reminder set. {n} outreach email{'s' if n != 1 else ''} queued.")
    resp.headers["HX-Trigger"] = "reviewChanged"
    return resp


@router.post("/{app_id}/redraft", response_class=HTMLResponse)
async def redraft(request: Request, app_id: int):
    form = await request.form()
    note = str(form.get("note", "")).strip()
    target = str(form.get("target", "app"))
    if not note:
        return _one(request, app_id, error="Write what to change first.")
    if target.startswith("contact-"):
        queue_redraft("contact", int(target.split("-")[1]), note)
    else:
        queue_redraft("app", apps.get(app_id).job_id, note)
    return _one(request, app_id, notice="Redraft queued: the next autopilot run (or catch-up) rewrites it with your note.")


@router.post("/{app_id}/skip", response_class=HTMLResponse)
def skip(request: Request, app_id: int):
    a = apps.get(app_id)
    job_id = a.job_id
    for c in out.contacts(app_id):
        out.unpick(c.id)
    removed = apps.unsave(job_id)
    if not removed:
        try:
            apps.move(app_id, "withdrawn", note="skipped on the review page")
        except StatusError:
            pass
    apps.jobs.set_state(job_id, "dismissed")
    resp = HTMLResponse('<div class="card review-done">Skipped and dismissed.</div>')
    resp.headers["HX-Trigger"] = "reviewChanged"
    return resp


# -- cold pitches (no posting): one card per contact ------------------------------------------------------------------

def _cold_one(request: Request, contact_id: int, **extra):
    ct = out.contact(contact_id)
    if not ct or ct.mode != "cold":
        raise HTTPException(404)
    if ct.status not in ("picked", "draft_ready"):
        msg = extra.get("notice") or f"Moved to {out_rules.STATUS_LABELS[ct.status]}."
        return HTMLResponse(f'<div class="card review-done">{ct.company}: {msg} <a href="/outreach?contact={ct.id}">Open in Outreach</a></div>')
    return templates.TemplateResponse(request, "partials/review_cold.html",
                                      {**_ctx(), **_cold(ct), **extra})


@router.post("/cold/{contact_id}/looks-good", response_class=HTMLResponse)
def cold_looks_good(request: Request, contact_id: int):
    try:
        out.approve(contact_id)
    except ValueError as e:
        return _cold_one(request, contact_id, error=str(e))
    resp = _cold_one(request, contact_id, notice="Approved: it goes out in the next sending window (follow-ups only if no reply).")
    resp.headers["HX-Trigger"] = "reviewChanged"
    return resp


@router.post("/cold/{contact_id}/redraft", response_class=HTMLResponse)
async def cold_redraft(request: Request, contact_id: int):
    note = str((await request.form()).get("note", "")).strip()
    if not note:
        return _cold_one(request, contact_id, error="Write what to change first.")
    queue_redraft("contact", contact_id, note)
    return _cold_one(request, contact_id, notice="Redraft queued: the next autopilot run (or catch-up) rewrites it with your note.")


@router.post("/cold/{contact_id}/skip", response_class=HTMLResponse)
def cold_skip(request: Request, contact_id: int):
    ct = out.contact(contact_id)
    out.unpick(contact_id)
    resp = HTMLResponse(f'<div class="card review-done">Skipped {ct.company}. The company stays in '
                        f'<a href="/outreach?view=companies">Find companies</a>.</div>')
    resp.headers["HX-Trigger"] = "reviewChanged"
    return resp
