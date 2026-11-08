"""Autopilot settings and decisions, as plain functions (easy to test, no storage, no network)."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


@dataclass
class AutopilotSettings:
    enabled: bool = True
    min_ai_score: int = 70
    max_saves_per_day: int = 5
    cap_scores: int = 15
    cap_app_drafts: int = 5
    cap_outreach_drafts: int = 10
    cap_redrafts: int = 5
    finder_searches_per_day: int = 3  # polite company-site crawls to find people for saved jobs
    size_checks_per_day: int = 15
    board_checks_per_day: int = 10
    linkedin_results: int = 25  # per LinkedIn run (max 50)
    backup_dir: str = str(Path.home() / "jobhunt-backups")  # next to your home folder, on Windows and Mac
    backups_keep: int = 14
    dismissed_cleanup_days: int = 60
    stale_contact_days: int = 182
    source_yc: bool = True
    source_builtin: bool = True
    source_hn: bool = True
    source_simplify: bool = True
    builtin_pages: int = 3
    source_yc_small: bool = True  # every active YC company with 5-60 people (for cold pitches), not just hiring ones
    cold_per_day: int = 0  # cold pitches the autopilot starts per day (Outreach > Find companies filters); 0 = off
    cold_filters: dict = field(default_factory=dict)  # the filters saved from Outreach > Find companies

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "AutopilotSettings":
        s = cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})
        s.linkedin_results = max(1, min(int(s.linkedin_results), 50))
        s.min_ai_score = max(0, min(int(s.min_ai_score), 100))
        s.cold_per_day = max(0, min(int(s.cold_per_day), 10))
        for name in ("max_saves_per_day", "cap_scores", "cap_app_drafts", "cap_outreach_drafts", "cap_redrafts"):
            setattr(s, name, max(0, min(int(getattr(s, name)), 50)))
        return s


@dataclass
class Candidate:
    job_id: int
    ai_score: Optional[int]
    eligible: bool
    pitch: bool
    hs_ok: bool = False
    local: bool = False
    employees: Optional[int] = None
    state: str = ""  # "" | saved | dismissed


def pick_saves(candidates: list[Candidate], settings: AutopilotSettings, saved_today: int) -> list[Candidate]:
    """Jobs the autopilot saves: AI score >= min, eligible OR pitch candidate, not already saved/dismissed,
    at most max_saves_per_day per day. Best first: pitch/high-school-friendly/local, then score."""
    room = max(0, settings.max_saves_per_day - saved_today)
    ok = [c for c in candidates if c.state == "" and c.ai_score is not None and c.ai_score >= settings.min_ai_score
          and (c.eligible or c.pitch)]
    ok.sort(key=lambda c: (-(c.hs_ok * 2 + c.pitch + c.local), -(c.ai_score or 0), c.job_id))
    return ok[:room]


def remaining(cap: int, used: int) -> int:
    return max(0, cap - used)


# --- Claude usage limits ---------------------------------------------------------------------------------------------

_LIMIT = re.compile(r"usage limit reached|claude ai usage limit|you've hit your (?:\w+ )?limit|you've reached your (?:\w+ )?limit|"
                    r"(?:5-hour|weekly|session) limit (?:reached|hit)|limit reached\s*[|∙·]|out of (?:extra )?usage", re.I)
_EPOCH = re.compile(r"limit reached\|(\d{10})")
_RESETS = re.compile(r"resets?\s+(?:at\s+|on\s+)?([0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm)?(?:\s*\([^)]*\))?|[A-Z][a-z]{2,8}\s+\d{1,2}[^.\n]*)",
                     re.I)


def parse_limit(output: str) -> tuple[bool, str]:
    """(hit a usage limit?, when it resets as text if Claude said)."""
    text = (output or "")[-800:]  # the limit notice is at the end; job text earlier in the output can't trigger it
    if not _LIMIT.search(text):
        return False, ""
    m = _EPOCH.search(text)
    if m:
        return True, datetime.fromtimestamp(int(m.group(1))).astimezone().strftime("%a %H:%M")
    m = _RESETS.search(text)
    return True, (m.group(1).strip() if m else "")


# --- review page order -------------------------------------------------------------------------------------------------

def review_priority(pitch: bool, ai_score: Optional[int], basic: Optional[int], deadline: Optional[str],
                    needs_person: bool = False) -> tuple:
    """Pitch candidates and best matches first, then soonest deadline. Cards that need a person sink a little."""
    score = ai_score if ai_score is not None else (basic or 0) - 10
    return (needs_person, not pitch, -score, deadline or "9999-12-31")


def due_weekly(last: Optional[str], now: datetime) -> bool:
    return not last or now - datetime.fromisoformat(last) >= timedelta(days=7)
