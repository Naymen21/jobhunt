"""Dashboard: one screen with what's new and what to do next. Read-only; actions link to their pages."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from jobsearch.models import WORK_MODE_LABELS
from storage.dashboard import summary

from ..templating import templates

router = APIRouter()


def greeting(now: datetime) -> str:
    return "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 18 else "Good evening"


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    now = datetime.now()
    return templates.TemplateResponse(request, "dashboard.html", {
        "active": "review", "d": summary(), "greeting": greeting(now), "today": f"{now:%A, %B} {now.day}",
        "WORK_MODE_LABELS": WORK_MODE_LABELS,
    })
