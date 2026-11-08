from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.templating import Jinja2Templates

from finder.patterns.templates import display_template
from finder.scoring import found_email_score
from finder.titles import DEPARTMENT_LABELS, SENIORITY_LABELS

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
STATIC_DIR = Path(__file__).parent / "static"


def static_url(name: str) -> str:
    """/static/<name>?v=<last modified>, so the browser fetches a fresh copy whenever the file changes."""
    try:
        return f"/static/{name}?v={int((STATIC_DIR / name).stat().st_mtime)}"
    except OSError:
        return f"/static/{name}"

# Sidebar groups: (key, label, href, icon, coming_soon). The four Email Finder tools live on /finder
# and switch by URL hash; the page script marks the right one active.
NAV_GROUPS = [
    [("review", "Review", "/review", "home", False)],
    [("finder", "Finder", "/finder#company", "people", False),
     ("verifier", "Verifier", "/finder#verifier", "finder", False),
     ("bulk", "Bulks", "/finder#bulk", "layers", False),
     ("history", "History", "/finder#history", "clock", False)],
    [("jobs", "Jobs", "/jobs", "jobs", False),
     ("applications", "Applications", "/applications", "applications", False),
     ("outreach", "Outreach", "/outreach", "outreach", False)],
    [("settings", "Settings", "/settings", "settings", False)],
]
NAV = [item for group in NAV_GROUPS for item in group]


def _platform() -> dict:
    """Paths and commands for the setup and scheduling steps, for wherever this copy lives (Windows or Mac)."""
    import os
    import sys
    root = Path(__file__).resolve().parent.parent
    win = os.name == "nt"
    py = root / ".venv" / ("Scripts/python.exe" if win else "bin/python")
    return {"windows": win, "mac": sys.platform == "darwin", "root": str(root), "python": str(py),
            "env_file": str(root / ".env"), "sep": "\\" if win else "/",
            "venv_python": ".venv\\Scripts\\python" if win else ".venv/bin/python"}


def _student():
    from jobsearch.student import current
    return current()


templates.env.globals["platform"] = _platform
templates.env.globals["student"] = _student

CHECK_LABELS = {
    "syntax": "Syntax",
    "random": "Looks human",
    "provider": "Work domain",
    "mx": "MX records",
    "smtp": "Mailbox (SMTP)",
    "catch_all": "Not catch-all",
}


def ago(value) -> str:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    secs = int((datetime.now(timezone.utc) - value).total_seconds())
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{secs // size}{unit} ago"
    return "just now"


def since(value) -> str:
    """Posting age in human units: 'today', '5d ago', '4 mo ago', '9 yr ago'."""
    if not value:
        return ""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - value).days
    if days < 1:
        return "today"
    if days < 60:
        return f"{days}d ago"
    if days < 365:
        return f"{days // 30} mo ago"
    return f"{days // 365} yr ago"


def localtime(value) -> str:
    """Any stored ISO time (UTC or local) shown as local 'YYYY-MM-DD HH:MM'."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)[:16]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def tidy_description(text: str) -> str:
    """Display-only cleanup: 'Team Name:\\n\\nStarCraft' -> 'Team Name: StarCraft', and no runs of blank lines."""
    text = re.sub(r"(?m)^([^\n]{1,40}):[ \t]*\n+(?=[^\n-][^\n]{0,80}$)", r"\1: ", text or "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def shorturl(url: str, width: int = 48) -> str:
    parts = urlsplit(url or "")
    text = (parts.hostname or "").removeprefix("www.") + (parts.path if parts.path != "/" else "")
    return text if len(text) <= width else text[: width - 1] + "…"


def fromjson(value: str):
    try:
        return json.loads(value or "{}")
    except ValueError:
        return {}


def score_tier(score: int) -> str:
    """Badge colour for a 0-100 confidence."""
    return "high" if score >= 80 else "mid" if score >= 50 else "low" if score > 0 else "none"


def avatar_class(name: str) -> str:
    """Stable colour per person, so the same name always gets the same avatar."""
    return f"av-c{sum(ord(c) for c in (name or '')) % 8}"


templates.env.globals.update(display_template=display_template, found_email_score=found_email_score,
                             DEPARTMENT_LABELS=DEPARTMENT_LABELS, SENIORITY_LABELS=SENIORITY_LABELS)
templates.env.filters["avatar_class"] = avatar_class
templates.env.filters["tier"] = score_tier
templates.env.filters["shorturl"] = shorturl
templates.env.filters["fromjson"] = fromjson
templates.env.globals.update(NAV=NAV, NAV_GROUPS=NAV_GROUPS, CHECK_LABELS=CHECK_LABELS, static_url=static_url)
templates.env.filters["ago"] = ago
templates.env.filters["since"] = since
templates.env.filters["localtime"] = localtime
templates.env.filters["tidy_description"] = tidy_description
