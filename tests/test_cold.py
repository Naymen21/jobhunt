"""Cold pitches (Outreach > Find companies): the full YC parser, filters, who to pitch, contacts tied to a company
instead of an application, drafting rules, sending order, the Review page, and the autopilot step. All offline."""
import json
import os
import tempfile
from datetime import datetime

os.environ.setdefault("JOBHUNT_DB", os.path.join(tempfile.mkdtemp(), "test.db"))
os.environ["JOBHUNT_WORKER"] = "0"

import pytest  # noqa: E402

from jobsearch import discover  # noqa: E402
from outreach.cold import ColdFilters, auto_choice, filter_companies, matches, suggest_cold  # noqa: E402

YC_ALL = [
    {"name": "Tiny Tutor", "slug": "tinytutor", "website": "https://tinytutor.test", "team_size": 12, "isHiring": False,
     "status": "Active", "all_locations": "Minneapolis, MN, USA", "regions": ["United States of America", "Remote"],
     "industries": ["Education"], "tags": ["EdTech", "AI"], "one_liner": "AI tutoring for middle schoolers",
     "long_description": "Tiny Tutor builds an AI tutor that helps kids with math homework.",
     "url": "https://www.ycombinator.com/companies/tinytutor"},
    {"name": "Big Robots", "team_size": 400, "status": "Active", "all_locations": "SF"},
    {"name": "Gone Co", "team_size": 20, "status": "Inactive", "all_locations": "SF"},
    {"name": "Pet Snacks", "team_size": 30, "isHiring": True, "status": "Active", "all_locations": "Austin, TX, USA",
     "industries": ["Consumer"], "tags": ["Pets"], "one_liner": "Healthy dog treats"},
]


def test_parse_yc_all_keeps_small_active_companies_hiring_or_not():
    got = discover.parse_yc_all(YC_ALL)
    assert [c.name for c in got] == ["Tiny Tutor", "Pet Snacks"]
    tutor = got[0]
    assert tutor.local and tutor.preferred and not tutor.hiring and tutor.employees == 12
    assert "Remote" in tutor.location and "AI tutor" in tutor.about and "EdTech" in tutor.industry


def _co(**kw):
    base = {"key": "x", "name": "X", "employees": 30, "local": False, "location": "", "industry": "", "one_liner": "",
            "about": "", "preferred": False, "source": "yc", "cold_status": "", "contacted": False}
    return {**base, **kw}


def test_cold_filters():
    f = ColdFilters(min_employees=10, max_employees=60)
    assert matches(_co(employees=10), f) and matches(_co(employees=60), f)
    assert not matches(_co(employees=9), f) and not matches(_co(employees=61), f)
    assert not matches(_co(employees=None), f) and matches(_co(employees=None), ColdFilters(include_unknown_size=True))
    assert not matches(_co(contacted=True), f) and matches(_co(contacted=True), ColdFilters(hide_contacted=False))
    assert not matches(_co(cold_status="dismissed"), f) and matches(_co(cold_status="dismissed"), ColdFilters(show_hidden=True))
    assert not matches(_co(), ColdFilters(local_only=True)) and matches(_co(local=True), ColdFilters(local_only=True))
    games = ColdFilters(keywords="games, ai")
    assert matches(_co(one_liner="Multiplayer gaming platform"), games)  # stem: games -> gaming
    assert matches(_co(industry="AI, Developer Tools"), games)
    assert not matches(_co(one_liner="We said hello to retail"), games)  # "ai" isn't inside "said"
    assert matches(_co(location="Remote · NYC"), ColdFilters(location="remote"))
    assert not matches(_co(source="hn"), ColdFilters(source="yc"))
    ranked = filter_companies([_co(name="Far", employees=50), _co(name="Near", local=True, employees=55),
                               _co(name="Small", employees=11)], f)
    assert [c["name"] for c in ranked] == ["Near", "Small", "Far"]  # local first, then smaller
    assert ColdFilters.from_dict({"min_employees": 50, "max_employees": 20}).max_employees == 50


PEOPLE = [
    {"email": "hr@co.test", "name": "Hana Ruiz", "title": "Head of People", "department": "hr", "kind": "personal",
     "confidence": 95, "evidence": "seen", "source_url": "u"},
    {"email": "eng@co.test", "name": "Eli Ng", "title": "Software Engineer", "department": "engineering", "kind": "personal",
     "confidence": 95, "evidence": "seen", "source_url": "u"},
    {"email": "cto@co.test", "name": "Cara Tan", "title": "CTO", "department": "engineering", "kind": "personal",
     "confidence": 90, "evidence": "seen", "source_url": "u"},
    {"email": "sam@co.test", "name": "Sam Fox", "title": "Co-founder & CEO", "department": "executive", "kind": "personal",
     "confidence": 70, "evidence": "pattern", "source_url": "u"},
    {"email": "hello@co.test", "name": "", "title": "", "department": "", "kind": "role", "confidence": 60,
     "evidence": "seen_no_name", "source_url": "u"},
]


def test_who_to_pitch():
    s = suggest_cold(PEOPLE)
    assert [(p.email, p.reason) for p in s] == [("sam@co.test", "founder"), ("cto@co.test", "tech lead"), ("eng@co.test", "team")]
    assert "hr@co.test" not in [p.email for p in suggest_cold(PEOPLE, limit=10)]  # never HR
    # the autopilot only takes a founder/lead with a seen address: the CEO here is a pattern guess, so the CTO
    assert auto_choice(PEOPLE).email == "cto@co.test"
    assert auto_choice([p for p in PEOPLE if p["email"] != "cto@co.test"]) is None  # never a guess
    assert [p.reason for p in suggest_cold([PEOPLE[-1]])] == ["shared inbox"]


@pytest.fixture()
def site():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


def _seed_company(name="Tiny Tutor", domain="tinytutor.test", people=True):
    from finder.models import FoundEmail
    from finder.patterns import detect_pattern
    from finder.store import DomainReport, SourceStatus
    from storage.companies_repo import CompanyRepo
    from storage.repo import SqlStore
    item = dict(YC_ALL[0], name=name, website=f"https://{domain}")
    row, _ = CompanyRepo().upsert(discover.parse_yc_all([item])[0])
    if people:
        emails = [FoundEmail(f"maya@{domain}", domain, "personal", "website", f"https://{domain}/about",
                             full_name="Maya Chen", title="Founder & CEO", department="executive"),
                  FoundEmail(f"leo.park@{domain}", domain, "personal", "website", f"https://{domain}/team",
                             full_name="Leo Park", title="Engineer", department="engineering")]
        SqlStore().save_report(DomainReport(domain, emails, detect_pattern(domain, emails), sources=[SourceStatus("website", "ok", "")]))
    return row


def test_find_companies_page_and_pick(site):
    from storage.outreach_repo import OutreachRepo
    row = _seed_company()
    page = site.get("/outreach?view=companies").text
    assert "Find companies" in page and "Filters" in page
    r = site.post("/outreach/companies/list", data={"min_employees": "10", "max_employees": "60", "keywords": "tutor",
                                                     "hide_contacted": "on"})
    assert "Tiny Tutor" in r.text and "~12 people" in r.text and "Local" in r.text
    from app.autopilot_run import load_settings
    assert load_settings().cold_filters["keywords"] == "tutor"  # remembered
    r = site.get("/outreach/companies/detail", params={"k": row.key})
    assert "Maya Chen" in r.text and "founder" in r.text and "Not interested" in r.text
    r = site.post("/outreach/companies/pick", params={"k": row.key}, data={"email": f"maya@tinytutor.test"})
    out = OutreachRepo()
    c = out.cold_contacts(row.key)[0]
    assert c.mode == "cold" and c.application_id == 0 and c.company == "Tiny Tutor" and c.confidence >= 85
    assert "already picked" in site.post("/outreach/companies/pick", params={"k": row.key},
                                         data={"email": "maya@tinytutor.test"}).text
    # hidden once contacted; typed address needs evidence, a random address isn't accepted silently
    r = site.post("/outreach/companies/list", data={"min_employees": "10", "max_employees": "60", "keywords": "tutor", "hide_contacted": "on"})
    assert "Tiny Tutor" not in r.text
    assert "in the Finder results" in site.post("/outreach/companies/pick", params={"k": row.key},
                                                      data={"email": "nobody@tinytutor.test"}).text
    site.post("/outreach/companies/pick", params={"k": row.key},
              data={"email": "ana@tinytutor.test", "name": "Ana", "evidence": "typed"})
    assert len(out.cold_contacts(row.key)) == 2
    # board + drafting handoff
    assert "Cold pitch" in site.get("/outreach/board").text
    pending = out.pending(10)
    d = next(x for x in pending["contacts"] if x["contact_id"] == c.id)
    assert d["mode"] == "cold" and d["role"] is None and d["company_size"] == 12 and "AI tutor" in d["company_about"]


def test_cold_drafts_rules_send_order_and_review(site):
    from outreach.validate import check_outreach
    from storage.outreach_repo import OutreachRepo
    row = _seed_company("Quiz Kite", "quizkite.test")
    out = OutreachRepo()
    c = out.pick_cold(row.key, "maya@quizkite.test")
    known = out.known_for_drafts()
    assert known[c.id]["cold"]
    item = {"contact_id": c.id, "hook": "Quiz Kite launched adaptive math quizzes in October.",
            "hook_source": "https://quizkite.test/blog", "hook_level": "company", "subject": "Quiz Kite + a student intern?",
            "email": "Hi Maya,\n\nYour adaptive math quizzes caught my eye.\n\nI built a club-matching site with 1,500 users.\n\n"
                     "Would you be open to a 15-minute chat about an internship?",
            "followup1": "Hi Maya, one more thing: I added quizzes to my site.", "followup2": "Hi Maya, last note from me.",
            "linkedin_note": "Hi Maya, I like Quiz Kite. Open to a quick chat about an internship?", "notes": ""}
    assert any("must say you're in high school" in e for e in check_outreach([item], known).errors)
    item["email"] = item["email"].replace("I built", "I'm a senior at Lincoln High School. I built")
    assert check_outreach([item], known).errors == []
    out.save_drafts([item])
    page = site.get("/review").text
    assert "Quiz Kite" in page and "Cold pitch" in page and "Looks good" in page
    r = site.post(f"/review/cold/{c.id}/looks-good")
    assert "Approved" in r.text and out.contact(c.id).status == "approved"
    first = [o for o in out.outbox(c.id) if o.step == "first"]
    assert first  # nothing to apply to: queued for the next sending window right away
    # skip removes an unapproved one
    c2 = out.pick_cold(row.key, "leo.park@quizkite.test")
    site.post(f"/review/cold/{c2.id}/skip")
    assert out.contact(c2.id) is None


def test_add_and_dismiss_company(site):
    r = site.post("/outreach/companies/add", data={"name": "Friend Found LLC", "website": "friendfound.test"})
    assert "Added Friend Found" in r.text and "Find people at Friend Found" in r.text
    from storage.companies_repo import CompanyRepo
    row = CompanyRepo().get("Friend Found LLC")
    assert row.source == "you" and row.domain == "friendfound.test" and row.employees is None
    assert "look like an address" in site.post("/outreach/companies/add", data={"name": "X", "website": "not a site"}).text
    site.post("/outreach/companies/dismiss", params={"k": row.key})
    assert CompanyRepo().get("Friend Found LLC").cold_status == "dismissed"
    r = site.post("/outreach/companies/list", data={"include_unknown_size": "on", "source": "you"})
    assert "Friend Found" not in r.text
    r = site.post("/outreach/companies/list", data={"include_unknown_size": "on", "source": "you", "show_hidden": "on"})
    assert "Friend Found" in r.text and "Hidden" in r.text


def test_autopilot_cold_step(site, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from app.autopilot_run import Autopilot
    from autopilot.claude import ClaudeResult
    from autopilot.rules import AutopilotSettings
    from storage.outreach_repo import OutreachRepo
    good = _seed_company("Auto Pitch Co", "autopitch.test")
    nobody = _seed_company("Nobody Here Co", "nobodyhere.test", people=False)
    from storage.companies_repo import CompanyRepo
    CompanyRepo().set_size("Nobody Here Co", 11, "YC directory")  # smaller: ranked first
    finder_calls = []
    s = AutopilotSettings(source_yc=False, source_yc_small=False, source_builtin=False, source_hn=False, source_simplify=False,
                          size_checks_per_day=0, board_checks_per_day=0, finder_searches_per_day=2, cold_per_day=1,
                          cold_filters={"keywords": "auto pitch, nobody here", "min_employees": 10, "max_employees": 60},
                          backup_dir=str(tmp_path / "b"), cap_outreach_drafts=0)
    ap = Autopilot(settings=s, claude=lambda c, a: ClaudeResult("ok", "ok"), run_searches=False, page_fn=lambda u: None,
                   detect_fn=lambda q, c: None, finder_fn=lambda company, domain: (finder_calls.append(company), domain)[1])
    run = ap.run("manual")
    log = {x["step"]: x for x in json.loads(run.log_json)}
    assert log["cold"]["counts"]["picked"] == 1 and log["cold"]["counts"]["no_good_person"] == 1
    picked = OutreachRepo().cold_contacts(good.key)
    assert [c.email for c in picked] == ["maya@autopitch.test"] and not OutreachRepo().cold_contacts(nobody.key)
    # next run: today's quota is used up
    run2 = ap.run("manual")
    assert {x["step"]: x for x in json.loads(run2.log_json)}["cold"]["status"] == "skipped"
    # off by default
    assert AutopilotSettings().cold_per_day == 0
