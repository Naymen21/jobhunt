from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from finder.models import VerificationResult


class Verification(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True)
    domain: str = Field(index=True)
    status: str = Field(index=True)
    confidence: int
    result_json: str  # full VerificationResult, so the card can be re-rendered later
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)

    @classmethod
    def from_result(cls, r: VerificationResult) -> "Verification":
        return cls(email=r.email, domain=r.domain, status=r.status, confidence=r.confidence,
                   result_json=json.dumps(r.to_dict()))

    def result(self) -> VerificationResult:
        return VerificationResult.from_dict(json.loads(self.result_json))


# --- Phase 2: domain discovery -------------------------------------------------------

class DomainRow(SQLModel, table=True):
    __tablename__ = "domain"
    domain: str = Field(primary_key=True)
    github_org: Optional[str] = None
    last_searched_at: str
    robots_blocked: bool = False
    sources_json: str = "[]"  # list of SourceStatus dicts from the last search


class FoundEmailRow(SQLModel, table=True):
    __tablename__ = "found_email"
    __table_args__ = (UniqueConstraint("email", "source_url"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    domain: str = Field(index=True)
    email: str = Field(index=True)
    kind: str  # personal | role
    full_name: Optional[str] = None
    name_source: Optional[str] = None
    source_type: str  # website | pdf | github | websearch
    source_url: str
    context: str = ""
    first_seen: str
    last_seen: str
    title: Optional[str] = None
    department: Optional[str] = Field(default=None, index=True)
    seniority: Optional[str] = None


class DomainPatternRow(SQLModel, table=True):
    __tablename__ = "domain_pattern"
    domain: str = Field(primary_key=True)
    template: Optional[str] = None
    support: int = 0
    contradict: int = 0
    total: int = 0
    confidence: int = 0
    result_json: str  # full PatternResult (breakdown + examples)
    computed_at: str


class SearchRun(SQLModel, table=True):
    __tablename__ = "search_run"
    id: Optional[int] = Field(default=None, primary_key=True)
    domain: str = Field(index=True)
    status: str = "queued"  # queued | running | done | failed | cancelled
    refresh: bool = False
    github_org: Optional[str] = None
    pages_crawled: int = 0
    pages_queued: int = 0
    emails_found: int = 0
    current_source: Optional[str] = None
    message: str = ""
    sources_json: str = "{}"  # {source: {"status", "message"}} live per-source state
    error: Optional[str] = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None


# --- Phase 2.5: company name -> domain, bulk find -------------------------------------

class CompanyDomainRow(SQLModel, table=True):
    __tablename__ = "company_domain"
    query: str = Field(primary_key=True)  # normalised company name
    domain: Optional[str] = None  # chosen domain; None while ambiguous / not found
    confirmed: bool = False  # picked by the owner (or a clear automatic match)
    result_json: str = "{}"  # full Resolution (candidates, scores)
    resolved_at: str


class BulkFindRun(SQLModel, table=True):
    __tablename__ = "bulk_find_run"
    id: Optional[int] = Field(default=None, primary_key=True)
    status: str = "queued"  # queued | running | done | failed | cancelled
    total: int = 0
    done: int = 0
    search_unsearched: bool = False
    message: str = ""
    error: Optional[str] = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None


class BulkFindRow(SQLModel, table=True):
    __tablename__ = "bulk_find_row"
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)
    position: int
    input_name: str
    input_company: str
    status: str = "pending"  # pending | done | needs_domain | error
    domain: Optional[str] = None
    email: Optional[str] = None
    score: Optional[int] = None
    evidence: Optional[str] = None
    why: str = ""


# --- Phase 3: settings + job search ---------------------------------------------------

class MetaRow(SQLModel, table=True):
    __tablename__ = "app_meta"
    key: str = Field(primary_key=True)
    value: str = ""


class ProfileRow(SQLModel, table=True):
    __tablename__ = "profile"
    id: int = Field(default=1, primary_key=True)  # one profile
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    school: str = ""
    grad_year: str = ""
    linkedin_url: str = ""
    github_url: str = ""
    portfolio_url: str = ""
    work_authorization: str = ""
    about: str = ""
    resume_link: str = ""  # shared Google Drive PDF, used in the outreach email signature
    student_level: str = "high_school"  # see jobsearch.student.LEVELS
    degree_program: str = ""  # e.g. "BS Computer Science" (college); "planning to study computer science" (high school)
    home_state: str = ""  # two letters, for the Local tag and Built In
    home_cities: str = ""  # comma separated
    updated_at: Optional[str] = None


class ResumeRow(SQLModel, table=True):
    __tablename__ = "resume"
    id: Optional[int] = Field(default=None, primary_key=True)
    label: str
    filename: str  # as uploaded
    stored_name: str  # file in data/resumes/
    kind: str  # pdf | docx
    size: int = 0
    text: str = ""
    is_default: bool = False
    uploaded_at: str


class WatchCompanyRow(SQLModel, table=True):
    __tablename__ = "watch_company"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    ats: Optional[str] = None  # greenhouse | lever | ashby | None (check manually)
    board: Optional[str] = None
    domain: Optional[str] = None
    careers_url: Optional[str] = None
    status: str = "unchecked"  # ok | no_feed | error | unchecked
    job_count: Optional[int] = None
    detected_how: str = ""
    message: str = ""
    last_checked: Optional[str] = None
    created_at: str = ""


class SavedSearchRow(SQLModel, table=True):
    __tablename__ = "saved_search"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    spec_json: str = "{}"  # jobsearch.filters.SearchSpec
    auto_run: bool = True  # included in the daily script
    created_at: str = ""
    last_run_id: Optional[int] = None
    last_run_at: Optional[str] = None
    last_new: int = 0
    last_total: int = 0


class JobRow(SQLModel, table=True):
    __tablename__ = "job"
    id: Optional[int] = Field(default=None, primary_key=True)
    match_key: str = Field(index=True)  # company|title|city, for merging across sources
    company: str = Field(index=True)
    title: str
    location: str = ""
    work_mode: Optional[str] = None
    job_type: Optional[str] = None
    pay_json: Optional[str] = None
    pay_year_max: Optional[float] = None  # for sorting
    posted_at: Optional[str] = None
    description: str = ""
    url: str = ""
    apply_url: str = ""
    department: str = ""
    sources_json: str = "[]"
    flags_json: str = "[]"
    state: str = Field(default="", index=True)  # "" | saved | dismissed
    ai_score: Optional[int] = None  # filled later by a Claude Code command, never by the website
    ai_notes: Optional[str] = None
    first_seen: str = ""
    last_seen: str = ""


class JobSourceRow(SQLModel, table=True):
    __tablename__ = "job_source"
    __table_args__ = (UniqueConstraint("source", "source_id"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(index=True)
    source: str
    source_id: str
    url: str = ""


class SearchJobRow(SQLModel, table=True):
    __tablename__ = "search_job"
    __table_args__ = (UniqueConstraint("search_id", "job_id"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    search_id: int = Field(index=True)
    job_id: int = Field(index=True)
    first_run_id: int  # the run that first found it: "new" when this is the search's latest run
    last_run_id: int
    match_score: int = 0
    match_json: str = "{}"


class JobRunRow(SQLModel, table=True):
    __tablename__ = "job_run"
    id: Optional[int] = Field(default=None, primary_key=True)
    search_id: int = Field(index=True)
    status: str = "queued"  # queued | running | done | failed | cancelled
    trigger: str = "manual"  # manual | scheduled
    total_sources: int = 0
    done_sources: int = 0
    fetched: int = 0
    kept: int = 0
    new_jobs: int = 0
    message: str = ""
    sources_json: str = "{}"  # {source: {"label", "status", "message", "jobs"}}
    error: Optional[str] = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None


class UsageEventRow(SQLModel, table=True):
    __tablename__ = "usage_event"
    id: Optional[int] = Field(default=None, primary_key=True)
    at: str = Field(index=True)  # ISO time
    kind: str = Field(index=True)  # feed_request | apify_run | apify_results | apify_cost_usd | apify_refused
    amount: float = 1
    note: str = ""


# --- Phase 4: applications ------------------------------------------------------------

class ApplicationRow(SQLModel, table=True):
    __tablename__ = "application"
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(index=True, unique=True)
    status: str = Field(default="saved", index=True)  # see applications.status.STATUSES
    cover_letter: str = ""  # what you'll send (your edits included)
    cover_letter_ai: str = ""  # the latest AI draft, to tell whether you edited it
    cover_edited: bool = False
    # requirements-table version: {"opening", "rows": [{"need", "bring"}], "closing"}; the default for PDF/DOCX export
    cover_table_json: str = ""
    cover_table_ai_json: str = ""
    table_edited: bool = False
    needs_person: bool = False  # autopilot couldn't find a good contact; add one by hand
    answers_json: str = "[]"  # [{"question", "answer", "source": "bank" | "ai"}]
    ai_notes: str = ""  # from /draft-applications: eligibility concerns, things to check
    drafts_version: int = 0
    drafts_history_json: str = "[]"  # earlier drafts + the feedback that replaced them
    drafted_at: Optional[str] = None
    approved_at: Optional[str] = None
    applied_at: Optional[str] = None
    follow_up_at: Optional[str] = None  # date (YYYY-MM-DD); cleared when you mark it done
    notes: str = ""  # yours
    created_at: str = ""
    updated_at: str = ""


class ApplicationEventRow(SQLModel, table=True):
    __tablename__ = "application_event"
    id: Optional[int] = Field(default=None, primary_key=True)
    application_id: int = Field(index=True)
    at: str
    kind: str  # status | drafts | edit | follow_up | note
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    note: str = ""


class AnswerRow(SQLModel, table=True):
    __tablename__ = "answer_bank"
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True)  # fixed keys (work_authorization, ...) or custom-<n>
    question: str
    answer: str = ""
    position: int = 0
    fixed: bool = False


# --- Phase 5: outreach -------------------------------------------------------------------

class CompanyPrefRow(SQLModel, table=True):
    __tablename__ = "company_pref"
    company_key: str = Field(primary_key=True)  # normalised company name
    company: str = ""
    reach_out_first: bool = False  # email before applying (top-choice companies)
    small_startup: bool = False
    ok_unpaid: bool = False  # only ever mentioned when small_startup is also on


class ContactRow(SQLModel, table=True):
    __tablename__ = "contact"
    id: Optional[int] = Field(default=None, primary_key=True)
    application_id: int = Field(index=True)  # 0 for a cold pitch (no posting): see mode / company_key
    mode: str = ""  # "" = tied to an application | "cold" = cold pitch to a company with no posting
    company_key: str = Field(default="", index=True)  # company_profile key, for cold pitches
    company: str = ""
    name: str = ""
    email: str = Field(index=True)
    title: str = ""
    department: str = ""
    kind: str = "personal"  # personal | role (shared inbox)
    confidence: int = 0
    evidence: str = ""  # seen | seen_no_name | pattern
    source_url: str = ""
    checked_ok: bool = False  # owner confirmed a low-confidence address
    status: str = Field(default="picked", index=True)  # see outreach.rules.STATUSES
    hook: str = ""
    hook_source: str = ""
    hook_level: str = ""  # person | team | company
    subject: str = ""
    body: str = ""
    body_ai: str = ""
    followup1: str = ""
    followup1_ai: str = ""
    followup2: str = ""
    followup2_ai: str = ""
    edited: bool = False
    linkedin_note: str = ""
    call_prep: str = ""
    thanks: str = ""
    ai_notes: str = ""
    drafts_version: int = 0
    history_json: str = "[]"
    thread_message_ids_json: str = "[]"  # Message-IDs of everything sent to this person (for threading + replies)
    approved_at: Optional[str] = None
    sent_at: Optional[str] = None
    replied_at: Optional[str] = None
    reply_kind: str = ""  # reply | out_of_office | stop | bounce
    reply_snippet: str = ""
    paused_until: Optional[str] = None  # out-of-office: no follow-ups before this date
    referred: bool = False
    created_at: str = ""
    updated_at: str = ""


class OutboxRow(SQLModel, table=True):
    __tablename__ = "outbox"
    id: Optional[int] = Field(default=None, primary_key=True)
    contact_id: Optional[int] = Field(default=None, index=True)  # None for the self-test email
    step: str = "first"  # first | followup1 | followup2 | test
    to_addr: str = ""
    subject: str = ""
    body: str = ""
    status: str = Field(default="scheduled", index=True)  # scheduled | sent | failed | cancelled
    not_before: str = ""  # ISO time: the earliest this may go out
    sent_at: Optional[str] = None
    message_id: str = ""
    in_reply_to: str = ""
    error: str = ""


class MailEventRow(SQLModel, table=True):
    __tablename__ = "mail_event"
    id: Optional[int] = Field(default=None, primary_key=True)
    at: str = Field(index=True)
    contact_id: Optional[int] = Field(default=None, index=True)
    kind: str  # sent | reply | out_of_office | stop | bounce | error | paused | resumed
    detail: str = ""
    message_id: str = ""


class DoNotContactRow(SQLModel, table=True):
    __tablename__ = "do_not_contact"
    email: str = Field(primary_key=True)
    reason: str = ""
    added_at: str = ""


# --- Phase 6: company profiles, autopilot -----------------------------------------------------------

class CompanyProfileRow(SQLModel, table=True):
    __tablename__ = "company_profile"
    key: str = Field(primary_key=True)  # normalised company name
    name: str = ""
    website: str = ""
    domain: str = ""
    employees: Optional[int] = None
    employees_source: str = ""  # YC directory | Built In | company site | Finder (people found)
    employees_url: str = ""
    employees_at: Optional[str] = None
    local: bool = False  # in your home area (Settings > Profile)
    location: str = ""
    industry: str = ""
    one_liner: str = ""
    about: str = ""  # longer description (YC long_description), for matching your interests and for drafting
    cold_status: str = ""  # "" | dismissed (hidden from Outreach > Find companies)
    source: str = ""  # how we found it: yc | builtin | hn | watchlist | jobs | you
    source_url: str = ""
    hiring: bool = True
    preferred: bool = False  # software, AI/ML, data, gaming, edtech, dev tools...
    board_status: str = "unchecked"  # found | none | unchecked
    watch_id: Optional[int] = None
    discovered_at: str = ""
    updated_at: str = ""


class AutopilotRunRow(SQLModel, table=True):
    __tablename__ = "autopilot_run"
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = "morning"  # morning | catch-up | manual
    status: str = "running"  # running | done | stopped | refused | failed
    started_at: str = ""
    finished_at: Optional[str] = None
    log_json: str = "[]"  # [{"step", "status", "detail", "counts"}]
    claude_status: str = ""  # ok | limit | not_needed | refused | error
    claude_reset: str = ""  # when the usage limit resets, if Claude said
    pending_ai_json: str = "[]"  # AI steps left for the catch-up run
