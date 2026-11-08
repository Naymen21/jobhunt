"""Phase 6: discovery parsers, company sizes, pitch candidates, paste people, the autopilot (rules, caps, usage-limit
stop, API-key refusal, catch-up), the review page's one-click flow, and maintenance. Claude, the network and the Finder
are all fakes here."""
import gzip
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("JOBHUNT_DB", os.path.join(tempfile.mkdtemp(), "test.db"))
os.environ["JOBHUNT_WORKER"] = "0"

import pytest  # noqa: E402

from autopilot import claude as claude_mod  # noqa: E402
from autopilot.claude import ClaudeResult  # noqa: E402
from autopilot.rules import AutopilotSettings, Candidate, parse_limit, pick_saves, review_priority  # noqa: E402
from jobsearch import discover  # noqa: E402
from jobsearch.flags import add_pitch, eligible_for_high_schooler, find_flags, is_pitch  # noqa: E402
from outreach.paste import parse_people  # noqa: E402

YC = [
    {"name": "Loop AI", "slug": "loop", "website": "https://loop.ai", "team_size": 48, "isHiring": True, "status": "Active",
     "all_locations": "Minneapolis, MN, USA", "industries": ["B2B", "Engineering, Product and Design"], "tags": ["AI"],
     "one_liner": "AI copilots for support teams", "url": "https://www.ycombinator.com/companies/loop"},
    {"name": "BigCo", "team_size": 900, "isHiring": True, "status": "Active", "all_locations": "SF"},
    {"name": "Sleepy", "team_size": 50, "isHiring": False, "status": "Active", "all_locations": "SF"},
    {"name": "Farmly", "team_size": 35, "isHiring": True, "status": "Active", "all_locations": "Austin, TX",
     "industries": ["Agriculture"], "tags": [], "one_liner": "Tractor parts marketplace"},
]
BUILTIN = """<div class="company-content-area"><a href="/company/northstar-labs"><h2>Northstar Labs</h2></a>
Software • Machine Learning Save Saved CREATE JOB ALERT 2 Offices 45 Employees Hiring Now We build data tools.</div>
<div class="company-content-area"><a href="/company/oldco"><h2>OldCo</h2></a> Consulting Save 1 Offices 2,400 Employees</div>"""


# --- discovery ---------------------------------------------------------------------------------------------------------

def test_discover_parsers():
    yc = discover.parse_yc(YC)
    assert [c.name for c in yc] == ["Loop AI", "Farmly"]
    loop = yc[0]
    assert loop.local and loop.preferred and loop.employees == 48 and loop.employees_source == "YC directory"
    assert not yc[1].preferred and not yc[1].local
    assert sorted(yc, key=lambda c: c.rank)[0].name == "Loop AI"
    bi = discover.parse_builtin(BUILTIN)
    assert [(c.name, c.employees, c.local, c.hiring) for c in bi] == [("Northstar Labs", 45, True, True), ("OldCo", 2400, True, False)]
    comp, job = discover.parse_hn_comment({"id": 99, "time": 1762000000, "text":
        "Acme Robotics | Software Engineer, Summer Intern | Minneapolis, MN | ONSITE<p>We build warehouse robots. Interns welcome. https://acme.example/jobs"}, "")
    assert comp.name == "Acme Robotics" and comp.local and comp.website.startswith("https://acme.example")
    assert job and job.job_type == "internship" and "Intern" in job.title and job.source == "hn"
    assert discover.parse_hn_comment({"id": 1, "text": "Great thread, thanks!"}, "") == (None, None)
    now = datetime(2026, 11, 6, tzinfo=timezone.utc)
    sim = discover.parse_simplify([
        {"active": True, "is_visible": True, "company_name": "Data Co", "title": "Data Intern", "url": "https://x/1", "id": "1",
         "locations": ["Minneapolis, MN"], "degrees": ["Bachelor's"], "terms": ["Summer 2027"], "date_posted": now.timestamp() - 86400},
        {"active": False, "company_name": "Gone", "title": "x", "id": "2"},
        {"active": True, "is_visible": True, "company_name": "Old", "title": "x", "id": "3", "date_posted": now.timestamp() - 400 * 86400}], now=now)
    assert [j.company for j in sim] == ["Data Co"]
    assert [f.key for f in find_flags(sim[0].description)][0] == "enrolled"  # the degree list reads like a posting
    assert discover.headcount_from_text("We're a team of 45 people building tools trusted by 2,000 customers") == 45
    assert discover.headcount_from_text("Trusted by 300 customers worldwide") is None
    assert discover.is_local("Eden Prairie, Minnesota") and discover.is_local("St. Paul") and not discover.is_local("Paul, Idaho")


def test_company_sizes_prefer_better_sources(site):
    from storage.companies_repo import CompanyRepo
    repo = CompanyRepo()
    repo.set_size("Sizey Inc", 12, "Finder (people found)")
    repo.set_size("Sizey Inc", 60, "company site", "https://sizey.example/about")
    assert repo.get("Sizey Inc").employees == 60
    repo.set_size("Sizey Inc", 5, "Finder (people found)")  # a weaker source doesn't overwrite
    assert repo.get("Sizey Inc").employees == 60
    row, new = repo.upsert(discover.parse_yc(YC)[0])
    assert new and row.employees == 48 and row.local and row.domain == "loop.ai"


# --- pitch candidates --------------------------------------------------------------------------------------------------

def test_pitch_candidates():
    college = find_flags("Must be currently enrolled in a bachelor's degree program.")
    assert not eligible_for_high_schooler(college)
    small = add_pitch(college, 45)
    assert is_pitch(small) and eligible_for_high_schooler(small) and small[0].label == "Pitch candidate"
    assert not is_pitch(add_pitch(college, 400)) and not is_pitch(add_pitch(college, None))
    assert not is_pitch(add_pitch(find_flags("Python and SQL."), 45))  # nothing blocking: just a normal job
    from jobsearch.filters import SearchSpec
    from jobsearch.models import Job
    from jobsearch.runner import run_search
    from jobsearch.config import JobSettings
    from jobsearch.sources import FeedClient
    j = Job("simplify", "s1", "Tiny Co", "Software Intern", "https://x", description="Candidates must be currently pursuing a Bachelor's degree.")
    report = run_search(SearchSpec(sources=["discover"], exclude_experience=[]), [], client=FeedClient(JobSettings()),
                        discover_jobs=[j], company_info={"tiny": {"employees": 40, "local": True}})
    assert report.kept and is_pitch(report.kept[0].job.flags)


# --- paste people ------------------------------------------------------------------------------------------------------------

PASTE = """Jane Doe
· 2nd
Software Engineer at Acme | Python
Minneapolis, MN
12 mutual connections
Connect
Marcus Lee
Status is reachable
Marcus Lee
3rd+
Co-Founder & CTO at Acme
San Francisco Bay Area
Follow
Priya Raman
· 1st
Talent Acquisition Partner - Acme
Greater Minneapolis-St. Paul Area
Message"""


def test_paste_parser():
    people = parse_people(PASTE, "Acme")
    assert [(p.name, p.title, p.department) for p in people] == [
        ("Jane Doe", "Software Engineer", "engineering"), ("Marcus Lee", "Co-Founder & CTO", "executive"),
        ("Priya Raman", "Talent Acquisition Partner", "recruiting")]


# --- autopilot rules ---------------------------------------------------------------------------------------------------------

def test_autopilot_rules():
    s = AutopilotSettings(min_ai_score=70, max_saves_per_day=2)
    cands = [Candidate(1, 90, True, False), Candidate(2, 75, False, True, local=True), Candidate(3, 65, True, False),
             Candidate(4, 95, False, False), Candidate(5, 80, True, False, hs_ok=True), Candidate(6, 99, True, False, state="saved")]
    picked = pick_saves(cands, s, saved_today=0)
    assert [c.job_id for c in picked] == [5, 2]  # high-school-friendly + local pitch first; 3 (65), 4 (ineligible), 6 (saved) never
    assert pick_saves(cands, s, saved_today=2) == []
    assert AutopilotSettings.from_dict({"linkedin_results": 500, "cap_scores": -3}).linkedin_results == 50
    assert parse_limit("Claude AI usage limit reached|1762459200")[0]
    assert not parse_limit("Saved 15 scores.")[0]
    assert review_priority(True, 60, None, None) < review_priority(False, 95, None, None)  # pitches first
    assert review_priority(False, 95, None, None) < review_priority(False, 60, None, None)


def test_claude_runner_guards(tmp_path):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        class P:
            returncode, stdout, stderr = 0, "Saved 3 scores.", ""
        return P()
    r = claude_mod.run_command("score-jobs", "3", tmp_path, runner=fake_run, env={"PATH": "x"})
    assert r.status == "ok" and seen["cmd"][:3] == ["claude", "-p", "/score-jobs 3"]
    assert "--permission-mode" in seen["cmd"] and "dontAsk" in seen["cmd"] and "WebSearch" not in seen["cmd"]
    claude_mod.run_command("draft-outreach", "2", tmp_path, runner=fake_run, env={"PATH": "x"})
    assert "WebSearch" in seen["cmd"]
    refused = claude_mod.run_command("score-jobs", "3", tmp_path, runner=fake_run, env={"ANTHROPIC_API_KEY": "sk-x"})
    assert refused.status == "refused" and "ANTHROPIC_API_KEY" in refused.output

    def limited(cmd, **kw):
        class P:
            returncode, stdout, stderr = 1, "", "You've hit your limit · resets 3pm (America/Chicago)"
        return P()
    r = claude_mod.run_command("score-jobs", "3", tmp_path, runner=limited, env={"PATH": "x"})
    assert r.status == "limit" and r.reset.startswith("3pm")


# --- the autopilot run (fakes) --------------------------------------------------------------------------------------------------

def _seed_job(title, company, desc, ai_score=None, size=None):
    from jobsearch.filters import Evaluation, SearchSpec
    from jobsearch.match import MatchResult
    from jobsearch.models import Job
    from jobsearch.runner import RunReport, ScoredJob
    from storage.companies_repo import CompanyRepo
    from storage.jobs_repo import JobRepo
    repo = JobRepo()
    if size:
        CompanyRepo().set_size(company, size, "YC directory")
    search = repo.save_search(None, f"t-{company}", SearchSpec(), auto_run=False)
    job = Job("greenhouse", f"{company}-{title}", company, title, "https://boards.example/x", description=desc)
    job.flags = add_pitch(find_flags(desc), size)
    repo.save_results(search.id, 700 + search.id, RunReport(kept=[ScoredJob(job, Evaluation(True, [], 1.0, []), MatchResult(60, 0.5, 1.0))]))
    jid = repo.results(search.id)[0][0].id
    if ai_score is not None:
        from storage.apps_repo import AppRepo
        AppRepo().save_scores([{"job_id": jid, "score": ai_score, "reasoning": "fits well"}])
    return jid


def _autopilot(tmp_path, claude, **kw):
    from app.autopilot_run import Autopilot
    s = AutopilotSettings(source_yc=False, source_yc_small=False, source_builtin=False, source_hn=False, source_simplify=False,
                          size_checks_per_day=0, board_checks_per_day=0, finder_searches_per_day=0,
                          backup_dir=str(tmp_path / "backups"), **kw)
    return Autopilot(settings=s, claude=claude, run_searches=False, page_fn=lambda u: None,
                     detect_fn=lambda q, c: None, finder_fn=lambda company, domain: None)


@pytest.fixture()
def site():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_autopilot_limit_stop_and_catch_up(site, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _seed_job("Data Intern", "Unscored Co", "Python and SQL.")  # gives /score-jobs something to do
    calls = []

    def claude_limit(command, args):
        calls.append(command)
        return ClaudeResult("limit", "Claude AI usage limit reached|1762459200", reset="Thu 14:00")
    run = _autopilot(tmp_path, claude_limit).run("morning")
    log = {x["step"]: x for x in json.loads(run.log_json)}
    assert run.status == "stopped" and run.claude_status == "limit" and run.claude_reset == "Thu 14:00"
    assert calls == ["score-jobs"]  # nothing else was attempted once the limit hit
    assert log["scores"]["status"] == "limit" and log["maintenance"]["status"] == "ok"  # non-AI steps still ran
    assert any(Path(tmp_path / "backups").glob("jobhunt-*.db.gz"))
    # catch-up: only AI steps, no searches/discover
    calls.clear()
    run2 = _autopilot(tmp_path, lambda c, a: (calls.append(c), ClaudeResult("ok", "done"))[1]).run("catch-up")
    steps = [x["step"] for x in json.loads(run2.log_json)]
    assert log["discover"]["status"] == "skipped"  # all sources off: the real listings cache is left alone
    assert "discover" not in steps and "searches" not in steps and "scores" in steps and run2.status == "done"


def test_autopilot_refuses_with_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    run = _autopilot(tmp_path, lambda c, a: pytest.fail("must not call Claude")).run("morning")
    assert run.status == "refused" and "ANTHROPIC_API_KEY" in run.log_json


def test_autopilot_auto_save_and_pitch_defaults(site, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from storage.apps_repo import AppRepo
    from storage.outreach_repo import OutreachRepo
    good = _seed_job("Software Intern", "Good Fit Inc", "Python. Open to high school students.", ai_score=88)
    pitch = _seed_job("Data Intern", "Tiny Startup", "Must be currently enrolled in a bachelor's degree program.", ai_score=80, size=40)
    low = _seed_job("QA Intern", "Meh Co", "Testing.", ai_score=50)
    run = _autopilot(tmp_path, lambda c, a: ClaudeResult("ok", "ok"), max_saves_per_day=5).run("manual")
    apps = AppRepo()
    assert apps.for_job(good) and apps.for_job(pitch) and not apps.for_job(low)
    assert OutreachRepo().prefs("Tiny Startup").reach_out_first  # pitches go out before applying
    # pitch jobs never get application drafts before a yes
    assert pitch not in [j["job_id"] for j in apps.pending_drafts(20)["jobs"]]
    log = {x["step"]: x for x in json.loads(run.log_json)}
    assert log["auto_save"]["counts"]["pitches"] >= 1


def test_review_one_click_flow(site, monkeypatch):
    from storage.apps_repo import AppRepo
    from storage.outreach_repo import OutreachRepo
    from finder.models import FoundEmail
    from finder.patterns import detect_pattern
    from finder.store import DomainReport, SourceStatus
    from storage.jobs_repo import JobRepo
    from storage.repo import SqlStore
    jid = _seed_job("Support Intern", "Review Co", "Tech support. Open to high school students.", ai_score=85)
    JobRepo().add_company(name="Review Co", ats="greenhouse", board="reviewco", domain="reviewco.test", status="ok")
    emails = [FoundEmail("ana@reviewco.test", "reviewco.test", "personal", "website", "https://reviewco.test/team",
                         full_name="Ana Lead", title="Recruiter", department="recruiting")]
    SqlStore().save_report(DomainReport("reviewco.test", emails, detect_pattern("reviewco.test", emails), sources=[SourceStatus("website", "ok", "")]))
    apps, out = AppRepo(), OutreachRepo()
    app = apps.ensure(jid)
    apps.save_drafts([{"job_id": jid, "cover_letter": "Dear Review Co team,\n\nI do tech support.\n\nSincerely,\nAlex Rivera",
                       "answers": [], "notes": "", "words": 12}])
    c = out.pick(app.id, "ana@reviewco.test")
    out.save_drafts([{"contact_id": c.id, "hook": "h", "hook_source": "https://x", "hook_level": "company", "subject": "Hi",
                      "email": "Hi Ana,\n\nInternship.\n\nCall?", "followup1": "f1", "followup2": "f2", "linkedin_note": "n", "notes": ""}])
    page = site.get("/review").text
    assert "Support Intern" in page and "Looks good" in page and "Hi Ana" in page
    r = site.post(f"/review/{app.id}/looks-good")
    assert "application approved" in r.text and "1 outreach email approved" in r.text
    assert apps.get(app.id).status == "approved" and out.contact(c.id).status == "approved"
    assert out.outbox(c.id) == []  # normal job: email waits until applied
    r = site.post(f"/review/{app.id}/applied")
    assert apps.get(app.id).status == "applied" and out.outbox(c.id)[0].step == "first"
    # redraft queue + skip
    jid2 = _seed_job("Other Intern", "Skip Co", "Python.", ai_score=75)
    app2 = apps.ensure(jid2)
    site.post(f"/review/{app2.id}/redraft", data={"note": "shorter", "target": "app"})
    from app.autopilot_run import redraft_queue
    assert redraft_queue()[-1] == {**redraft_queue()[-1], "kind": "app", "id": jid2, "note": "shorter"}
    site.post(f"/review/{app2.id}/skip")
    assert apps.for_job(jid2) is None and apps.jobs.job(jid2).state == "dismissed"


def test_paste_people_endpoint(site):
    from storage.apps_repo import AppRepo
    from finder.models import FoundEmail
    from finder.patterns import detect_pattern
    from finder.store import DomainReport, SourceStatus
    from storage.jobs_repo import JobRepo
    from storage.repo import SqlStore
    jid = _seed_job("Eng Intern", "Paste Co", "Python.", ai_score=70)
    JobRepo().add_company(name="Paste Co", ats="lever", board="pasteco", domain="pasteco.test", status="ok")
    emails = [FoundEmail(f"{f}.{l}@pasteco.test", "pasteco.test", "personal", "website", f"https://pasteco.test/t#{i}", full_name=f"{f.title()} {l.title()}")
              for i, (f, l) in enumerate([("amy", "ng"), ("bob", "lee"), ("cy", "ford"), ("di", "wu")])]
    SqlStore().save_report(DomainReport("pasteco.test", emails, detect_pattern("pasteco.test", emails), sources=[SourceStatus("website", "ok", "")]))
    app = AppRepo().ensure(jid)
    r = site.post(f"/applications/{app.id}/paste-people", data={"text": PASTE.replace("Acme", "Paste Co")})
    assert "jane.doe@pasteco.test" in r.text and "marcus.lee@pasteco.test" in r.text and "Talent Acquisition Partner" in r.text
    r = site.post(f"/applications/{app.id}/add-person", data={"email": "jane.doe@pasteco.test", "name": "Jane Doe",
                                                             "title": "Software Engineer", "confidence": "70", "evidence": "pattern"})
    from storage.outreach_repo import OutreachRepo
    c = [x for x in OutreachRepo().contacts(app.id) if x.email == "jane.doe@pasteco.test"][0]
    assert c.confidence == 70 and c.evidence == "pattern"


# --- maintenance -----------------------------------------------------------------------------------------------------------------

def test_backup_and_cleanup(tmp_path, site):
    from app.autopilot_run import backup_database, cleanup_dismissed
    for i in range(16):
        assert backup_database(tmp_path, keep=14, today=f"2026-10-{i + 1:02d}")
    files = sorted(tmp_path.glob("jobhunt-*.db.gz"))
    assert len(files) == 14 and files[0].name == "jobhunt-2026-10-03.db.gz"
    assert gzip.open(files[-1]).read(16).startswith(b"SQLite format 3")
    assert backup_database(tmp_path, keep=14, today="2026-10-16") is None  # once a day
    from sqlmodel import Session
    from storage.db import engine
    from storage.tables import JobRow
    jid = _seed_job("Old Intern", "Dusty Co", "x")
    with Session(engine) as s:
        j = s.get(JobRow, jid)
        j.state, j.last_seen = "dismissed", (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(timespec="seconds")
        s.add(j)
        s.commit()
    assert cleanup_dismissed(60) >= 1
    with Session(engine) as s:
        assert s.get(JobRow, jid) is None


def test_interest_areas_match_loosely():
    from jobsearch.filters import _title_matches
    assert _title_matches("Software Engineer Intern", "Software Engineering")
    assert _title_matches("Data Scientist Intern", "Data Science")
    assert _title_matches("Machine Learning Engineer Intern", "AI / Machine Learning")
    assert not _title_matches("Mechanical Engineering Intern", "Software Engineering")
    assert not _title_matches("Marketing Intern", "IT")


def test_no_degree_and_company_size_filters():
    from jobsearch.filters import SearchSpec, evaluate
    from jobsearch.models import Job
    college = Job("simplify", "1", "BigCo", "Software Intern", "https://x", description="Candidates must be currently pursuing a Bachelor's degree.")
    open_ = Job("simplify", "2", "OpenCo", "Software Intern", "https://x", description="Degree level not listed; check the posting.")
    for j in (college, open_):
        j.flags = find_flags(j.description)
    spec = SearchSpec(no_degree_only=True, exclude_experience=[])
    assert not evaluate(college, spec).passes and evaluate(open_, spec).passes
    assert "requires college" in evaluate(college, spec).hidden_because[0]
    college.flags = add_pitch(college.flags, 40)
    assert evaluate(college, spec).passes  # pitch candidates stay
    size = SearchSpec(min_employees=10, max_employees=100, exclude_experience=[])
    assert evaluate(open_, size, employees=50).passes
    assert not evaluate(open_, size, employees=400).passes and not evaluate(open_, size, employees=5).passes
    assert evaluate(open_, size).passes  # unknown size stays by default
    assert not evaluate(open_, SearchSpec(max_employees=100, hide_unknown_size=True, exclude_experience=[])).passes


def test_search_form_saves_eligibility_and_size(site):
    r = site.post("/jobs/searches", data={"name": "nd", "include_keywords": "software", "eligibility": "no_degree",
                                          "min_employees": "10", "max_employees": "100", "hide_unknown_size": "on"})
    assert r.status_code == 204
    from storage.jobs_repo import JobRepo
    repo = JobRepo()
    spec = repo.spec_of([s for s in repo.searches() if s.name == "nd"][0])
    assert spec.no_degree_only and not spec.high_school_only
    assert (spec.min_employees, spec.max_employees, spec.hide_unknown_size) == (10, 100, True)
    assert "No degree requirement" in site.get("/jobs/searches/%d/edit" % [s for s in repo.searches() if s.name == "nd"][0].id).text


def test_full_posting_text_and_degree_wording():
    from jobsearch.describe import enrich, from_page
    from jobsearch.models import Job
    workday = {"jobPostingInfo": {"jobDescription": "<p>Build dashboards for our clinical teams.</p>" * 10 +
                                  "<p>Must be currently enrolled in a degree granting college program.</p>"}}
    calls = []

    def get_json(url):
        calls.append(url)
        return workday if "/wday/cxs/cch/careers/job/" in url else None
    page = ('<html><script type="application/ld+json">{"@type": "JobPosting", "description": "' + "<p>Analytics work.</p>" * 30 +
            '<p>Education Eligibility: You must be enrolled at an accredited college or university at the time of the internship. '
            'We primarily seek rising seniors graduating between August 2027 and June 2028.</p>"}</script></html>')
    jobs = [Job("simplify", "1", "Children's", "Data Intern", "https://cch.wd5.myworkdayjobs.com/en-US/careers/job/Cincinnati/Data-Intern_R1",
                description="Internship at Children's. Degree level not listed on the Simplify list; check the posting."),
            Job("simplify", "2", "CenturyLink", "Data Intern", "https://jobs.example.com/job/2", description="summary")]
    cache = {}
    assert enrich(jobs, get_json, lambda u: (u, page), cache) == 2
    assert calls[0].startswith("https://cch.wd5.myworkdayjobs.com/wday/cxs/cch/careers/job/Cincinnati/")
    assert "degree granting" in jobs[0].description and "From the listing:" in jobs[0].description
    assert [f.key for f in find_flags(jobs[0].description)][0] == "enrolled"
    keys = [f.key for f in find_flags(jobs[1].description)]
    assert "college" in keys and "grad_year" in keys and "hs_ok" not in keys  # college "rising seniors" isn't high school
    assert enrich([Job("simplify", "1", "x", "y", jobs[0].url, description="s")], lambda u: pytest.fail("cached"), None, cache) == 1
    assert from_page("<html><body><p>tiny</p></body></html>") is None
    assert [f.key for f in find_flags("Open to rising seniors in high school.")] == ["hs_ok"]
    for text in ("Applicants must be a current undergraduate student.", "Must be enrolled full-time in a four-year university.",
                 "Must have completed at least two years of college coursework."):
        assert [f.key for f in find_flags(text)] == ["college"], text
