"""Application pipeline statuses. You move applications along; nothing is ever submitted for you."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

STATUSES = ["saved", "drafts_ready", "approved", "applied", "interview", "offer", "rejected", "withdrawn"]
STATUS_LABELS = {
    "saved": "Saved", "drafts_ready": "Drafts ready", "approved": "Approved", "applied": "Applied",
    "interview": "Interview", "offer": "Offer", "rejected": "Rejected", "withdrawn": "Withdrawn",
}
CLOSED = ["offer", "rejected", "withdrawn"]
# Board columns: the three end states share one "Closed" column, each with its own tag.
COLUMNS = [("saved", "Saved", ["saved"]), ("drafts_ready", "Drafts ready", ["drafts_ready"]),
           ("approved", "Approved", ["approved"]), ("applied", "Applied", ["applied"]),
           ("interview", "Interview", ["interview"]), ("closed", "Closed", CLOSED)]
# Statuses an application can be moved to from each status (Withdrawn is always available).
NEXT = {
    "saved": ["drafts_ready", "approved"],
    "drafts_ready": ["approved", "saved"],
    "approved": ["applied", "drafts_ready"],
    "applied": ["interview", "offer", "rejected"],
    "interview": ["offer", "rejected", "applied"],
    "offer": ["interview"],
    "rejected": ["applied", "interview"],
    "withdrawn": ["saved"],
}
# Saved jobs at these stages can be unsaved from the Jobs page (the application is removed).
REMOVABLE = {"saved", "drafts_ready"}
FOLLOW_UP_DAYS = 7


class StatusError(ValueError):
    pass


def check_move(current: str, new: str) -> None:
    if new not in STATUSES:
        raise StatusError(f"unknown status '{new}'")
    if new != current and new != "withdrawn" and new not in NEXT.get(current, []):
        raise StatusError(f"can't go from {STATUS_LABELS[current]} to {STATUS_LABELS[new]}")


def follow_up_date(applied: date, days: int = FOLLOW_UP_DAYS) -> str:
    return (applied + timedelta(days=days)).isoformat()


def follow_up_due(follow_up_at: Optional[str], status: str, today: Optional[date] = None) -> bool:
    if not follow_up_at or status not in ("applied", "interview"):
        return False
    return date.fromisoformat(follow_up_at[:10]) <= (today or date.today())
