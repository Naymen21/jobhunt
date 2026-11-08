"""Sections that arrive in later phases."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..templating import templates

router = APIRouter()

COMING = {
}


def _make(key: str):
    title, blurb = COMING[key]

    def view(request: Request):
        return templates.TemplateResponse(request, "coming_soon.html",
                                          {"active": key, "title": title, "blurb": blurb})
    return view


for _key in COMING:
    router.add_api_route(f"/{_key}", _make(_key), methods=["GET"], response_class=HTMLResponse)
