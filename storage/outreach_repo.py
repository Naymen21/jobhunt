"""SQLite persistence for Outreach: contacts, drafts, the send queue, replies, do-not-contact, company preferences.
`tick()` is the worker step: schedule, check replies, send at most one email. The web app and the scheduled script
both call it; every rule it applies lives in outreach.rules."""
from __future__ import annotations

import json
import random
import re
from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlmodel import Session, col, select

from finder.models import now_iso
from finder.resolve import cache_key
from finder.scoring import found_email_score
from jobsearch.dedupe import norm_company
from outreach import rules
from outreach.classify import classify
from outreach.contacts import Suggestion, suggest
from outreach.mail import GmailConfig, Inbox, Sender, compose, since_days

from .db import engine
from .jobs_repo import PROFILE_FIELDS, JobRepo
from .tables import (ApplicationRow, CompanyDomainRow, CompanyPrefRow, ContactRow, DoNotContactRow, FoundEmailRow, JobRow,
                     MailEventRow, MetaRow, OutboxRow, WatchCompanyRow)

REPLY_CHECK_MINUTES = 5


def local_now() -> datetime:
    return datetime.now().astimezone()


def _iso(dt: datetime) -> str:
    return dt.astimezone().isoformat(timespec="seconds")


def _parse(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value).astimezone() if value else None


class OutreachRepo:
    def __init__(self):
        self.jobs = JobRepo()

    # -- meta ---------------------------------------------------------------------------------------
    def _meta(self, key: str) -> Optional[str]:
        with Session(engine) as s:
            row = s.get(MetaRow, f"outreach_{key}")
            return row.value if row else None

    def _set_meta(self, key: str, value: Optional[str]) -> None:
        with Session(engine) as s:
            row = s.get(MetaRow, f"outreach_{key}") or MetaRow(key=f"outreach_{key}")
            row.value = value or ""
            s.add(row)
            s.commit()

    def paused(self) -> Optional[str]:
        return self._meta("paused") or None

    def pause(self, reason: str) -> None:
        self._set_meta("paused", reason)
        self._event(None, "paused", reason)

    def resume(self) -> None:
        self._set_meta("paused", "")
        self._event(None, "resumed", "you resumed sending")

    def _event(self, contact_id: Optional[int], kind: str, detail: str = "", message_id: str = "",
               at: Optional[datetime] = None) -> None:
        with Session(engine) as s:
            s.add(MailEventRow(at=_iso(at) if at else _iso(local_now()), contact_id=contact_id, kind=kind,
                               detail=detail[:500], message_id=message_id))
            s.commit()

    def events(self, contact_id: int) -> list[MailEventRow]:
        with Session(engine) as s:
            return list(s.exec(select(MailEventRow).where(MailEventRow.contact_id == contact_id).order_by(col(MailEventRow.id).desc())))

    # -- company preferences ---------------------------------------------------------------------------
    def prefs(self, company: str) -> CompanyPrefRow:
        key = norm_company(company)
        with Session(engine) as s:
            return s.get(CompanyPrefRow, key) or CompanyPrefRow(company_key=key, company=company)

    def set_prefs(self, company: str, **values) -> CompanyPrefRow:
        key = norm_company(company)
        with Session(engine) as s:
            row = s.get(CompanyPrefRow, key) or CompanyPrefRow(company_key=key, company=company)
            for k, v in values.items():
                setattr(row, k, bool(v))
            if not row.small_startup:
                row.ok_unpaid = False  # only small startups can take unpaid interns
            s.add(row)
            s.commit()
            s.refresh(row)
            return row

    # -- people from the Finder -----------------------------------------------------------------------
    def company_domain(self, company: str) -> Optional[str]:
        with Session(engine) as s:
            for w in s.exec(select(WatchCompanyRow)):
                if w.domain and norm_company(w.name) == norm_company(company):
                    return w.domain
            row = s.get(CompanyDomainRow, cache_key(company))
            return row.domain if row and row.domain else None

    def people(self, domain: str) -> list[dict]:
        with Session(engine) as s:
            rows = list(s.exec(select(FoundEmailRow).where(FoundEmailRow.domain == domain)))
        best: dict[str, dict] = {}
        for r in rows:
            score = found_email_score(r.kind, bool(r.full_name))
            cur = best.get(r.email)
            if cur is None or score > cur["confidence"] or (r.title and not cur["title"]):
                best[r.email] = {"email": r.email, "name": r.full_name or "", "title": r.title or "",
                                 "department": r.department or "", "kind": r.kind, "confidence": score,
                                 "evidence": "seen" if r.full_name else "seen_no_name", "source_url": r.source_url}
        return list(best.values())

    def do_not_contact(self) -> set[str]:
        with Session(engine) as s:
            return {r.email for r in s.exec(select(DoNotContactRow))}

    def suggestions(self, app_id: int) -> tuple[Optional[str], list[Suggestion]]:
        with Session(engine) as s:
            app = s.get(ApplicationRow, app_id)
            job = s.get(JobRow, app.job_id)
            picked = {c.email for c in s.exec(select(ContactRow).where(ContactRow.application_id == app_id))}
        domain = self.company_domain(job.company)
        if not domain:
            return None, []
        pref = self.prefs(job.company)
        return domain, suggest(self.people(domain), job.title, pref.small_startup, exclude=picked | self.do_not_contact())

    # -- contacts ---------------------------------------------------------------------------------------
    def pick(self, app_id: int, email: str) -> ContactRow:
        domain, sugg = self.suggestions(app_id)
        person = next((p for p in sugg if p.email == email), None)
        if person is None and domain:  # picked from the full Finder list rather than the suggestions
            found = next((p for p in self.people(domain) if p["email"] == email), None)
            person = found and Suggestion(found["email"], found["name"], found["title"], found["department"], found["kind"],
                                          found["confidence"], found["evidence"], found["source_url"], "picked")
        if person is None:
            raise ValueError("that address isn't in the Finder results for this company")
        if email.lower() in self.do_not_contact():
            raise ValueError("that person is on your do-not-contact list")
        with Session(engine) as s:
            app = s.get(ApplicationRow, app_id)
            job = s.get(JobRow, app.job_id)
            row = ContactRow(application_id=app_id, company=job.company, name=person.name, email=person.email,
                             title=person.title, department=person.department, kind=person.kind,
                             confidence=person.confidence, evidence=person.evidence, source_url=person.source_url,
                             created_at=now_iso(), updated_at=now_iso())
            s.add(row)
            s.commit()
            s.refresh(row)
        self._event(row.id, "picked", f"picked from Finder ({person.reason})")
        return row

    def pick_manual(self, app_id: int, email: str, name: str = "", title: str = "", department: str = "",
                    confidence: int = 0, evidence: str = "pattern", source_url: str = "") -> ContactRow:
        """A person you pasted or typed in. Pattern guesses below 80 need your OK before they can be queued."""
        email = email.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", email):
            raise ValueError("that isn't an email address")
        if email in self.do_not_contact():
            raise ValueError("that person is on your do-not-contact list")
        with Session(engine) as s:
            if s.exec(select(ContactRow).where(ContactRow.application_id == app_id, ContactRow.email == email)).first():
                raise ValueError("already picked for this application")
            app = s.get(ApplicationRow, app_id)
            job = s.get(JobRow, app.job_id)
            row = ContactRow(application_id=app_id, company=job.company, name=name.strip(), email=email, title=title.strip(),
                             department=department, kind="personal", confidence=int(confidence), evidence=evidence,
                             source_url=source_url, created_at=now_iso(), updated_at=now_iso())
            s.add(row)
            app.needs_person = False
            s.add(app)
            s.commit()
            s.refresh(row)
        self._event(row.id, "picked", f"added by you ({evidence}, confidence {confidence})")
        return row

    def paste_people(self, app_id: int, text: str, verifier=None) -> list[dict]:
        """Names + titles from pasted LinkedIn text -> Find Email with the company's pattern -> suggestions."""
        with Session(engine) as s:
            app = s.get(ApplicationRow, app_id)
            job = s.get(JobRow, app.job_id)
        return self._paste(job.company, self.company_domain(job.company), text, verifier)

    def _paste(self, company: str, domain: Optional[str], text: str, verifier=None) -> list[dict]:
        from finder.find import BadName, find_email
        from outreach.paste import parse_people
        from .repo import SqlStore
        out = []
        for p in parse_people(text, company)[:40]:
            item = {"name": p.name, "title": p.title, "department": p.department, "email": "", "confidence": 0,
                    "evidence": "", "why": ""}
            if domain:
                try:
                    res = find_email(p.name, domain, SqlStore(), verifier=verifier, verify=False)
                except (BadName, ValueError):
                    res = None
                if res and res.best:
                    item.update(email=res.best.email, confidence=res.best.score,
                                evidence="seen" if res.best.evidence.startswith("seen") else "pattern", why=res.best.why)
            out.append(item)
        return out

    # -- cold pitches: a company with no posting ------------------------------------------------------------
    def cold_company(self, key: str):
        from .companies_repo import CompanyRepo
        from .tables import CompanyProfileRow
        with Session(engine) as s:
            row = s.get(CompanyProfileRow, key)
        if row is None:
            return None, None
        return row, row.domain or self.company_domain(row.name)

    def cold_suggestions(self, key: str) -> tuple[Optional[str], list[Suggestion]]:
        from outreach.cold import suggest_cold
        row, domain = self.cold_company(key)
        if not row or not domain:
            return domain, []
        picked = {c.email for c in self.cold_contacts(key)}
        return domain, suggest_cold(self.people(domain), exclude=picked | self.do_not_contact(), limit=6)

    def cold_contacts(self, key: Optional[str] = None) -> list[ContactRow]:
        with Session(engine) as s:
            stmt = select(ContactRow).where(ContactRow.mode == "cold").order_by(col(ContactRow.updated_at).desc())
            if key is not None:
                stmt = stmt.where(ContactRow.company_key == key)
            return list(s.exec(stmt))

    def pick_cold(self, key: str, email: str, name: str = "", title: str = "", department: str = "", confidence: int = 0,
                  evidence: str = "", source_url: str = "", kind: str = "personal", how: str = "") -> ContactRow:
        """A person at a company you're cold-pitching. From the Finder (found here by address) or typed/pasted by you."""
        row, domain = self.cold_company(key)
        if row is None:
            raise ValueError("that company isn't in your list")
        email = email.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", email):
            raise ValueError("that isn't an email address")
        if email in self.do_not_contact():
            raise ValueError("that person is on your do-not-contact list")
        found = next((p for p in self.people(domain) if p["email"].lower() == email), None) if domain else None
        if found:
            name, title, department = found["name"] or name, found["title"] or title, found["department"] or department
            kind, confidence, evidence, source_url = found["kind"], found["confidence"], found["evidence"], found["source_url"]
            how = how or "picked from Finder"
        elif not evidence:
            raise ValueError("that address isn't in the Finder results for this company")
        with Session(engine) as s:
            if s.exec(select(ContactRow).where(ContactRow.email == email, ContactRow.company_key == key)).first():
                raise ValueError("already picked for this company")
            c = ContactRow(application_id=0, mode="cold", company_key=key, company=row.name, name=name.strip(), email=email,
                           title=title.strip(), department=department, kind=kind, confidence=int(confidence),
                           evidence=evidence, source_url=source_url, created_at=now_iso(), updated_at=now_iso())
            s.add(c)
            s.commit()
            s.refresh(c)
        self._event(c.id, "picked", f"cold pitch: {how or f'added by you ({evidence}, confidence {confidence})'}")
        return c

    def paste_people_cold(self, key: str, text: str, verifier=None) -> list[dict]:
        row, domain = self.cold_company(key)
        return self._paste(row.name, domain, text, verifier) if row else []

    def unpick(self, contact_id: int) -> bool:
        with Session(engine) as s:
            c = s.get(ContactRow, contact_id)
            if not c or c.status not in ("picked", "draft_ready"):
                return False
            s.delete(c)
            s.commit()
            return True

    def contact(self, contact_id: int) -> Optional[ContactRow]:
        with Session(engine) as s:
            return s.get(ContactRow, contact_id)

    def contacts(self, app_id: Optional[int] = None) -> list[ContactRow]:
        with Session(engine) as s:
            stmt = select(ContactRow).order_by(col(ContactRow.updated_at).desc())
            if app_id is not None:
                stmt = stmt.where(ContactRow.application_id == app_id)
            return list(s.exec(stmt))

    def set_checked(self, contact_id: int, ok: bool = True) -> None:
        with Session(engine) as s:
            c = s.get(ContactRow, contact_id)
            c.checked_ok = ok
            s.add(c)
            s.commit()
        self._event(contact_id, "checked", "you confirmed this address" if ok else "confirmation removed")

    def set_status(self, contact_id: int, status: str, note: str = "") -> ContactRow:
        assert status in rules.STATUSES
        with Session(engine) as s:
            c = s.get(ContactRow, contact_id)
            c.status, c.updated_at = status, now_iso()
            if status == "referred":
                c.referred = True
            s.add(c)
            if status in ("replied", "call_scheduled", "referred", "no_response", "do_not_contact"):
                self._cancel_pending(s, contact_id)
            s.commit()
            s.refresh(c)
        self._event(contact_id, "status", rules.STATUS_LABELS[status] + (f": {note}" if note else ""))
        return c

    def add_dnc(self, email: str, reason: str) -> None:
        with Session(engine) as s:
            if not s.get(DoNotContactRow, email.lower()):
                s.add(DoNotContactRow(email=email.lower(), reason=reason[:300], added_at=now_iso()))
            for c in s.exec(select(ContactRow).where(ContactRow.email == email)):
                c.status = "do_not_contact"
                s.add(c)
                self._cancel_pending(s, c.id)
            s.commit()

    def update_drafts(self, contact_id: int, **fields) -> ContactRow:
        with Session(engine) as s:
            c = s.get(ContactRow, contact_id)
            for k, v in fields.items():
                setattr(c, k, (v or "").strip())
            c.edited = (c.body != c.body_ai or c.followup1 != c.followup1_ai or c.followup2 != c.followup2_ai)
            c.updated_at = now_iso()
            s.add(c)
            for o in s.exec(select(OutboxRow).where(OutboxRow.contact_id == contact_id, OutboxRow.status == "scheduled")):
                o.body = {"first": c.body, "followup1": c.followup1, "followup2": c.followup2}[o.step] + self.signature()
                if o.step == "first":
                    o.subject = c.subject
                s.add(o)
            s.commit()
            s.refresh(c)
            return c

    def auto_pick(self, app_id: int, pitch: bool) -> list[ContactRow]:
        """Autopilot: pick contacts by the quality rules, or mark the application "Needs a person" instead of guessing.
        Pitch: founder / CTO / team lead with a seen address. Normal: recruiter + up to 2 on the team, seen addresses."""
        if self.contacts(app_id):
            return []
        domain, sugg = self.suggestions(app_id)
        good = [p for p in sugg if p.kind == "personal" and p.confidence >= 85 and p.reason != "shared inbox"]
        if pitch:
            from outreach.contacts import LEADER
            lead = re.compile(r"\b(?:cto|chief technology|vp (?:of )?engineering|head of engineering|engineering manager|"
                              r"founder|co-?founder|ceo|team lead|tech lead)\b", re.I)
            people = [p for p in self.people(domain)] if domain else []
            leaders = sorted([p for p in people if p["kind"] == "personal" and p["confidence"] >= 85 and p.get("name")
                              and (lead.search(p.get("title") or "") or LEADER.search(p.get("title") or ""))],
                             key=lambda p: (not re.search(r"cto|chief technology|founder", p.get("title") or "", re.I), p["email"]))
            chosen = [leaders[0]["email"]] if leaders else []
        else:
            chosen = [p.email for p in good][:3]
        picked = []
        for email in chosen:
            try:
                picked.append(self.pick(app_id, email))
            except ValueError:
                continue
        with Session(engine) as s:
            app = s.get(ApplicationRow, app_id)
            app.needs_person = not picked
            s.add(app)
            s.commit()
        return picked

    # -- the AI handoff ----------------------------------------------------------------------------------
    def signature(self) -> str:
        p = self.jobs.profile()
        links = " · ".join(x.replace("https://", "").replace("http://", "") for x in (p.linkedin_url, p.resume_link) if x)
        from applications.export import format_phone
        line2 = " · ".join(x for x in (f"{p.school} '{p.grad_year[-2:]}" if p.school and p.grad_year else p.school,
                                         format_phone(p.phone)) if x)
        return "\n\nThanks,\n" + "\n".join(x for x in (p.name, line2, links) if x)

    def _contact_dict(self, c: ContactRow) -> dict:
        cold = c.mode == "cold"
        with Session(engine) as s:
            app = None if cold else s.get(ApplicationRow, c.application_id)
            job = s.get(JobRow, app.job_id) if app else None
        pref = self.prefs(c.company)
        from jobsearch.flags import is_pitch
        pitch = bool(job) and is_pitch(json.loads(job.flags_json or "[]"))
        from .companies_repo import CompanyRepo
        from .tables import CompanyProfileRow
        with Session(engine) as s:
            profile = s.get(CompanyProfileRow, c.company_key) if c.company_key else None
        profile = profile or CompanyRepo().get(c.company)
        return {
            "mode": "cold" if cold else ("pitch" if pitch else "normal"),
            "company_size": profile.employees if profile else None,
            "company_local": bool(profile and profile.local),
            "company_one_liner": profile.one_liner if profile else "",
            "company_about": profile.about if profile else "",
            "company_website": (profile.website or profile.domain) if profile else "",
            "company_location": profile.location if profile else "",
            "contact_id": c.id, "name": c.name, "first_name": (c.name or "").split(" ")[0], "email": c.email,
            "title": c.title, "department": c.department, "found_at": c.source_url, "status": c.status,
            "company": c.company,
            "company_prefs": {"small_startup": pref.small_startup, "ok_to_offer_unpaid": pref.small_startup and pref.ok_unpaid,
                              "reach_out_before_applying": pref.reach_out_first},
            "role": {"job_id": job.id, "title": job.title, "applied": app.status in ("applied", "interview", "offer"),
                     "applied_on": app.applied_at, "posting_url": job.url,
                     "description": (job.description or "")[:2500]} if job else None,
            "current": {"subject": c.subject, "email": c.body, "followup1": c.followup1, "followup2": c.followup2,
                        "linkedin_note": c.linkedin_note, "hook": c.hook, "hook_source": c.hook_source,
                        "edited_by_you": c.edited, "call_prep": c.call_prep, "thanks": c.thanks,
                        "reply": c.reply_snippet} if c.drafts_version or c.call_prep or c.thanks else None,
            "earlier_feedback": [h.get("feedback") for h in json.loads(c.history_json) if h.get("feedback")],
        }

    def _you(self) -> dict:
        p = self.jobs.profile()
        resume = self.jobs.default_resume()
        return {**{f: getattr(p, f) for f in PROFILE_FIELDS}, "student": self.jobs.student_summary(),
                "resume_text": resume.text if resume else "",
                "signature_added_automatically": self.signature().strip()}

    def pending(self, limit: int = 5) -> dict:
        with Session(engine) as s:
            rows = list(s.exec(select(ContactRow).where(ContactRow.status == "picked", ContactRow.drafts_version == 0)
                               .order_by(ContactRow.id)))
        return {"kind": "outreach", "pending_total": len(rows), "you": self._you(),
                "contacts": [self._contact_dict(c) for c in rows[:limit]]}

    def show(self, contact_id: int) -> Optional[dict]:
        c = self.contact(contact_id)
        return {"kind": "contact", "you": self._you(), "contact": self._contact_dict(c)} if c else None

    def known_for_drafts(self, any_status: bool = False) -> dict[int, dict]:
        with Session(engine) as s:
            rows = list(s.exec(select(ContactRow)))
        out = {}
        for c in rows:
            if c.status == "do_not_contact" or (not any_status and c.status not in ("picked", "draft_ready", "approved")):
                continue
            pref = self.prefs(c.company)
            with Session(engine) as s:
                app = s.get(ApplicationRow, c.application_id)
                job = s.get(JobRow, app.job_id) if app else None
            from jobsearch.flags import is_pitch
            out[c.id] = {"first_name": (c.name or "").split(" ")[0], "unpaid_ok": pref.small_startup and pref.ok_unpaid,
                         "pitch": bool(job and is_pitch(json.loads(job.flags_json or "[]"))), "cold": c.mode == "cold"}
        return out

    def save_drafts(self, items: list[dict], feedback: str = "") -> int:
        with Session(engine) as s:
            for it in items:
                c = s.get(ContactRow, it["contact_id"])
                if c.drafts_version:
                    history = json.loads(c.history_json)
                    history.append({"version": c.drafts_version, "subject": c.subject, "email": c.body,
                                    "followup1": c.followup1, "followup2": c.followup2, "linkedin_note": c.linkedin_note,
                                    "hook": c.hook, "replaced_at": now_iso(), "feedback": feedback or None})
                    c.history_json = json.dumps(history[-10:])
                c.hook, c.hook_source, c.hook_level = it["hook"], it["hook_source"], it["hook_level"]
                c.subject = it["subject"]
                c.body = c.body_ai = it["email"]
                c.followup1 = c.followup1_ai = it["followup1"]
                c.followup2 = c.followup2_ai = it["followup2"]
                c.linkedin_note, c.ai_notes = it["linkedin_note"], it.get("notes", "")
                c.edited, c.drafts_version, c.updated_at = False, c.drafts_version + 1, now_iso()
                if c.status == "picked":
                    c.status = "draft_ready"
                if c.status == "approved":  # a redraft of an approved, unsent email needs approving again
                    c.status = "draft_ready"
                    self._cancel_pending(s, c.id)
                s.add(c)
                s.add(MailEventRow(at=now_iso(), contact_id=c.id, kind="drafts",
                                   detail=f"drafts v{c.drafts_version} ({it.get('words', '?')} words)" + (f": {feedback}" if feedback else "")))
            s.commit()
        return len(items)

    def save_text(self, field_name: str, items: list[dict]) -> int:
        with Session(engine) as s:
            for it in items:
                c = s.get(ContactRow, it["contact_id"])
                setattr(c, field_name, it[field_name])
                c.updated_at = now_iso()
                s.add(c)
                s.add(MailEventRow(at=now_iso(), contact_id=c.id, kind=field_name, detail="call prep written" if field_name == "call_prep" else "thank-you drafted"))
            s.commit()
        return len(items)

    # -- approval + queue ------------------------------------------------------------------------------------
    def approve(self, contact_id: int) -> ContactRow:
        """Approves the email AND its two follow-ups (each follow-up only sends if there's been no reply)."""
        c = self.contact(contact_id)
        if c.email.lower() in self.do_not_contact():
            raise ValueError("this person is on your do-not-contact list")
        if c.status != "draft_ready":
            raise ValueError("only a contact with drafts ready can be approved")
        ok, why = rules.queueable(c.confidence, c.kind, c.checked_ok)
        if not ok:
            raise ValueError(why)
        with Session(engine) as s:
            c = s.get(ContactRow, contact_id)
            c.status, c.approved_at, c.updated_at = "approved", now_iso(), now_iso()
            s.add(c)
            s.commit()
        self._event(contact_id, "approved", "approved the email and both follow-ups")
        self.schedule_ready()
        return self.contact(contact_id)

    def _cancel_pending(self, s: Session, contact_id: int) -> None:
        for o in s.exec(select(OutboxRow).where(OutboxRow.contact_id == contact_id, OutboxRow.status == "scheduled")):
            o.status = "cancelled"
            s.add(o)

    def schedule_ready(self, now: Optional[datetime] = None, rng: Optional[random.Random] = None) -> int:
        """Queue first emails for approved contacts once the order-of-operations rule allows it."""
        now = now or local_now()
        n = 0
        with Session(engine) as s:
            for c in s.exec(select(ContactRow).where(ContactRow.status == "approved")):
                if s.exec(select(OutboxRow).where(OutboxRow.contact_id == c.id, OutboxRow.step == "first",
                                                  OutboxRow.status.in_(["scheduled", "sent"]))).first():
                    continue
                app = s.get(ApplicationRow, c.application_id) if c.mode != "cold" else None
                pref = s.get(CompanyPrefRow, norm_company(c.company))
                applied = date.fromisoformat(app.applied_at[:10]) if app and app.applied_at else None
                first = c.mode == "cold" or bool(pref and pref.reach_out_first)  # a cold pitch has nothing to apply to
                day = rules.first_send_not_before(applied, first, now.date(), rng)
                if day is None:
                    continue  # waits until you mark the application applied
                start = rules.next_window_start(max(now, datetime.combine(day, rules.SEND_START, now.tzinfo)))
                s.add(OutboxRow(contact_id=c.id, step="first", to_addr=c.email, subject=c.subject,
                                body=c.body + self.signature(), not_before=_iso(start)))
                n += 1
            s.commit()
        return n

    def _schedule_followup(self, s: Session, c: ContactRow, step: str, after: date) -> None:
        fu1, _ = rules.followup_dates(after)
        day = fu1 if step == "followup1" else after + timedelta(days=rules.FOLLOWUP2_DAYS)
        when = rules.next_window_start(datetime.combine(day, rules.SEND_START).astimezone())
        body = (c.followup1 if step == "followup1" else c.followup2) + self.signature()
        ids = json.loads(c.thread_message_ids_json)
        s.add(OutboxRow(contact_id=c.id, step=step, to_addr=c.email, subject=self._re(c.subject), body=body,
                        not_before=_iso(when), in_reply_to=ids[-1] if ids else ""))

    @staticmethod
    def _re(subject: str) -> str:
        return subject if subject.lower().startswith("re:") else f"Re: {subject}"

    # -- the worker ----------------------------------------------------------------------------------------
    def counts_today(self, now: datetime) -> dict:
        start = datetime.combine(now.date(), time(0), now.tzinfo)
        with Session(engine) as s:
            sent = list(s.exec(select(OutboxRow).where(OutboxRow.status == "sent")))
            events = list(s.exec(select(MailEventRow).where(MailEventRow.kind == "bounce")))
        sent_today = [o for o in sent if _parse(o.sent_at) and _parse(o.sent_at) >= start and o.step != "test"]
        last = max((_parse(o.sent_at) for o in sent if o.sent_at), default=None)
        first = min((_parse(o.sent_at).date() for o in sent if o.sent_at and o.step != "test"), default=None)
        with Session(engine) as s:
            pauses = [_parse(e.at).date() for e in s.exec(select(MailEventRow).where(MailEventRow.kind == "paused"))
                      if "automatically" in (e.detail or "")]
        if first and pauses:
            first = max([first] + pauses)  # an automatic bounce pause restarts the ramp
        with Session(engine) as s:
            pauses = [_parse(e.at).date() for e in s.exec(select(MailEventRow).where(MailEventRow.kind == "paused"))
                      if "automatically" in (e.detail or "")]
        if first and pauses:
            first = max([first] + pauses)  # an automatic bounce pause restarts the ramp
        bounces_today = sum(1 for e in events if _parse(e.at) and _parse(e.at) >= start)
        return {"sent_today": len(sent_today), "last_sent": last, "first_send": first,
                "total_sent": sum(1 for o in sent if o.step != "test"), "total_bounces": len(events),
                "bounces_today": bounces_today}

    def today(self, now: Optional[datetime] = None) -> dict:
        now = now or local_now()
        k = self.counts_today(now)
        cap = rules.daily_cap(k["first_send"], now.date())
        end_today = datetime.combine(now.date(), time(23, 59), now.tzinfo)
        with Session(engine) as s:
            queued = list(s.exec(select(OutboxRow).where(OutboxRow.status == "scheduled")))
            needs_me = list(s.exec(select(ContactRow).where(ContactRow.status == "replied")))
        return {"cap": cap, "sent_today": k["sent_today"], "left_today": max(0, cap - k["sent_today"]),
                "queued": len(queued), "replies": len(needs_me),
                "followups_today": sum(1 for o in queued if o.step != "first" and _parse(o.not_before) <= end_today),
                "paused": self.paused(), "configured": GmailConfig().configured,
                "in_window": rules.in_send_window(now), "next": min((o.not_before for o in queued), default=None)}

    def tick(self, now: Optional[datetime] = None, sender: Optional[Sender] = None, inbox: Optional[Inbox] = None,
             rng: Optional[random.Random] = None, check_replies: Optional[bool] = None) -> dict:
        """One worker step: schedule, (maybe) check replies, send at most one due email, close out quiet threads."""
        now = now or local_now()
        rng = rng or random.Random()
        cfg = GmailConfig()
        result = {"scheduled": self.schedule_ready(now, rng), "replies": 0, "sent": None, "reason": ""}
        if cfg.configured or inbox:
            last = _parse(self._meta("last_check"))
            due = check_replies if check_replies is not None else (not last or now - last >= timedelta(minutes=REPLY_CHECK_MINUTES))
            if due:
                result["replies"] = self.check_replies(inbox or Inbox(cfg), now)
                self._set_meta("last_check", _iso(now))
        self._close_quiet(now)
        if not (cfg.configured or sender):
            result["reason"] = "Gmail isn't set up"
            return result
        k = self.counts_today(now)
        gap = timedelta(seconds=int(self._meta("next_gap") or rules.random_gap(rng).total_seconds()))
        decision = rules.can_send(now, k["sent_today"], k["first_send"], k["last_sent"], bool(self.paused()), gap)
        if not decision.ok:
            result["reason"] = decision.reason
            return result
        with Session(engine) as s:
            due = [o for o in s.exec(select(OutboxRow).where(OutboxRow.status == "scheduled", OutboxRow.step != "test"))
                   if _parse(o.not_before) <= now]
        due.sort(key=lambda o: (o.step != "first", o.not_before))  # follow-ups after first emails at equal times
        if not due:
            result["reason"] = "nothing due"
            return result
        result["sent"] = self._send(due[0].id, sender or Sender(cfg), now)
        self._set_meta("next_gap", str(int(rules.random_gap(rng).total_seconds())))
        return result

    def _send(self, outbox_id: int, sender: Sender, now: datetime) -> Optional[int]:
        cfg = sender.cfg
        with Session(engine) as s:
            o = s.get(OutboxRow, outbox_id)
            c = s.get(ContactRow, o.contact_id) if o.contact_id else None
            if c and (c.email.lower() in self.do_not_contact() or c.status in ("replied", "call_scheduled", "referred", "do_not_contact")):
                o.status = "cancelled"
                s.add(o)
                s.commit()
                return None
            if c and c.paused_until and date.fromisoformat(c.paused_until) > now.date():
                o.not_before = _iso(rules.next_window_start(datetime.combine(date.fromisoformat(c.paused_until), rules.SEND_START, now.tzinfo)))
                s.add(o)
                s.commit()
                return None
            refs = json.loads(c.thread_message_ids_json) if c else []
            msg = compose(cfg, o.to_addr, o.subject, o.body, in_reply_to=o.in_reply_to, references=refs)
            try:
                message_id = sender.send(msg)
            except Exception as e:  # leave it scheduled; record why
                o.error = f"{type(e).__name__}: {e}"[:300]
                s.add(o)
                s.add(MailEventRow(at=now_iso(), contact_id=o.contact_id, kind="error", detail=o.error))
                s.commit()
                return None
            o.status, o.sent_at, o.message_id, o.error = "sent", _iso(now), message_id, ""
            s.add(o)
            if c:
                c.thread_message_ids_json = json.dumps(refs + [message_id])
                if o.step == "first":
                    c.status, c.sent_at = "sent", _iso(now)
                    self._schedule_followup(s, c, "followup1", now.date())
                elif o.step == "followup1":
                    c.status = "followup1"
                    self._schedule_followup(s, c, "followup2", now.date())
                else:
                    c.status = "followup2"
                c.updated_at = now_iso()
                s.add(c)
            s.add(MailEventRow(at=_iso(now), contact_id=o.contact_id, kind="sent",
                               detail={"first": "first email", "followup1": "follow-up 1", "followup2": "follow-up 2",
                                       "test": f"test email to {o.to_addr}"}[o.step], message_id=message_id))
            s.commit()
            return o.id

    def _close_quiet(self, now: datetime) -> None:
        """Follow-up 2 went out a week ago and nobody answered: No response."""
        with Session(engine) as s:
            for c in s.exec(select(ContactRow).where(ContactRow.status == "followup2")):
                last = s.exec(select(OutboxRow).where(OutboxRow.contact_id == c.id, OutboxRow.step == "followup2",
                                                      OutboxRow.status == "sent")).first()
                if last and _parse(last.sent_at) + timedelta(days=rules.NO_RESPONSE_DAYS) <= now:
                    c.status = "no_response"
                    s.add(c)
            s.commit()

    # -- replies --------------------------------------------------------------------------------------------
    def check_replies(self, inbox: Inbox, now: Optional[datetime] = None) -> int:
        now = now or local_now()
        with Session(engine) as s:
            contacts = [c for c in s.exec(select(ContactRow)) if json.loads(c.thread_message_ids_json)]
            tests = [o.message_id for o in s.exec(select(OutboxRow).where(OutboxRow.step == "test", OutboxRow.status == "sent"))]
        by_id = {mid: c.id for c in contacts for mid in json.loads(c.thread_message_ids_json)}
        by_addr = {c.email.lower(): c.id for c in contacts}
        seen = {e.message_id for e in self._all_events() if e.message_id}
        handled = 0
        for inc in inbox.fetch_related(set(by_id) | set(tests), set(by_addr), since_days(45)):
            if inc.message_id in seen or inc.message_id in by_id or inc.message_id in tests:
                continue  # already handled, or a copy of something we sent
            if inc.in_reply_to in tests or any(r in tests for r in inc.references):
                self._event(None, "reply", f"reply to the test email from {inc.from_addr}: {inc.body[:120]}", inc.message_id, at=now)
                handled += 1
                continue
            cid = by_id.get(inc.in_reply_to) or next((by_id[r] for r in inc.references if r in by_id), None) \
                or by_addr.get(inc.from_addr) or next((v for k, v in by_id.items() if k.strip("<>") in inc.body), None)
            if cid is None:
                continue
            self._apply_reply(cid, inc, now)
            handled += 1
        return handled

    def _all_events(self) -> list[MailEventRow]:
        with Session(engine) as s:
            return list(s.exec(select(MailEventRow)))

    def _apply_reply(self, contact_id: int, inc, now: datetime) -> None:
        got = classify(inc.from_addr, inc.subject, inc.body, inc.headers, now.date())
        with Session(engine) as s:
            c = s.get(ContactRow, contact_id)
            if got.kind == "bounce":
                c.reply_kind, c.status = "bounce", "no_response"
                self._cancel_pending(s, c.id)
            elif got.kind == "out_of_office":
                back = got.return_date or (now.date() + timedelta(days=7))
                c.paused_until = (back + timedelta(days=2)).isoformat()
                c.reply_kind, c.reply_snippet = "out_of_office", got.snippet
                for o in s.exec(select(OutboxRow).where(OutboxRow.contact_id == c.id, OutboxRow.status == "scheduled")):
                    if _parse(o.not_before).date() < date.fromisoformat(c.paused_until):
                        o.not_before = _iso(rules.next_window_start(datetime.combine(date.fromisoformat(c.paused_until), rules.SEND_START, now.tzinfo)))
                        s.add(o)
            elif got.kind == "stop":
                c.reply_kind, c.reply_snippet, c.status = "stop", got.snippet, "do_not_contact"
                self._cancel_pending(s, c.id)
                if not s.get(DoNotContactRow, c.email.lower()):
                    s.add(DoNotContactRow(email=c.email.lower(), reason=f"asked to stop: {got.snippet[:200]}", added_at=now_iso()))
            else:
                c.reply_kind, c.reply_snippet, c.replied_at, c.status = "reply", got.snippet, _iso(now), "replied"
                self._cancel_pending(s, c.id)
            c.updated_at = now_iso()
            s.add(c)
            s.add(MailEventRow(at=_iso(now), contact_id=c.id, kind=got.kind, detail=got.snippet or got.kind, message_id=inc.message_id))
            s.commit()
        if got.kind == "bounce":
            k = self.counts_today(now)
            reason = rules.bounce_pause_reason(k["bounces_today"], k["total_sent"], k["total_bounces"])
            if reason and not self.paused():
                self.pause(f"Sending paused automatically: {reason}. Check the addresses, then resume.")

    # -- test email ---------------------------------------------------------------------------------------------
    def send_test(self, sender: Optional[Sender] = None, now: Optional[datetime] = None) -> OutboxRow:
        cfg = (sender.cfg if sender else GmailConfig())
        now = now or local_now()
        with Session(engine) as s:
            o = OutboxRow(step="test", to_addr=cfg.address, subject="jobhunt test email",
                          body="This is a test from jobhunt on your computer. If you reply to it, the Outreach page "
                               "should show that the reply was detected." + self.signature(), not_before=_iso(now))
            s.add(o)
            s.commit()
            s.refresh(o)
        self._send(o.id, sender or Sender(cfg), now)
        with Session(engine) as s:
            return s.get(OutboxRow, o.id)

    def test_status(self) -> dict:
        with Session(engine) as s:
            test = s.exec(select(OutboxRow).where(OutboxRow.step == "test").order_by(col(OutboxRow.id).desc())).first()
            replies = [e for e in s.exec(select(MailEventRow).where(MailEventRow.kind == "reply", MailEventRow.contact_id == None))]  # noqa: E711
        sent = _parse(test.sent_at) if test and test.sent_at else None
        return {"test": test, "reply_seen": bool(sent and any(_parse(e.at) >= sent for e in replies))}

    def outbox(self, contact_id: int) -> list[OutboxRow]:
        with Session(engine) as s:
            return list(s.exec(select(OutboxRow).where(OutboxRow.contact_id == contact_id).order_by(OutboxRow.id)))

    def apply_by(self, app_id: int) -> Optional[str]:
        """'Reach out before applying': apply within 5 days of the first email."""
        with Session(engine) as s:
            app = s.get(ApplicationRow, app_id)
            if not app or app.applied_at:
                return None
            first = [c.sent_at for c in s.exec(select(ContactRow).where(ContactRow.application_id == app_id)) if c.sent_at]
        return rules.apply_by(min(_parse(x) for x in first).date()).isoformat() if first else None

    def referred_apps(self) -> set[int]:
        with Session(engine) as s:
            return {c.application_id for c in s.exec(select(ContactRow).where(ContactRow.referred == True))  # noqa: E712
                    if c.application_id}
