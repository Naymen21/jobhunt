"""Sending rules from CLAUDE.md, as plain functions so they're easy to test.

- Daily cap: 10/day to start. After 14 days with bounces under 2%, 20/day; after another 14 clean days, 25/day.
  An automatic bounce pause restarts the clock. Hard ceiling 30.
- Weekdays 8:00-17:00 local time only, with a random 4-12 minute gap between sends.
- Follow-up 1: 7 days after the first email. Follow-up 2: 14 days after follow-up 1. Only if there's no reply.
- Pause everything if 2+ bounces in a day, or the bounce rate passes 2% (once at least 50 emails have gone out).
- Only "seen in a public source" or strong-pattern addresses can be queued without the owner's per-contact OK.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

STATUSES = ["picked", "draft_ready", "approved", "sent", "followup1", "followup2",
            "replied", "call_scheduled", "referred", "no_response", "do_not_contact"]
STATUS_LABELS = {
    "picked": "Picked", "draft_ready": "Draft ready", "approved": "Approved", "sent": "Sent",
    "followup1": "Follow-up 1 sent", "followup2": "Follow-up 2 sent", "replied": "Replied",
    "call_scheduled": "Call scheduled", "referred": "Referred", "no_response": "No response",
    "do_not_contact": "Do not contact",
}
COLUMNS = [("picked", "Picked", ["picked"]), ("draft_ready", "Draft ready", ["draft_ready"]),
           ("approved", "Approved", ["approved"]), ("sent", "Sent", ["sent", "followup1", "followup2"]),
           ("replied", "Replied", ["replied", "call_scheduled", "referred"]),
           ("closed", "Closed", ["no_response", "do_not_contact"])]
WAITING_FOR_REPLY = {"sent", "followup1", "followup2"}

RAMP = [(0, 10), (14, 20), (28, 25)]  # (clean days since sending started or since the last bounce pause, cap)
HARD_CEILING = 30
SEND_START, SEND_END = time(8, 0), time(17, 0)
GAP_MINUTES = (4, 12)
FOLLOWUP1_DAYS, FOLLOWUP2_DAYS = 7, 14
NO_RESPONSE_DAYS = 7  # after follow-up 2
BOUNCES_PER_DAY_LIMIT = 2
BOUNCE_RATE_LIMIT = 0.02
BOUNCE_RATE_MIN_SENT = 50
QUEUE_MIN_CONFIDENCE = 80  # strong pattern; "seen" addresses are 85-97
APPLY_BY_DAYS = 5  # "reach out before applying": apply within this many days of the first email
EMAIL_AFTER_APPLYING_DAYS = (1, 2)


def daily_cap(clean_since: Optional[date], today: date) -> int:
    """`clean_since`: the first send day, or the day of the last automatic bounce pause if that's later."""
    days = max(0, (today - clean_since).days) if clean_since else 0
    cap = RAMP[0][1]
    for after, value in RAMP:
        if days >= after:
            cap = value
    return min(cap, HARD_CEILING)


def in_send_window(moment: datetime) -> bool:
    return moment.weekday() < 5 and SEND_START <= moment.time() < SEND_END


def next_window_start(moment: datetime) -> datetime:
    """The next moment sending is allowed (the moment itself if it's inside the window)."""
    if in_send_window(moment):
        return moment
    day = moment.date()
    if moment.time() >= SEND_END or moment.weekday() >= 5:
        day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, SEND_START, moment.tzinfo)


def random_gap(rng: Optional[random.Random] = None) -> timedelta:
    rng = rng or random.Random()
    return timedelta(seconds=rng.randint(GAP_MINUTES[0] * 60, GAP_MINUTES[1] * 60))


@dataclass
class SendDecision:
    ok: bool
    reason: str = ""
    wait_until: Optional[datetime] = None


def can_send(now: datetime, sent_today: int, first_send: Optional[date], last_sent: Optional[datetime],
             paused: bool, next_gap: timedelta) -> SendDecision:
    """May one more email go out right now?"""
    if paused:
        return SendDecision(False, "sending is paused")
    if not in_send_window(now):
        return SendDecision(False, "outside sending hours (weekdays 8am-5pm)", next_window_start(now))
    cap = daily_cap(first_send, now.date())
    if sent_today >= cap:
        return SendDecision(False, f"today's limit reached ({cap}/day)",
                            next_window_start(datetime.combine(now.date() + timedelta(days=1), SEND_START, now.tzinfo)))
    if last_sent and now - last_sent < next_gap:
        return SendDecision(False, "waiting a few minutes between emails", last_sent + next_gap)
    return SendDecision(True)


def bounce_pause_reason(bounces_today: int, total_sent: int, total_bounces: int) -> Optional[str]:
    if bounces_today >= BOUNCES_PER_DAY_LIMIT:
        return f"{bounces_today} emails bounced today"
    if total_sent >= BOUNCE_RATE_MIN_SENT and total_bounces / total_sent > BOUNCE_RATE_LIMIT:
        return f"bounce rate is {100 * total_bounces / total_sent:.1f}% (limit {100 * BOUNCE_RATE_LIMIT:.0f}%)"
    return None


def followup_dates(first_sent: date) -> tuple[date, date]:
    fu1 = first_sent + timedelta(days=FOLLOWUP1_DAYS)
    return fu1, fu1 + timedelta(days=FOLLOWUP2_DAYS)


def queueable(confidence: int, kind: str, checked_ok: bool) -> tuple[bool, str]:
    """Can this address go in the send queue?"""
    if checked_ok:
        return True, "you confirmed this address"
    if kind == "role":
        return False, "shared inbox: confirm it before queueing"
    if confidence >= QUEUE_MIN_CONFIDENCE:
        return True, "seen in a public source" if confidence >= 85 else "strong pattern match"
    return False, f"confidence {confidence} is below {QUEUE_MIN_CONFIDENCE}: confirm the address first"


def first_send_not_before(applied_on: Optional[date], reach_out_first: bool, approved_on: date,
                          rng: Optional[random.Random] = None) -> Optional[date]:
    """Default order: apply first, then email 1-2 days later. 'Reach out before applying' sends right away.
    Returns None when the email has to wait for the application to be marked applied."""
    if reach_out_first:
        return approved_on
    if applied_on is None:
        return None
    rng = rng or random.Random()
    return max(approved_on, applied_on + timedelta(days=rng.randint(*EMAIL_AFTER_APPLYING_DAYS)))


def apply_by(first_sent: date) -> date:
    return first_sent + timedelta(days=APPLY_BY_DAYS)
