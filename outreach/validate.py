"""Checks on outreach drafts from the Claude Code commands. Errors stop the save; nothing half-saves."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from applications.validate import BANNED_PHRASES, PLACEHOLDER, words

EMAIL_MAX_WORDS = 130  # body, not counting the signature
FOLLOWUP1_MAX_WORDS = 80
FOLLOWUP2_MAX_WORDS = 50
LINKEDIN_MAX_CHARS = 200
SUBJECT_MAX_CHARS = 70
URL = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|org|net|io|ai|co|app|dev)(?:/\S*)?\b", re.I)
HONORIFIC = re.compile(r"^\s*(?:hi|hello|dear)\s+(?:sir|madam|mr\.?|mrs\.?|ms\.?|miss|dr\.?)\b", re.I | re.M)
BAD_SUBJECTS = re.compile(r"^\s*(?:internship request|request for internship|internship inquiry|job inquiry|hello|hi)\s*$", re.I)
BAIT = re.compile(r"\b(?:learn (?:more )?about your (?:job|role|work)|pick your brain)\b", re.I)
OUTREACH_STOCK = BANNED_PHRASES + ["i hope this email finds you well", "i hope this finds you well", "i'm reaching out to",
                                   "i am reaching out to", "just following up", "just checking in", "circle back",
                                   "touch base", "i know you're busy", "sorry to bother"]


@dataclass
class Checked:
    ok: list = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _common(text: str, where: str, max_words: int) -> list[str]:
    problems = []
    if not text.strip():
        return [f"{where} is empty"]
    if words(text) > max_words:
        problems.append(f"{where} is {words(text)} words (max {max_words})")
    if URL.search(text):
        problems.append(f"{where} has a link; links only go in the signature")
    if HONORIFIC.search(text):
        problems.append(f"{where} uses Sir/Mr/Ms; use their first name")
    low = text.lower()
    problems += [f"{where} uses a stock phrase: \"{p}\"" for p in OUTREACH_STOCK if p in low]
    if PLACEHOLDER.search(text):
        problems.append(f"{where} has a placeholder: {PLACEHOLDER.search(text).group(0)}")
    return problems


def check_outreach(items, known_contacts: dict[int, dict]) -> Checked:
    """known_contacts: {contact_id: {"first_name": ..., "unpaid_ok": bool}}"""
    out = Checked()
    if not isinstance(items, list):
        out.errors.append("expected a JSON list")
        return out
    for n, it in enumerate(items, 1):
        if not isinstance(it, dict):
            out.errors.append(f"item {n}: not an object")
            continue
        cid = it.get("contact_id")
        if cid not in known_contacts:
            out.errors.append(f"item {n}: contact_id {cid!r} isn't a picked contact")
            continue
        info, where, p = known_contacts[cid], f"contact {cid}", []
        hook = (it.get("hook") or "").strip()
        source = (it.get("hook_source") or "").strip()
        level = (it.get("hook_level") or "").strip()
        subject = " ".join((it.get("subject") or "").split())
        body = (it.get("email") or "").strip()
        fu1, fu2 = (it.get("followup1") or "").strip(), (it.get("followup2") or "").strip()
        note = " ".join((it.get("linkedin_note") or "").split())
        if not hook:
            p.append("hook is empty")
        if not source.startswith(("http://", "https://")):
            p.append("hook_source must be the URL where you found the hook")
        if level not in ("person", "team", "company"):
            p.append("hook_level must be person, team or company")
        if not subject:
            p.append("subject is empty")
        elif len(subject) > SUBJECT_MAX_CHARS or BAD_SUBJECTS.match(subject):
            p.append("subject must be short and specific to them (not 'Internship Request')")
        p += _common(body, "email", EMAIL_MAX_WORDS)
        if body and info.get("first_name") and not re.match(rf"\s*(?:hi|hello|hey)\s+{re.escape(info['first_name'])}\b", body, re.I):
            p.append(f"email should open with their first name, e.g. \"Hi {info['first_name']},\"")
        from jobsearch.student import current as _who
        ask = r"\bintern(?:ship)?\b|\b(?:role|position|job|opening)\b" if _who().level == "recent_grad" else r"\bintern(?:ship)?\b"
        if body and not re.search(ask, body, re.I):
            p.append("email must say plainly that you're interested in an internship"
                     + (" or a role" if _who().level == "recent_grad" else ""))
        if body and BAIT.search(body):
            p.append("email reads like a bait-and-switch ('learn about your job'); ask about the internship directly")
        paragraphs = [x for x in re.split(r"\n\s*\n", body) if x.strip()]
        if body and len(paragraphs) > 5:  # greeting + 3 short paragraphs (+ thanks)
            p.append("email should be 3 short paragraphs")
        from jobsearch.student import current
        if body and current().high_school and (info.get("pitch") or info.get("cold")) \
                and not re.search(r"high[- ]school", " ".join(paragraphs[:3]), re.I):
            p.append(f"{'cold' if info.get('cold') else 'pitch'} emails must say you're in high school in the first or second sentence")
        if body and re.search(r"\bunpaid\b", body, re.I) and not info.get("unpaid_ok"):
            p.append("email mentions unpaid work, but this company isn't marked 'small startup + OK to offer unpaid'")
        p += _common(fu1, "followup1", FOLLOWUP1_MAX_WORDS)
        p += _common(fu2, "followup2", FOLLOWUP2_MAX_WORDS)
        if not note:
            p.append("linkedin_note is empty")
        elif len(note) > LINKEDIN_MAX_CHARS:
            p.append(f"linkedin_note is {len(note)} characters (max {LINKEDIN_MAX_CHARS})")
        elif URL.search(note):
            p.append("linkedin_note has a link")
        if p:
            out.errors += [f"{where}: {x}" for x in p]
            continue
        out.ok.append({"contact_id": cid, "hook": hook, "hook_source": source, "hook_level": level, "subject": subject,
                       "email": body, "followup1": fu1, "followup2": fu2, "linkedin_note": note,
                       "notes": (it.get("notes") or "").strip()[:1200], "words": words(body)})
    return out


def check_text(items, known_ids: set[int], field_name: str, max_words: int) -> Checked:
    """For /prep-call (call_prep) and /draft-thanks (thanks)."""
    out = Checked()
    for n, it in enumerate(items if isinstance(items, list) else [], 1):
        cid, text = it.get("contact_id"), (it.get(field_name) or "").strip()
        if cid not in known_ids:
            out.errors.append(f"item {n}: unknown contact_id {cid!r}")
        elif not text:
            out.errors.append(f"contact {cid}: {field_name} is empty")
        elif words(text) > max_words:
            out.errors.append(f"contact {cid}: {field_name} is {words(text)} words (max {max_words})")
        elif field_name == "thanks" and URL.search(text):
            out.errors.append(f"contact {cid}: thank-you has a link")
        elif PLACEHOLDER.search(text):
            out.errors.append(f"contact {cid}: {field_name} has a placeholder")
        else:
            out.ok.append({"contact_id": cid, field_name: text})
    if not isinstance(items, list):
        out.errors.append("expected a JSON list")
    return out
