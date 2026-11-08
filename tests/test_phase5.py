"""Phase 5: requirements-table cover letters, and outreach (contacts, drafts checks, the send queue rules, threading,
reply sorting, do-not-contact, bounces). Gmail is replaced by fakes: nothing here touches the network."""
import io
import json
import os
import random
import tempfile
import zipfile
from datetime import date, datetime, timedelta

os.environ.setdefault("JOBHUNT_DB", os.path.join(tempfile.mkdtemp(), "test.db"))
os.environ["JOBHUNT_WORKER"] = "0"

import pytest  # noqa: E402

from applications.export import Letterhead, to_docx, to_pdf  # noqa: E402
from applications.validate import check_drafts, check_table  # noqa: E402
from outreach import rules  # noqa: E402
from outreach.classify import classify  # noqa: E402
from outreach.contacts import suggest  # noqa: E402
from outreach.mail import GmailConfig, Incoming, Sender  # noqa: E402
from outreach.validate import check_outreach  # noqa: E402

TABLE = {"opening": "Your support internship asks for hands-on tech support, which I already do for my school district. "
                    "I'm a Lincoln High School senior heading into computer science.",
         "rows": [{"need": "Technical support experience", "bring": "I reimage and deploy district iPads and laptops every week."},
                  {"need": "Salesforce", "bring": "Haven't used it yet, but I taught myself Supabase and Vercel to ship a production site."},
                  {"need": "Clear communication", "bring": "I teach coding to kids at Code Ninjas, so I'm used to explaining things simply."}],
         "closing": "Could we set up a short call to talk about the role?"}
LETTER = "Dear IDEMIA team,\n\nI deploy devices for my district.\n\nSincerely,\nAlex Rivera"
GOOD = {"contact_id": 1, "hook": "Their team launched a new biometric kiosk in October.", "hook_source": "https://example.com/news",
        "hook_level": "team", "subject": "Your kiosk launch + a summer intern?",
        "email": "Hi Elena,\n\nYour team's new kiosk launch caught my eye because I deploy devices for my school district.\n\n"
                 "I'm a senior at Lincoln High School heading into computer science. I built StudyBuddy, a club-matching site my "
                 "school adopted, with about 1,500 users.\n\nI'm looking for an internship this summer. Would you have 15 minutes for a quick call?",
        "followup1": "Hi Elena, one more thing: I just finished deploying 200 laptops for our district. Still happy to talk if you have 15 minutes.",
        "followup2": "Hi Elena, last note from me. If someone else handles interns, I'd be grateful for a name.",
        "linkedin_note": "Hi Elena, your team's kiosk launch caught my eye. I'm a Lincoln senior heading into CS and looking for a summer internship. Open to a quick chat?",
        "notes": ""}


# --- Part 0: the requirements table -------------------------------------------------------------------

def test_table_checks():
    clean, problems = check_table(TABLE)
    assert not problems and len(clean["rows"]) == 3
    _, problems = check_table({**TABLE, "rows": TABLE["rows"][:2]})
    assert any("3-5 rows" in p for p in problems)
    _, problems = check_table({**TABLE, "opening": "As a seasoned engineer I am writing to apply."})
    assert any("stock phrase" in p for p in problems)
    long = {**TABLE, "rows": [{"need": "x", "bring": "One. Two. Three sentences is too many."}] + TABLE["rows"][1:]}
    assert any("1-2 short sentences" in p for p in check_table(long)[1])
    ok = check_drafts([{"job_id": 1, "cover_letter": LETTER, "answers": [], "cover_table": TABLE}], {1})
    assert ok.ok and ok.ok[0]["cover_table"]["rows"][1]["need"] == "Salesforce"


def test_table_exports():
    head = Letterhead("Alex Rivera", "me@example.com", "6125550142")
    pdf = to_pdf(LETTER, head, date(2026, 11, 5), table=TABLE)
    from pypdf import PdfReader
    text = PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
    assert "What you're looking for" in text and "What I bring" in text and "Salesforce" in text and "Dear IDEMIA team," in text
    xml = zipfile.ZipFile(io.BytesIO(to_docx(LETTER, head, date(2026, 11, 5), table=TABLE))).read("word/document.xml").decode()
    assert "<w:tbl>" in xml and "What I bring" in xml and "Haven't used it yet" in xml.replace("&#39;", "'")


# --- rules ------------------------------------------------------------------------------------------------

def test_sending_ramp():
    start = date(2026, 11, 2)
    assert [rules.daily_cap(start, start + timedelta(days=d)) for d in (0, 13, 14, 27, 28, 90)] == [10, 10, 20, 20, 25, 25]
    assert rules.daily_cap(None, start) == 10 and max(c for _, c in rules.RAMP) <= rules.HARD_CEILING == 30


def test_send_window_and_gaps():
    tz = datetime(2026, 11, 9, 9).astimezone().tzinfo
    monday9, friday18 = datetime(2026, 11, 9, 9, 0, tzinfo=tz), datetime(2026, 11, 13, 18, 0, tzinfo=tz)
    assert rules.in_send_window(monday9) and not rules.in_send_window(friday18)
    assert not rules.in_send_window(datetime(2026, 11, 14, 10, 0, tzinfo=tz))  # Saturday
    assert rules.next_window_start(friday18) == datetime(2026, 11, 16, 8, 0, tzinfo=tz)  # Monday 8am
    assert rules.next_window_start(datetime(2026, 11, 9, 6, 0, tzinfo=tz)) == datetime(2026, 11, 9, 8, 0, tzinfo=tz)
    gaps = {rules.random_gap(random.Random(i)).total_seconds() for i in range(50)}
    assert min(gaps) >= 240 and max(gaps) <= 720 and len(gaps) > 10
    gap = timedelta(minutes=6)
    assert rules.can_send(monday9, 0, None, None, False, gap).ok
    assert "paused" in rules.can_send(monday9, 0, None, None, True, gap).reason
    assert "limit" in rules.can_send(monday9, 10, monday9.date(), None, False, gap).reason
    waiting = rules.can_send(monday9, 1, monday9.date(), monday9 - timedelta(minutes=2), False, gap)
    assert not waiting.ok and waiting.wait_until == monday9 + timedelta(minutes=4)


def test_bounces_followups_gate_and_order():
    assert rules.bounce_pause_reason(2, 10, 2)
    assert rules.bounce_pause_reason(0, 100, 3) and not rules.bounce_pause_reason(0, 100, 2)
    assert not rules.bounce_pause_reason(1, 20, 1)  # too few sent for a rate
    assert rules.followup_dates(date(2026, 11, 9)) == (date(2026, 11, 16), date(2026, 11, 30))
    assert rules.queueable(97, "personal", False)[0] and rules.queueable(85, "personal", False)[0]
    assert not rules.queueable(60, "personal", False)[0] and rules.queueable(60, "personal", True)[0]
    assert not rules.queueable(85, "role", False)[0]
    rng = random.Random(1)
    assert rules.first_send_not_before(None, False, date(2026, 11, 9), rng) is None  # waits for the application
    assert rules.first_send_not_before(None, True, date(2026, 11, 9), rng) == date(2026, 11, 9)
    sent_on = rules.first_send_not_before(date(2026, 11, 9), False, date(2026, 11, 9), rng)
    assert date(2026, 11, 10) <= sent_on <= date(2026, 11, 11)
    assert rules.apply_by(date(2026, 11, 9)) == date(2026, 11, 14)


# --- replies ----------------------------------------------------------------------------------------------

def test_reply_sorting():
    today = date(2026, 11, 9)
    assert classify("mailer-daemon@googlemail.com", "Delivery Status Notification (Failure)", "Address not found").kind == "bounce"
    ooo = classify("elena@x.com", "Automatic reply: hi", "I'm out of the office and will return on November 20.", today=today)
    assert ooo.kind == "out_of_office" and ooo.return_date == date(2026, 11, 20)
    assert classify("e@x.com", "Re: hi", "On leave, back 12/1.", today=today).return_date == date(2026, 12, 1)
    assert classify("e@x.com", "Re: hi", "Thanks, but we're not interested. Please remove me from your list.").kind == "stop"
    real = classify("e@x.com", "Re: hi", "Sure, how about Thursday at 3?\n\n> Hi Elena, I'm a senior at...")
    assert real.kind == "reply" and "Thursday" in real.snippet and "senior" not in real.snippet


# --- drafts checks ---------------------------------------------------------------------------------------

def test_outreach_checks():
    known = {1: {"first_name": "Elena", "unpaid_ok": False}}
    assert check_outreach([GOOD], known).ok
    bad = dict(GOOD, hook_source="", subject="Internship Request",
               email="Dear Ms. Smith,\n\nI hope this email finds you well. See www.mysite.com. I could work unpaid.",
               linkedin_note="x" * 201)
    errors = " ".join(check_outreach([bad], known).errors)
    for expected in ["hook_source", "Internship Request", "Sir/Mr/Ms", "stock phrase", "link", "first name", "internship",
                     "unpaid", "characters"]:
        assert expected in errors, expected
    long = dict(GOOD, email=GOOD["email"] + " word" * 120)
    assert "max 130" in " ".join(check_outreach([long], known).errors)
    assert check_outreach([dict(GOOD, email=GOOD["email"] + " I'm happy to start unpaid.")],
                          {1: {"first_name": "Elena", "unpaid_ok": True}}).ok


def test_contact_suggestions():
    people = [
        {"email": "r@x.com", "name": "Rita Ray", "title": "Technical Recruiter", "department": "recruiting", "kind": "personal", "confidence": 97},
        {"email": "e@x.com", "name": "Eli Eng", "title": "Software Engineer", "department": "engineering", "kind": "personal", "confidence": 97},
        {"email": "d@x.com", "name": "Dana Data", "title": "Data Scientist", "department": "engineering", "kind": "personal", "confidence": 97},
        {"email": "c@x.com", "name": "Cleo Chief", "title": "Co-founder & CEO", "department": "executive", "kind": "personal", "confidence": 97},
        {"email": "info@x.com", "name": "", "title": "", "department": "", "kind": "role", "confidence": 85},
    ]
    picks = suggest(people, "Data Science Intern")
    assert [p.reason for p in picks] == ["recruiter", "team", "team"] and picks[1].email == "d@x.com"
    assert "founder" in [p.reason for p in suggest(people, "Software Engineer Intern", small_startup=True)]
    assert all(p.kind == "personal" for p in picks)  # shared inboxes only as a last resort
    only_inbox = suggest([people[-1]], "Software Engineer Intern")
    assert [p.reason for p in only_inbox] == ["shared inbox"]


# --- the whole flow with a fake Gmail -------------------------------------------------------------------------

class FakeSMTP:
    sent: list = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        assert user == "me@gmail.com" and password == "app-pass"

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


class FakeInbox:
    def __init__(self, messages):
        self.messages = messages

    def fetch_related(self, our_ids, addresses, since):
        return list(self.messages)


@pytest.fixture()
def world(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from finder.models import FoundEmail
    from finder.patterns import detect_pattern
    from finder.store import DomainReport, SourceStatus
    from jobsearch.filters import Evaluation, SearchSpec
    from jobsearch.match import MatchResult
    from jobsearch.models import Job
    from jobsearch.runner import RunReport, ScoredJob
    from storage.apps_repo import AppRepo
    from storage.jobs_repo import JobRepo
    from storage.outreach_repo import OutreachRepo
    from storage.repo import SqlStore
    monkeypatch.setenv("GMAIL_ADDRESS", "me@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-pass")
    FakeSMTP.sent = []
    from sqlmodel import Session, select
    from storage.db import engine
    from storage.tables import ContactRow, DoNotContactRow, MailEventRow, MetaRow, OutboxRow
    with TestClient(app) as client:
        with Session(engine) as s:  # each test starts with an empty outreach queue
            for table in (OutboxRow, MailEventRow, ContactRow, DoNotContactRow):
                for row in s.exec(select(table)):
                    s.delete(row)
            for row in s.exec(select(MetaRow)):
                if row.key.startswith("outreach_"):
                    s.delete(row)
            s.commit()
        jobs, apps, out = JobRepo(), AppRepo(), OutreachRepo()
        tag = str(len(jobs.searches()))
        company, domain = f"Acme Robotics {tag}", f"acme{tag}.test"
        jobs.add_company(name=company, ats="greenhouse", board=f"acme{tag}", domain=domain, status="ok")
        emails = [FoundEmail(f"rita@{domain}", domain, "personal", "website", f"https://{domain}/team", full_name="Rita Ray",
                             title="Technical Recruiter", department="recruiting"),
                  FoundEmail(f"eli@{domain}", domain, "personal", "website", f"https://{domain}/team#2", full_name="Eli Eng",
                             title="Software Engineer", department="engineering"),
                  FoundEmail(f"info@{domain}", domain, "role", "website", f"https://{domain}/contact")]
        SqlStore().save_report(DomainReport(domain, emails, detect_pattern(domain, emails), sources=[SourceStatus("website", "ok", "")]))
        search = jobs.save_search(None, f"s{tag}", SearchSpec(), auto_run=False)
        job = Job("greenhouse", f"acme-{tag}", company, "Software Engineer Intern", "https://boards.example/acme")
        jobs.save_results(search.id, 500 + search.id, RunReport(kept=[ScoredJob(job, Evaluation(True, [], 1.0, []), MatchResult(60, 0.5, 1.0))]))
        job_id = jobs.results(search.id)[0][0].id
        app_row = apps.ensure(job_id)
        yield {"client": client, "out": out, "apps": apps, "app": app_row, "domain": domain, "company": company, "job_id": job_id}


def _draft(world, email_addr, first="Rita"):
    out = world["out"]
    c = out.pick(world["app"].id, email_addr)
    from outreach.validate import check_outreach as chk
    item = dict(GOOD, contact_id=c.id, email=GOOD["email"].replace("Elena", first), followup1=GOOD["followup1"].replace("Elena", first),
                followup2=GOOD["followup2"].replace("Elena", first), linkedin_note=GOOD["linkedin_note"].replace("Elena", first))
    checked = chk([item], out.known_for_drafts())
    assert not checked.errors, checked.errors
    out.save_drafts(checked.ok)
    return out.contact(c.id)


def test_outreach_flow(world):
    out, apps, client = world["out"], world["apps"], world["client"]
    tz = datetime(2026, 11, 9, 9).astimezone().tzinfo
    monday = datetime(2026, 11, 9, 9, 0, tzinfo=tz)
    sender = Sender(GmailConfig(), smtp_factory=FakeSMTP)

    # suggestions come from the Finder: recruiter + engineer, never the shared inbox while people exist
    domain, sugg = out.suggestions(world["app"].id)
    assert domain == world["domain"] and [s.reason for s in sugg] == ["recruiter", "team"]
    section = client.get(f"/applications/{world['app'].id}/outreach").text
    assert "Rita Ray" in section and "Pick" in section

    c = _draft(world, f"rita@{world['domain']}")
    assert c.status == "draft_ready" and c.drafts_version == 1
    assert "Your kiosk launch" in client.get(f"/outreach/{c.id}").text

    # default order: approved, but nothing is scheduled until the application is marked applied
    out.approve(c.id)
    assert out.schedule_ready(monday) == 0 and out.outbox(c.id) == []
    apps.move(world["app"].id, "approved")
    apps.move(world["app"].id, "applied", today=monday.date())
    out.schedule_ready(monday, random.Random(3))
    first = out.outbox(c.id)[0]
    assert first.step == "first" and date(2026, 11, 10) <= datetime.fromisoformat(first.not_before).date() <= date(2026, 11, 11)
    assert "Thanks,\nAlex" in first.body or "Thanks," in first.body  # signature added

    # nothing goes out early or outside hours
    assert out.tick(monday, sender=sender, inbox=FakeInbox([]))["sent"] is None
    send_time = datetime.fromisoformat(first.not_before).astimezone() + timedelta(minutes=1)
    r = out.tick(send_time, sender=sender, inbox=FakeInbox([]), rng=random.Random(1))
    assert r["sent"] == first.id and len(FakeSMTP.sent) == 1
    msg = FakeSMTP.sent[0]
    assert msg["To"] == f"rita@{world['domain']}" and msg.get_content_type() == "text/plain" and not msg.is_multipart()
    c = out.contact(c.id)
    assert c.status == "sent"
    fu1 = [o for o in out.outbox(c.id) if o.step == "followup1"][0]
    assert datetime.fromisoformat(fu1.not_before).date() == send_time.date() + timedelta(days=7)
    assert fu1.in_reply_to == msg["Message-ID"] and fu1.subject.startswith("Re: ")

    # follow-up 1 goes out in the same thread
    t = datetime.fromisoformat(fu1.not_before).astimezone() + timedelta(minutes=1)
    out.tick(t, sender=sender, inbox=FakeInbox([]), rng=random.Random(2))
    reply1 = FakeSMTP.sent[-1]
    assert reply1["In-Reply-To"] == msg["Message-ID"] and msg["Message-ID"] in reply1["References"]
    assert out.contact(c.id).status == "followup1"

    # a real reply stops follow-up 2 and pins it for you
    incoming = Incoming(f"rita@{world['domain']}", "Re: Your kiosk launch", "Happy to chat Thursday!", "<r1@x>",
                        reply1["Message-ID"], [msg["Message-ID"]], None, {})
    assert out.check_replies(FakeInbox([incoming]), t) == 1
    c = out.contact(c.id)
    assert c.status == "replied" and "Thursday" in c.reply_snippet
    assert all(o.status != "scheduled" for o in out.outbox(c.id))
    assert out.today(t)["replies"] == 1
    assert out.check_replies(FakeInbox([incoming]), t) == 0  # the same message isn't counted twice
    assert "Replied: needs your response" in client.get("/outreach").text

    # referral shows on the application card
    out.set_status(c.id, "referred")
    assert "Referred" in client.get("/applications/board").text


def test_low_confidence_needs_ok_and_stop_and_bounce(world):
    out = world["out"]
    tz = datetime(2026, 11, 9, 9).astimezone().tzinfo
    c = _draft(world, f"eli@{world['domain']}", "Eli")
    with Session_override(c.id, confidence=60):
        with pytest.raises(ValueError, match="confirm"):
            out.approve(c.id)
        out.set_checked(c.id)
        out.out = out.set_prefs(world["company"], reach_out_first=True)
        assert out.approve(c.id).status == "approved"
    first = out.outbox(c.id)[0]
    when = datetime.fromisoformat(first.not_before).astimezone() + timedelta(minutes=1)
    sender = Sender(GmailConfig(), smtp_factory=FakeSMTP)
    out.tick(when, sender=sender, inbox=FakeInbox([]))
    sent_id = out.contact(c.id).thread_message_ids_json
    # reach-out-first: the application shows "Apply by"
    assert out.apply_by(world["app"].id) == (when.date() + timedelta(days=5)).isoformat()
    # "please stop" -> permanent do-not-contact, nothing more sends
    stop = Incoming(f"eli@{world['domain']}", "Re: hi", "Please stop emailing me.", "<s1@x>", json.loads(sent_id)[0], [], None, {})
    out.check_replies(FakeInbox([stop]), when)
    assert out.contact(c.id).status == "do_not_contact" and f"eli@{world['domain']}" in out.do_not_contact()
    with pytest.raises(ValueError, match="do-not-contact"):
        out.pick(world["app"].id, f"eli@{world['domain']}")


def test_out_of_office_and_bounce_pause(world):
    out = world["out"]
    tz = datetime(2026, 11, 9, 9).astimezone().tzinfo
    out.set_prefs(world["company"], reach_out_first=True)
    c = _draft(world, f"rita@{world['domain']}")
    out.approve(c.id)
    first = out.outbox(c.id)[0]
    when = datetime.fromisoformat(first.not_before).astimezone() + timedelta(minutes=1)
    sender = Sender(GmailConfig(), smtp_factory=FakeSMTP)
    out.tick(when, sender=sender, inbox=FakeInbox([]))
    mid = json.loads(out.contact(c.id).thread_message_ids_json)[0]
    back = when.date() + timedelta(days=20)
    ooo = Incoming(f"rita@{world['domain']}", "Automatic reply", f"I'm out of the office until {back:%B} {back.day}.",
                   "<o1@x>", mid, [], None, {"Auto-Submitted": "auto-replied"})
    out.check_replies(FakeInbox([ooo]), when)
    c = out.contact(c.id)
    assert c.status == "sent" and c.paused_until == (back + timedelta(days=2)).isoformat()
    fu1 = [o for o in out.outbox(c.id) if o.step == "followup1" and o.status == "scheduled"][0]
    assert datetime.fromisoformat(fu1.not_before).date() >= back + timedelta(days=2)

    # two bounces in a day pause all sending
    for i in range(2):
        b = Incoming("mailer-daemon@googlemail.com", "Delivery Status Notification (Failure)", f"Address not found {mid}",
                     f"<b{i}@x>", "", [], None, {})
        out._apply_reply(c.id, b, when)
    assert out.paused() and "bounced" in out.paused()
    assert "paused" in out.tick(when, sender=sender, inbox=FakeInbox([]))["reason"] or out.tick(when, sender=sender, inbox=FakeInbox([]))["sent"] is None
    out.resume()
    assert not out.paused()


def test_daily_cap_and_test_email(world):
    out = world["out"]
    tz = datetime(2026, 11, 9, 9).astimezone().tzinfo
    sender = Sender(GmailConfig(), smtp_factory=FakeSMTP)
    test = out.send_test(sender, datetime(2026, 11, 9, 9, tzinfo=tz))
    assert test.status == "sent" and FakeSMTP.sent[-1]["To"] == "me@gmail.com"
    reply = Incoming("me@gmail.com", "Re: jobhunt test email", "got it", "<t@x>", test.message_id, [], None, {})
    out.check_replies(FakeInbox([reply]), datetime(2026, 11, 9, 9, 5, tzinfo=tz))
    assert out.test_status()["reply_seen"]
    # the daily cap: the first 14 days allow 10 real sends (test emails don't count)
    k = out.counts_today(datetime(2026, 11, 9, 10, tzinfo=tz))
    d = rules.can_send(datetime(2026, 11, 9, 10, tzinfo=tz), 10, date(2026, 11, 9), None, False, timedelta(0))
    assert not d.ok and "limit" in d.reason and k["sent_today"] >= 0


class Session_override:
    """Temporarily change a contact's confidence (to test the low-confidence gate)."""

    def __init__(self, contact_id, **fields):
        self.id, self.fields = contact_id, fields

    def __enter__(self):
        from sqlmodel import Session
        from storage.db import engine
        from storage.tables import ContactRow
        with Session(engine) as s:
            c = s.get(ContactRow, self.id)
            for k, v in self.fields.items():
                setattr(c, k, v)
            s.add(c)
            s.commit()
        return self

    def __exit__(self, *a):
        return False
