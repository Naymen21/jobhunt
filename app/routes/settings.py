"""Settings: profile, resumes, and which API keys are configured (read-only; keys live in .env)."""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from jobsearch.config import APIFY_ACTOR, RESUME_DIR
from jobsearch.config import settings as job_settings
from jobsearch.match import skills_in
from jobsearch.resume import ResumeError, extract_text
from jobsearch.sources import ApifySource, FeedClient, FeedError
from storage.apps_repo import AppRepo
from storage.jobs_repo import PROFILE_FIELDS, JobRepo

from ..templating import templates

router = APIRouter(prefix="/settings")
repo = JobRepo()
apps = AppRepo()

TABS = [("profile", "Profile"), ("resumes", "Resumes"), ("answers", "Answer bank"), ("autopilot", "Autopilot"),
        ("keys", "Keys & usage"), ("appearance", "Appearance")]

# Pop accent choices (light, dark). The pick is stored in this browser only, like the light/dark theme.
POPS = [("pink", "Pink", "#ff85a1", "#ff85a1"), ("red", "Red", "#c1121f", "#c1121f"),
        ("rose", "Dusty rose", "#e5989b", "#e5989b"), ("steel", "Steel blue", "#669bbc", "#669bbc"),
        ("maroon", "Maroon", "#780000", "#780000")]

# Keys this project reads from .env. Values are never shown or editable here.
KEYS = [
    {"name": "APIFY_TOKEN", "used_for": "LinkedIn job search through Apify (free plan, public listings only)",
     "how": "Sign up free at apify.com, open Settings > API & Integrations, copy the Personal API token."},
    {"name": "GMAIL_ADDRESS", "used_for": "Outreach: the Gmail account emails are sent from (and whose inbox is checked for replies)",
     "how": "Your Gmail address, e.g. you@gmail.com. See 'Connecting Gmail' below."},
    {"name": "GMAIL_APP_PASSWORD", "used_for": "Outreach: lets jobhunt send through Gmail (SMTP) and read replies (IMAP, read-only)",
     "how": "A 16-character App Password from your Google Account. See 'Connecting Gmail' below."},
    {"name": "GITHUB_TOKEN", "used_for": "Finder: reading public commits (raises the limit from 60 to 5000 calls/hour)",
     "how": "github.com > Settings > Developer settings > Personal access tokens > Fine-grained, with no permissions."},
]


def key_status() -> list[dict]:
    rows = []
    for k in KEYS:
        rows.append({**k, "set": bool(os.environ.get(k["name"], ""))})  # only whether it's set; no characters shown
    return rows


def _context(tab: str, **extra) -> dict:
    resumes = repo.resumes()
    selected = extra.pop("selected", None) or (resumes[0] if resumes else None)
    from jobsearch.student import AREA_PRESETS, LEVELS, US_STATES
    return {"active": "settings", "tab": tab, "tabs": TABS, "profile": repo.profile(), "resumes": resumes,
            "LEVELS": LEVELS, "AREA_PRESETS": AREA_PRESETS, "US_STATES": US_STATES,
            "selected": selected, "selected_skills": sorted(skills_in(selected.text), key=str.lower) if selected else [],
            "keys": key_status(), "usage": repo.usage_this_month(), "apify_actor": APIFY_ACTOR,
            "anthropic_key_present": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "budget_fraction": job_settings.apify_budget_fraction, "max_results": job_settings.apify_max_results,
            "pops": POPS, "answers": apps.answers() if tab == "answers" else [],
            **(_autopilot_ctx() if tab == "autopilot" else {}),
            **extra}


@router.get("", response_class=HTMLResponse)
def page(request: Request, tab: str = "profile"):
    tab = tab if tab in dict(TABS) else "profile"
    return templates.TemplateResponse(request, "settings.html", _context(tab))


@router.post("/profile", response_class=HTMLResponse)
async def save_profile(request: Request):
    form = await request.form()
    repo.save_profile({f: str(form.get(f, "")) for f in PROFILE_FIELDS})
    return templates.TemplateResponse(request, "partials/settings_profile.html", _context("profile", saved=True))


# -- resumes ----------------------------------------------------------------------------------

def _resumes(request: Request, **extra):
    return templates.TemplateResponse(request, "partials/settings_resumes.html", _context("resumes", **extra))


@router.post("/resumes", response_class=HTMLResponse)
async def upload_resume(request: Request, file: UploadFile = File(...), label: str = Form("")):
    data = await file.read()
    try:
        kind, text = extract_text(file.filename or "", data)
    except ResumeError as e:
        return _resumes(request, error=str(e))
    row = repo.add_resume(label.strip(), file.filename or f"resume.{kind}", kind, data, text)
    if row.is_default:
        repo.rescore(row.text)
    return _resumes(request, selected=row, notice=f"Uploaded {row.filename}: {len(text):,} characters of text read.")


@router.get("/resumes/{resume_id}", response_class=HTMLResponse)
def preview_resume(request: Request, resume_id: int):
    row = repo.resume(resume_id)
    if not row:
        raise HTTPException(404)
    return _resumes(request, selected=row)


@router.post("/resumes/{resume_id}/default", response_class=HTMLResponse)
def make_default(request: Request, resume_id: int):
    row = repo.resume(resume_id)
    if not row:
        raise HTTPException(404)
    repo.set_default_resume(resume_id)
    n = repo.rescore(row.text)
    return _resumes(request, selected=repo.resume(resume_id),
                    notice=f"{row.label} is now the default." + (f" Re-scored {n} saved job matches." if n else ""))


@router.post("/resumes/{resume_id}/delete", response_class=HTMLResponse)
def delete_resume(request: Request, resume_id: int):
    repo.delete_resume(resume_id)
    default = repo.default_resume()
    repo.rescore(default.text if default else None)
    return _resumes(request)


@router.get("/resumes/{resume_id}/file")
def download_resume(resume_id: int):
    row = repo.resume(resume_id)
    if not row or not (RESUME_DIR / row.stored_name).exists():
        raise HTTPException(404)
    return FileResponse(RESUME_DIR / row.stored_name, filename=row.filename)


# -- usage ------------------------------------------------------------------------------------

@router.get("/usage/apify", response_class=HTMLResponse)
def apify_usage(request: Request):
    """Live usage from Apify's account API. Loaded after the page so a slow call doesn't block it."""
    source = ApifySource(job_settings)
    ctx = {"configured": source.configured, "usage": None, "error": None,
           "budget_fraction": job_settings.apify_budget_fraction}
    if source.configured:
        client = FeedClient(job_settings)
        try:
            ctx["usage"] = source.usage(client)
            spent = repo.apify_spent_today()
            ctx["daily_budget"] = source.daily_budget(ctx["usage"], job_settings.apify_budget_fraction, spent)
            ctx["spent_today"] = spent
            ctx["left_month"] = max(0.0, ctx["usage"].limit_usd * job_settings.apify_budget_fraction - ctx["usage"].used_usd)
        except FeedError as e:
            ctx["error"] = str(e)
        finally:
            client.close()
    return templates.TemplateResponse(request, "partials/apify_usage.html", ctx)



# -- answer bank ------------------------------------------------------------------------------

def _answers(request: Request, **extra):
    return templates.TemplateResponse(request, "partials/settings_answers.html", _context("answers", **extra))


@router.post("/answers", response_class=HTMLResponse)
async def save_answers(request: Request):
    form = await request.form()
    values = {}
    for row in apps.answers():
        if f"a-{row.id}" in form:
            values[row.id] = (str(form.get(f"q-{row.id}", row.question)), str(form.get(f"a-{row.id}", "")))
    apps.save_answers(values)
    new_q, new_a = str(form.get("new_question", "")).strip(), str(form.get("new_answer", "")).strip()
    if new_q:
        apps.add_answer(new_q, new_a)
    return _answers(request, saved=True)


@router.post("/answers/{answer_id}/delete", response_class=HTMLResponse)
def delete_answer(request: Request, answer_id: int):
    apps.delete_answer(answer_id)
    return _answers(request)


# -- autopilot ------------------------------------------------------------------------------------------------------

_manual_run = {"thread": None}


def _autopilot_ctx() -> dict:
    from ..autopilot_run import last_run, load_settings
    run = last_run()
    t = _manual_run["thread"]
    return {"ap": load_settings(), "run": run, "run_log": json.loads(run.log_json) if run else [],
            "running": bool(t and t.is_alive()) or bool(run and run.status == "running")}


def _autopilot(request: Request, **extra):
    return templates.TemplateResponse(request, "partials/settings_autopilot.html", {**_context("autopilot"), **extra})


@router.post("/autopilot", response_class=HTMLResponse)
async def save_autopilot(request: Request):
    from autopilot.rules import AutopilotSettings
    from ..autopilot_run import save_settings
    form = await request.form()
    values = {k: form.get(k) for k in AutopilotSettings.__dataclass_fields__ if k in form}
    for k in ("enabled", "source_yc", "source_yc_small", "source_builtin", "source_hn", "source_simplify"):
        values[k] = form.get(k) == "on"
    for k, v in list(values.items()):
        if isinstance(v, str) and v.strip().lstrip("-").isdigit():
            values[k] = int(v)
    current = _autopilot_ctx()["ap"].to_dict()
    save_settings(AutopilotSettings.from_dict({**current, **values}))
    return _autopilot(request, saved=True)


@router.post("/autopilot/run", response_class=HTMLResponse)
def run_autopilot(request: Request):
    """Manual run in the background (same steps as the morning run)."""
    import threading
    from ..autopilot_run import Autopilot
    t = _manual_run["thread"]
    if not (t and t.is_alive()):
        t = threading.Thread(target=lambda: Autopilot().run("manual"), name="autopilot-manual", daemon=True)
        _manual_run["thread"] = t
        t.start()
    return _autopilot(request)


@router.get("/autopilot/status", response_class=HTMLResponse)
def autopilot_status(request: Request):
    return _autopilot(request)


@router.get("/usage/apify-mini", response_class=HTMLResponse)
def apify_mini(request: Request):
    source = ApifySource(job_settings)
    if not source.configured:
        return HTMLResponse('<span class="rs-label">LinkedIn budget</span><span class="xsmall muted">Apify not configured</span>')
    client = FeedClient(job_settings)
    try:
        u = source.usage(client)
    except FeedError as e:
        return HTMLResponse(f'<span class="rs-label">LinkedIn budget</span><span class="xsmall warn-text">{e}</span>')
    finally:
        client.close()
    spent = repo.apify_spent_today()
    daily = source.daily_budget(u, job_settings.apify_budget_fraction, spent)
    left = max(0.0, u.limit_usd * job_settings.apify_budget_fraction - u.used_usd)
    over = u.used_usd >= u.limit_usd * job_settings.apify_budget_fraction
    status = "paused until the monthly reset (80% line)" if over else f"${max(0, daily - spent):.2f} left today"
    return HTMLResponse(f'<span class="rs-label">LinkedIn budget</span><span class="rs-status">{status}</span>'
                        f'<span class="xsmall muted">today ${daily:.2f} · ${left:.2f} left this month before the 80% line</span>')
