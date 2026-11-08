"""Turn the bits every source reports differently into one shape: plain text, pay, work mode, job type, dates."""
from __future__ import annotations

import html as html_lib
import re
from datetime import datetime, timezone
from typing import Optional

from bs4 import BeautifulSoup

from .models import Pay

# --- text --------------------------------------------------------------------------------

def html_to_text(value: str) -> str:
    """Job HTML (Greenhouse sends it entity-escaped) to readable plain text with line breaks kept."""
    if not value:
        return ""
    if "&lt;" in value[:400]:
        value = html_lib.unescape(value)
    soup = BeautifulSoup(value, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    # Line breaks only around block elements, so inline tags (<strong>, <a>) stay inside their sentence.
    for tag in soup.find_all(["p", "div", "section", "article", "blockquote", "pre", "table", "tr",
                              "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol"]):
        tag.insert_before("\n\n")
        tag.insert_after("\n\n")
    for li in soup.find_all("li"):
        li.insert_before("\n- ")
        li.insert_after("\n")
    text = soup.get_text("").replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    out, blank = [], False
    for line in lines:
        if not line or line == "-":
            blank = blank or not line
            continue
        if blank and out and not (line.startswith("- ") and out[-1].startswith("- ")):
            out.append("")
        out.append(line)
        blank = False
    return "\n".join(out).strip()


def sentences(text: str) -> list[str]:
    parts = []
    for line in (text or "").splitlines():
        line = line.strip(" -\t")
        if line:
            parts += [p.strip() for p in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(])", line) if p.strip()]
    return parts


def norm_words(text: str) -> str:
    return re.sub(r"[^a-z0-9+#]+", " ", (text or "").lower()).strip()


# --- pay ---------------------------------------------------------------------------------

_CUR = {"$": "USD", "€": "EUR", "£": "GBP"}
_NUM = r"\d[\d,]*(?:\.\d+)?"
_PAY_RE = re.compile(
    r"(?P<cur>[$€£])\s?(?P<a>" + _NUM + r")\s?(?P<ak>[kK])?"
    r"(?:\s*/\s*(?P<per1>hour|hr|year|yr|month|mo)\b)?"  # LinkedIn: "$130,000.00/yr - $160,000.00/yr"
    r"(?:\s*(?:-|–|—|to|and)\s*(?:[$€£])?\s?(?P<b>" + _NUM + r")\s?(?P<bk>[kK])?)?"
    r"(?:\s*(?P<code>USD|CAD|EUR|GBP|AUD|SGD))?"
    r"(?:\s*(?:/|per|an|a)\s*(?P<per>hour|hr|year|yr|annum|annually|month|mo|week|wk)\b)?"
)
_BIG_WORD = re.compile(r"^\s*(?:million|billion|mm?\b|bn?\b|\+?\s*(?:users|players|customers))", re.I)
_PAY_WORDS = re.compile(r"salary|compensation|pay\b|pay range|base|hourly|wage|rate|ote|stipend|range", re.I)
_PERIODS = {"hour": "hour", "hr": "hour", "year": "year", "yr": "year", "annum": "year", "annually": "year",
            "month": "month", "mo": "month", "week": "week", "wk": "week"}


def _num(text: Optional[str], k: Optional[str]) -> Optional[float]:
    if not text:
        return None
    value = float(text.replace(",", ""))
    return value * 1000 if k else value


def parse_pay(text: str) -> Optional[Pay]:
    """Best pay range written in the text, preferring amounts next to words like 'salary' or 'pay range'."""
    if not text:
        return None
    best, best_rank = None, -1
    for m in _PAY_RE.finditer(text):
        tail = text[m.end(): m.end() + 40]
        if _BIG_WORD.match(tail):
            continue
        ak, bk = m.group("ak"), m.group("bk")
        if bk and not ak and m.group("a") and float(m.group("a").replace(",", "")) < 1000:
            ak = bk  # "$70-90k"
        low, high = _num(m.group("a"), ak), _num(m.group("b"), bk)
        if high is not None and low is not None and high < low:
            continue
        per = _PERIODS.get((m.group("per") or m.group("per1") or "").lower())
        if not per:
            after = tail.lower()
            if re.match(r"\s*(?:usd|cad)?\s*(?:an hour|hourly|/\s*h\b|per hour)", after):
                per = "hour"
            elif low < 500:
                per = "hour"
            else:
                per = "year"
        yearly = low * (2080 if per == "hour" else 12 if per == "month" else 52 if per == "week" else 1)
        if per == "hour" and not (7 <= low <= 500):
            continue
        if per != "hour" and not (10_000 <= yearly <= 2_000_000):
            continue
        before = text[max(0, m.start() - 140): m.start()]
        rank = (2 if _PAY_WORDS.search(before) else 0) + (1 if high else 0)
        if rank > best_rank:
            code = m.group("code")
            currency = code if code else _CUR.get(m.group("cur"), "USD")
            best = Pay(low, high, currency, per, m.group(0).strip())
            best_rank = rank
    return best


def pay_label(pay: Optional[Pay]) -> str:
    if not pay or pay.min is None:
        return ""
    sym = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "CA$", "AUD": "A$"}.get(pay.currency, pay.currency + " ")

    def f(v):
        if pay.period == "hour":
            return f"{sym}{v:,.0f}" if v == int(v) else f"{sym}{v:,.2f}"
        return f"{sym}{v / 1000:,.0f}K" if v >= 1000 else f"{sym}{v:,.0f}"
    suffix = {"hour": "/hr", "month": "/mo", "week": "/wk"}.get(pay.period, "/yr")
    if pay.max and pay.max != pay.min:
        return f"{f(pay.min)}–{f(pay.max)}{suffix}"
    return f"{f(pay.min)}{suffix}"


# --- work mode / job type ------------------------------------------------------------------

_MODE_EXPLICIT = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "on-site": "onsite", "on site": "onsite",
                  "in office": "onsite", "office": "onsite"}


def map_work_mode(value: Optional[str]) -> Optional[str]:
    return _MODE_EXPLICIT.get((value or "").strip().lower().replace("_", " "))


def detect_work_mode(location: str = "", title: str = "", description: str = "") -> Optional[str]:
    head = f"{title} {location}".lower()
    if "hybrid" in head:
        return "hybrid"
    if re.search(r"\bremote\b", head):
        return "remote"
    if re.search(r"\bon-?site\b|\bin[- ]office\b", head):
        return "onsite"
    body = (description or "").lower()
    role = r"(?:this |the )?(?:role|position|job|opportunity)"
    if re.search(r"\bhybrid (?:role|position|work|schedule|model|environment)\b|\b" + role + r" is (?:a )?hybrid\b"
                 r"|\bmix of (?:remote|work[- ]from[- ]home)(?: work)? and (?:on-?site|in[- ]office)\b"
                 r"|\bon-?site (?:work )?schedule of (?:two|three|four|[2-4]) days\b|\b[2-4] days (?:a|per) week in (?:the )?office\b", body):
        return "hybrid"
    if re.search(r"\b(?:fully|100%) remote\b|\b" + role + r" is (?:fully )?remote\b|\bremote[- ]first\b", body):
        return "remote"
    if re.search(r"\b" + role + r" is (?:fully )?(?:on-?site|in[- ]office)\b|\b(?:fully|100%) (?:on-?site|in[- ]office)\b"
                 r"|\bon-?site five days\b|\b5 days (?:a|per) week in (?:the )?office\b", body):
        return "onsite"
    return None


_TYPE_EXPLICIT = {
    "intern": "internship", "internship": "internship", "co-op": "internship",
    "fulltime": "full_time", "full time": "full_time", "full-time": "full_time", "permanent": "full_time", "regular": "full_time",
    "parttime": "part_time", "part time": "part_time", "part-time": "part_time",
    "contract": "contract", "contractor": "contract", "temporary": "contract", "temp": "contract", "freelance": "contract",
    "fixed term": "contract", "fixed-term": "contract", "seasonal": "contract",
}


def map_job_type(value: Optional[str]) -> Optional[str]:
    v = (value or "").strip().lower().replace("_", " ")
    if v in _TYPE_EXPLICIT:
        return _TYPE_EXPLICIT[v]
    for word, kind in _TYPE_EXPLICIT.items():
        if re.search(r"\b" + re.escape(word) + r"\b", v):
            return kind
    return None


def detect_job_type(title: str = "", explicit: Optional[str] = None) -> Optional[str]:
    t = (title or "").lower()
    # The title wins over the feed's field: "Gameplay Engineer (Contract)" is a contract even when filed as Full Time.
    if re.search(r"\bintern(?:ship)?s?\b|\bco-?op\b|\bapprentice", t):
        return "internship"
    if re.search(r"\bpart[- ]time\b", t):
        return "part_time"
    if re.search(r"\bcontract(?:or)?\b|\btemporary\b|\bfreelance\b|\bfixed[- ]term\b", t):
        return "contract"
    return map_job_type(explicit) or ("full_time" if re.search(r"\bfull[- ]time\b", t) else None)


# --- dates ---------------------------------------------------------------------------------

def to_iso(value) -> Optional[str]:
    """Epoch milliseconds/seconds or an ISO-ish string to an ISO 8601 UTC string."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            ts = value / 1000 if value > 10**11 else value
            return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")
        text = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, OverflowError, OSError):
        return None


def age_days(iso: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    if not iso:
        return None
    now = now or datetime.now(timezone.utc)
    return (now - datetime.fromisoformat(iso)).total_seconds() / 86400
