"""SQLite persistence for Applications: the pipeline, drafts, AI scores, the answer bank, and the pending-work
bundles that the Claude Code commands read through `python -m applications`."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Optional

from sqlmodel import Session, col, select

from applications.status import REMOVABLE, StatusError, check_move, follow_up_date, follow_up_due
from finder.models import now_iso
from jobsearch.flags import eligible_for_high_schooler
from jobsearch.models import JOB_TYPE_LABELS, WORK_MODE_LABELS, Pay
from jobsearch.normalize import pay_label

from .db import engine
from .jobs_repo import PROFILE_FIELDS, JobRepo
from .tables import (AnswerRow, ApplicationEventRow, ApplicationRow, ContactRow, JobRow, SavedSearchRow, SearchJobRow)

DESCRIPTION_LIMIT = 4000  # characters per job sent to the AI commands (keeps runs light on usage)

FIXED_ANSWERS = [
    ("work_authorization", "Are you legally authorized to work in the US? Will you now or in the future need sponsorship?"),
    ("availability", "When can you start? What is your availability?"),
    ("hours", "How many hours per week can you work?"),
    ("relocation", "Are you willing to relocate?"),
    ("heard_about", "How did you hear about this position?"),
    ("salary", "What are your salary expectations?"),
]


class AppRepo:
    def __init__(self):
        self.jobs = JobRepo()

    # -- answer bank ------------------------------------------------------------------------
    def seed_answers(self) -> None:
        with Session(engine) as s:
            have = {r.key for r in s.exec(select(AnswerRow))}
            for i, (key, q) in enumerate(FIXED_ANSWERS):
                if key not in have:
                    s.add(AnswerRow(key=key, question=q, position=i, fixed=True))
            s.commit()

    def answers(self) -> list[AnswerRow]:
        self.seed_answers()
        with Session(engine) as s:
            return list(s.exec(select(AnswerRow).order_by(AnswerRow.position, AnswerRow.id)))

    def save_answers(self, values: dict[int, tuple[str, str]]) -> None:
        """{answer id: (question, answer)}. Fixed questions keep their wording."""
        with Session(engine) as s:
            for row in s.exec(select(AnswerRow)):
                if row.id in values:
                    q, a = values[row.id]
                    if not row.fixed and q.strip():
                        row.question = q.strip()
                    row.answer = a.strip()
                    s.add(row)
            s.commit()

    def add_answer(self, question: str, answer: str) -> AnswerRow:
        with Session(engine) as s:
            n = len(list(s.exec(select(AnswerRow))))
            row = AnswerRow(key=f"custom-{datetime.now().timestamp():.0f}-{n}", question=question.strip(),
                            answer=answer.strip(), position=100 + n)
            s.add(row)
            s.commit()
            s.refresh(row)
            return row

    def delete_answer(self, answer_id: int) -> None:
        with Session(engine) as s:
            row = s.get(AnswerRow, answer_id)
            if row and not row.fixed:
                s.delete(row)
                s.commit()

    # -- pipeline ---------------------------------------------------------------------------
    def _event(self, s: Session, app: ApplicationRow, kind: str, note: str = "", frm=None, to=None) -> None:
        s.add(ApplicationEventRow(application_id=app.id, at=now_iso(), kind=kind, from_status=frm, to_status=to, note=note))

    def for_job(self, job_id: int) -> Optional[ApplicationRow]:
        with Session(engine) as s:
            return s.exec(select(ApplicationRow).where(ApplicationRow.job_id == job_id)).first()

    def get(self, app_id: int) -> Optional[ApplicationRow]:
        with Session(engine) as s:
            return s.get(ApplicationRow, app_id)

    def statuses_by_job(self) -> dict[int, str]:
        with Session(engine) as s:
            return {a.job_id: a.status for a in s.exec(select(ApplicationRow))}

    def ensure(self, job_id: int) -> ApplicationRow:
        """Saving a job puts it in the pipeline as Saved (or brings back a withdrawn one)."""
        with Session(engine) as s:
            app = s.exec(select(ApplicationRow).where(ApplicationRow.job_id == job_id)).first()
            if app is None:
                app = ApplicationRow(job_id=job_id, status="saved", created_at=now_iso(), updated_at=now_iso())
                s.add(app)
                s.flush()
                self._event(s, app, "status", "saved from Jobs", None, "saved")
            s.commit()
            s.refresh(app)
            return app

    def unsave(self, job_id: int) -> bool:
        """Unsaving removes the application only if nothing's been approved or sent yet."""
        with Session(engine) as s:
            app = s.exec(select(ApplicationRow).where(ApplicationRow.job_id == job_id)).first()
            if app is None or app.status not in REMOVABLE:
                return False
            for e in s.exec(select(ApplicationEventRow).where(ApplicationEventRow.application_id == app.id)):
                s.delete(e)
            s.delete(app)
            s.commit()
            return True

    def move(self, app_id: int, new: str, note: str = "", today: Optional[date] = None) -> ApplicationRow:
        today = today or date.today()
        with Session(engine) as s:
            app = s.get(ApplicationRow, app_id)
            if app is None:
                raise StatusError("application not found")
            check_move(app.status, new)
            old = app.status
            app.status, app.updated_at = new, now_iso()
            if new == "approved":
                app.approved_at = now_iso()
            if new == "applied" and old == "approved":
                app.applied_at = today.isoformat()
                app.follow_up_at = follow_up_date(today)
            if new in ("offer", "rejected", "withdrawn"):
                app.follow_up_at = None
            s.add(app)
            self._event(s, app, "status", note, old, new)
            # the job itself stays "saved" while it's in the pipeline
            job = s.get(JobRow, app.job_id)
            if job and job.state != "saved" and new != "withdrawn":
                job.state = "saved"
                s.add(job)
            s.commit()
            s.refresh(app)
            return app

    def update(self, app_id: int, *, cover_letter: Optional[str] = None, notes: Optional[str] = None,
               follow_up_at: Optional[str] = None, clear_follow_up: bool = False) -> ApplicationRow:
        with Session(engine) as s:
            app = s.get(ApplicationRow, app_id)
            if cover_letter is not None:
                text = cover_letter.replace("\r\n", "\n").strip()
                if text != app.cover_letter:
                    app.cover_letter = text
                    edited = bool(app.cover_letter_ai) and text != app.cover_letter_ai
                    if edited and not app.cover_edited:
                        self._event(s, app, "edit", "you edited the cover letter")
                    app.cover_edited = edited
            if notes is not None:
                app.notes = notes.strip()
            if follow_up_at:
                date.fromisoformat(follow_up_at)  # raises on a bad date
                app.follow_up_at = follow_up_at
                self._event(s, app, "follow_up", f"follow-up set for {follow_up_at}")
            if clear_follow_up:
                app.follow_up_at = None
                self._event(s, app, "follow_up", "followed up")
            app.updated_at = now_iso()
            s.add(app)
            s.commit()
            s.refresh(app)
            return app

    def update_table(self, app_id: int, table: dict) -> ApplicationRow:
        with Session(engine) as s:
            app = s.get(ApplicationRow, app_id)
            text = json.dumps(table)
            if text != app.cover_table_json:
                app.cover_table_json = text
                edited = bool(app.cover_table_ai_json) and text != app.cover_table_ai_json
                if edited and not app.table_edited:
                    self._event(s, app, "edit", "you edited the table cover letter")
                app.table_edited = edited
                app.updated_at = now_iso()
                s.add(app)
                s.commit()
                s.refresh(app)
            return app

    def events(self, app_id: int) -> list[ApplicationEventRow]:
        with Session(engine) as s:
            return list(s.exec(select(ApplicationEventRow).where(ApplicationEventRow.application_id == app_id)
                               .order_by(col(ApplicationEventRow.id).desc())))

    def board(self) -> list[tuple[ApplicationRow, JobRow]]:
        with Session(engine) as s:
            return list(s.exec(select(ApplicationRow, JobRow).join(JobRow, JobRow.id == ApplicationRow.job_id)
                               .order_by(col(ApplicationRow.updated_at).desc())))

    def summary(self, today: Optional[date] = None) -> dict:
        today = today or date.today()
        week_start = today - timedelta(days=today.weekday())
        rows = [a for a, _ in self.board()]
        return {
            "applied_this_week": sum(1 for a in rows if a.applied_at and date.fromisoformat(a.applied_at[:10]) >= week_start),
            "awaiting_drafts": sum(1 for a in rows if a.status == "saved"),
            "follow_ups_due": sum(1 for a in rows if follow_up_due(a.follow_up_at, a.status, today)),
            "total": len(rows),
        }

    # -- AI handoff: read ---------------------------------------------------------------------
    def _you(self, with_answers: bool) -> dict:
        p = self.jobs.profile()
        resume = self.jobs.default_resume()
        you = {f: getattr(p, f) for f in PROFILE_FIELDS}
        you["student"] = self.jobs.student_summary()
        you["resume_name"] = resume.label if resume else None
        you["resume_text"] = resume.text if resume else ""
        if with_answers:
            you["answer_bank"] = [{"key": a.key, "question": a.question, "answer": a.answer} for a in self.answers()]
        return you

    def _job_dict(self, job: JobRow, basic: Optional[int]) -> dict:
        flags = json.loads(job.flags_json or "[]")
        pay = Pay(**json.loads(job.pay_json)) if job.pay_json else None
        desc = job.description or ""
        return {
            "job_id": job.id, "title": job.title, "company": job.company, "location": job.location,
            "work_mode": WORK_MODE_LABELS.get(job.work_mode or "", "not stated"),
            "job_type": JOB_TYPE_LABELS.get(job.job_type or "", "not stated"),
            "pay": pay_label(pay) or "not listed", "posted": (job.posted_at or "")[:10] or "unknown",
            "basic_match": basic, "eligible_for_high_schooler": eligible_for_high_schooler(flags),
            "eligible_for_you": eligible_for_high_schooler(flags),  # same check, at your student level
            "pitch_candidate": any(f["key"] == "pitch" for f in flags),
            "company_size": self._company(job.company).get("employees"),
            "company_local": bool(self._company(job.company).get("local")),
            "flags": [{"label": f["label"], "evidence": f["snippet"]} for f in flags],
            "apply_url": job.apply_url or job.url,
            "description": desc[:DESCRIPTION_LIMIT] + (" […cut for length]" if len(desc) > DESCRIPTION_LIMIT else ""),
        }

    def _company(self, name: str) -> dict:
        if not hasattr(self, "_sizes"):
            from .companies_repo import CompanyRepo
            self._sizes = CompanyRepo().size_map()
        from jobsearch.dedupe import norm_company
        return self._sizes.get(norm_company(name), {})

    def _best_basic(self, s: Session) -> dict[int, int]:
        latest = {r.id: r.last_run_id for r in s.exec(select(SavedSearchRow)) if r.last_run_id}
        best: dict[int, int] = {}
        for link in s.exec(select(SearchJobRow)):
            if latest.get(link.search_id) == link.last_run_id:
                best[link.job_id] = max(best.get(link.job_id, 0), link.match_score)
        return best

    def best_basic(self) -> dict[int, int]:
        """Best basic match per job across the searches' latest runs."""
        with Session(engine) as s:
            return self._best_basic(s)

    def pending_scores(self, limit: int = 15) -> dict:
        """Unscored jobs from the latest runs (saved jobs first, then best basic match), not dismissed."""
        with Session(engine) as s:
            best = self._best_basic(s)
            rows = [j for j in s.exec(select(JobRow).where(JobRow.ai_score == None, JobRow.state != "dismissed"))  # noqa: E711
                    if j.id in best or j.state == "saved"]
            rows.sort(key=lambda j: (j.state != "saved", -best.get(j.id, 0)))
            total = len(rows)
            jobs = [self._job_dict(j, best.get(j.id)) for j in rows[:limit]]
        return {"kind": "scores", "pending_total": total, "returned": len(jobs), "you": self._you(False), "jobs": jobs}

    def pending_drafts(self, limit: int = 5) -> dict:
        """Saved applications with no drafts yet."""
        with Session(engine) as s:
            best = self._best_basic(s)
            pairs = list(s.exec(select(ApplicationRow, JobRow).join(JobRow, JobRow.id == ApplicationRow.job_id)
                                .where(ApplicationRow.status == "saved", ApplicationRow.drafts_version == 0)
                                .order_by(ApplicationRow.id)))
            from jobsearch.flags import is_pitch
            said_yes = {c.application_id for c in s.exec(select(ContactRow).where(ContactRow.status.in_(["replied", "call_scheduled", "referred"])))}
            pairs = [(a, j) for a, j in pairs if not is_pitch(json.loads(j.flags_json or "[]")) or a.id in said_yes]
            total = len(pairs)
            jobs = []
            for app, job in pairs[:limit]:
                d = self._job_dict(job, best.get(job.id))
                d["ai_score"], d["ai_reasoning"] = job.ai_score, job.ai_notes
                jobs.append(d)
        return {"kind": "drafts", "pending_total": total, "returned": len(jobs), "you": self._you(True), "jobs": jobs}

    def show(self, job_id: int) -> Optional[dict]:
        with Session(engine) as s:
            job = s.get(JobRow, job_id)
            if not job:
                return None
            app = s.exec(select(ApplicationRow).where(ApplicationRow.job_id == job_id)).first()
            d = self._job_dict(job, self._best_basic(s).get(job_id))
        d["ai_score"], d["ai_reasoning"] = job.ai_score, job.ai_notes
        if app:
            d["application"] = {"status": app.status, "cover_letter": app.cover_letter, "edited_by_you": app.cover_edited,
                                "cover_table": json.loads(app.cover_table_json) if app.cover_table_json else None,
                                "table_edited_by_you": app.table_edited,
                                "answers": json.loads(app.answers_json), "notes": app.ai_notes,
                                "drafts_version": app.drafts_version,
                                "earlier_feedback": [h.get("feedback") for h in json.loads(app.drafts_history_json) if h.get("feedback")]}
        return {"kind": "job", "you": self._you(True), "job": d}

    def saved_job_ids(self) -> set[int]:
        with Session(engine) as s:
            return {a.job_id for a in s.exec(select(ApplicationRow))}

    def all_job_ids(self) -> set[int]:
        with Session(engine) as s:
            return set(s.exec(select(JobRow.id)))

    # -- AI handoff: write ----------------------------------------------------------------------
    def save_scores(self, items: list[dict]) -> int:
        with Session(engine) as s:
            for it in items:
                job = s.get(JobRow, it["job_id"])
                job.ai_score, job.ai_notes = it["score"], it["reasoning"]
                s.add(job)
            s.commit()
        return len(items)

    def save_drafts(self, items: list[dict], feedback: str = "") -> int:
        with Session(engine) as s:
            for it in items:
                app = s.exec(select(ApplicationRow).where(ApplicationRow.job_id == it["job_id"])).first()
                if app.drafts_version:
                    history = json.loads(app.drafts_history_json)
                    history.append({"version": app.drafts_version, "cover_letter": app.cover_letter,
                                    "cover_table": json.loads(app.cover_table_json) if app.cover_table_json else None,
                                    "answers": json.loads(app.answers_json), "replaced_at": now_iso(),
                                    "feedback": feedback or None})
                    app.drafts_history_json = json.dumps(history[-10:])
                app.cover_letter = app.cover_letter_ai = it["cover_letter"]
                app.cover_edited = False
                if it.get("cover_table"):
                    app.cover_table_json = app.cover_table_ai_json = json.dumps(it["cover_table"])
                    app.table_edited = False
                app.answers_json = json.dumps(it["answers"])
                app.ai_notes = it.get("notes", "")
                app.drafts_version += 1
                app.drafted_at = app.updated_at = now_iso()
                note = f"drafts v{app.drafts_version} ({it.get('words', '?')} words)" + (f": {feedback}" if feedback else "")
                self._event(s, app, "drafts", note)
                if app.status == "saved":
                    self._event(s, app, "status", "drafts written", "saved", "drafts_ready")
                    app.status = "drafts_ready"
                s.add(app)
            s.commit()
        return len(items)

