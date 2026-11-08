"""Sort an incoming email that answers one of our outreach threads: bounce, out-of-office, stop, or a real reply."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

_BOUNCE_FROM = re.compile(r"mailer-daemon|postmaster|mail delivery (?:subsystem|system)", re.I)
_BOUNCE_SUBJECT = re.compile(r"delivery status notification|undeliverable|undelivered mail|delivery (?:has )?failed|"
                             r"returned mail|address not found|failure notice", re.I)
_OOO = re.compile(r"out of (?:the )?office|\bOOO\b|on (?:vacation|holiday|leave|parental leave|pto)|away from (?:my )?"
                  r"(?:desk|email|the office)|automatic reply|auto-?reply|limited access to (?:my )?email", re.I)
_STOP = re.compile(r"not interested|no thanks|please (?:stop|don'?t|do not) (?:email|contact|reach)|stop (?:emailing|contacting)|"
                   r"unsubscribe|remove me|take me off|do not contact|don'?t contact me|not (?:a )?good fit for (?:us|you)", re.I)
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_RETURN = re.compile(r"(?:back|return(?:ing)?|returns|in the office|until|till|through)\s+(?:on|by|after|from)?\s*(?:\w+day,?\s+)?"
                     r"(?:(?P<month>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s+(?P<day>\d{1,2})"
                     r"|(?P<m2>\d{1,2})/(?P<d2>\d{1,2})(?:/(?P<y2>\d{2,4}))?)", re.I)


@dataclass
class Classified:
    kind: str  # bounce | out_of_office | stop | reply
    return_date: Optional[date] = None
    snippet: str = ""


def _snip(body: str) -> str:
    lines = [l for l in (body or "").splitlines() if l.strip() and not l.lstrip().startswith(">")]
    text = " ".join(" ".join(lines).split())
    return text[:240]


def return_date(text: str, today: date) -> Optional[date]:
    m = _RETURN.search(text or "")
    if not m:
        return None
    try:
        if m.group("month"):
            month, day, year = _MONTHS[m.group("month")[:3].lower()], int(m.group("day")), today.year
        else:
            month, day = int(m.group("m2")), int(m.group("d2"))
            year = int(m.group("y2")) if m.group("y2") else today.year
            year += 2000 if year < 100 else 0
        found = date(year, month, day)
    except ValueError:
        return None
    if found < today and not (m.group("y2")):
        found = date(found.year + 1, found.month, found.day)
    return found


def classify(from_addr: str, subject: str, body: str, headers: Optional[dict] = None,
             today: Optional[date] = None) -> Classified:
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    today = today or datetime.now().date()
    top = "\n".join(l for l in (body or "").splitlines() if not l.lstrip().startswith(">"))  # ignore our quoted text
    if _BOUNCE_FROM.search(from_addr or "") or _BOUNCE_SUBJECT.search(subject or "") \
            or "delivery-status" in headers.get("content-type", ""):
        return Classified("bounce", snippet=_snip(top))
    auto = headers.get("auto-submitted", "").lower() not in ("", "no") or "x-autoreply" in headers \
        or headers.get("precedence", "").lower() in ("auto_reply", "auto-reply")
    if auto or _OOO.search(subject or "") or _OOO.search(top[:600]):
        return Classified("out_of_office", return_date(top, today), _snip(top))
    if _STOP.search(subject or "") or _STOP.search(top[:1200]):
        return Classified("stop", snippet=_snip(top))
    return Classified("reply", snippet=_snip(top))
