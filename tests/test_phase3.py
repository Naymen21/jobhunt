"""Phase 3: job sources, budget guard, normalisation, flags, match score, dedupe, ATS detection, resumes,
and the Jobs / Settings pages. Every network call goes to saved responses through httpx.MockTransport.

Fixtures in tests/fixtures/jobs/: greenhouse_jobs.json, lever_postings.json and ashby_jobs.json are trimmed real
responses (2026-11-05). apify_items.json follows the approved actor's documented output fields."""
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("JOBHUNT_DB", os.path.join(tempfile.mkdtemp(), "test.db"))

import httpx  # noqa: E402
import pytest  # noqa: E402

from jobsearch.config import JobSettings  # noqa: E402
from jobsearch.dedupe import dedupe  # noqa: E402
from jobsearch.detect import board_guesses, boards_in, detect_ats  # noqa: E402
from jobsearch.filters import SearchSpec, evaluate  # noqa: E402
from jobsearch.flags import find_flags  # noqa: E402
from jobsearch.match import match, skills_in  # noqa: E402
from jobsearch.models import Job, Pay, WatchCompany  # noqa: E402
from jobsearch.normalize import detect_job_type, detect_work_mode, html_to_text, pay_label, parse_pay  # noqa: E402
from jobsearch.resume import ResumeError, extract_text  # noqa: E402
from jobsearch.runner import run_search  # noqa: E402
from jobsearch.sources import FEEDS, ApifyQuery, ApifySource, FeedClient, FeedError  # noqa: E402

FX = Path(__file__).parent / "fixtures" / "jobs"


def load(name):
    return json.loads((FX / name).read_text(encoding="utf-8"))


def fake_api(limits=None, limits_status=200, run_items=None, calls=None):
    """One MockTransport for all four sources. `calls` collects every request for assertions."""
    calls = calls if calls is not None else []

    def handler(req: httpx.Request):
        calls.append(req)
        host, path = req.url.host, req.url.path
        if host == "boards-api.greenhouse.io":
            if path == "/v1/boards/riotgames/jobs":
                return httpx.Response(200, json=load("greenhouse_jobs.json"))
            if path == "/v1/boards/riotgames":
                return httpx.Response(200, json=load("greenhouse_board.json"))
            return httpx.Response(404, json={"status": 404, "error": "Job not found"})
        if host == "api.lever.co":
            if path == "/v0/postings/jamcity":
                return httpx.Response(200, json=load("lever_postings.json"))
            return httpx.Response(404, json={"ok": False, "error": "Document not found"})
        if host == "api.ashbyhq.com":
            if path == "/posting-api/job-board/linear":
                return httpx.Response(200, json=load("ashby_jobs.json"))
            return httpx.Response(404, text="Not Found")
        if host == "api.apify.com":
            if path == "/v2/users/me/limits":
                return httpx.Response(limits_status, json=limits or {})
            if path.endswith("/run-sync-get-dataset-items"):
                return httpx.Response(201, json=run_items if run_items is not None else load("apify_items.json"))
        return httpx.Response(500)
    return httpx.MockTransport(handler), calls


def settings(**kw):
    s = JobSettings()
    s.feed_delay = 0
    s.apify_token = ""
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def client(transport, **kw):
    return FeedClient(settings(**kw), transport=transport)


def limits(used, limit=5.0):
    return {"data": {"monthlyUsageCycle": {"startAt": "2026-11-01T00:00:00.000Z", "endAt": "2026-11-30T23:59:59.999Z"},
                     "limits": {"maxMonthlyUsageUsd": limit}, "current": {"monthlyUsageUsd": used}}}


# --- feeds -------------------------------------------------------------------------------------

def test_greenhouse_feed():
    t, calls = fake_api()
    jobs = FEEDS["greenhouse"].fetch(WatchCompany("Riot Games", "greenhouse", "riotgames"), client(t))
    assert len(jobs) == 3 and calls[0].url.params["content"] == "true"
    j = jobs[0]
    assert j.source == "greenhouse" and j.company == "Riot Games" and "Anti-Cheat" in j.title
    assert j.url.startswith("https://") and "Los Angeles" in j.location
    assert j.pay and j.pay.min == 229200 and j.pay.max == 319500 and j.pay.period == "year"  # from the custom pay field
    assert j.posted_at.startswith("2026-10-")
    assert "Responsibilities:" in j.description and "&lt;" not in j.description and "\n- " in j.description
    assert jobs[2].job_type == "contract"  # "(Contract)" in the title


def test_lever_feed():
    t, _ = fake_api()
    jobs = FEEDS["lever"].fetch(WatchCompany("Jam City", "lever", "jamcity"), client(t))
    j = jobs[0]
    assert (j.title, j.location, j.work_mode, j.job_type) == ("2D Animator", "Montevideo", "hybrid", "full_time")
    assert j.posted_at == "2026-09-18T14:37:00+00:00" and j.apply_url.endswith("/apply") and j.department == "Art"


def test_lever_salary_range():
    j = FEEDS["lever"].to_job({"id": "x", "text": "QA Tester", "hostedUrl": "https://jobs.lever.co/a/x", "categories": {},
                               "salaryRange": {"min": 20, "max": 25, "currency": "USD", "interval": "per-hour-wage"}},
                              WatchCompany("A"))
    assert (j.pay.min, j.pay.max, j.pay.period) == (20, 25, "hour") and pay_label(j.pay) == "$20–$25/hr"


def test_ashby_feed():
    t, _ = fake_api()
    jobs = FEEDS["ashby"].fetch(WatchCompany("Linear", "ashby", "linear"), client(t))
    assert all(j.work_mode == "remote" and j.job_type == "full_time" for j in jobs)
    paid = [j for j in jobs if j.pay]
    assert paid and (paid[0].pay.min, paid[0].pay.max) == (240000, 320000)


def test_feed_errors_are_reported():
    t, _ = fake_api()
    with pytest.raises(FeedError, match="404"):
        FEEDS["lever"].fetch(WatchCompany("Nope", "lever", "nope"), client(t))
    assert not FEEDS["ashby"].probe("nope", client(t)).ok
    p = FEEDS["greenhouse"].probe("riotgames", client(t))
    assert p.ok and p.jobs == 3 and p.company_name == "Riot Games"


# --- Apify + budget guard --------------------------------------------------------------------------

def test_apify_not_configured():
    t, calls = fake_api()
    src = ApifySource(settings())
    check = src.check_budget(25, client(t))
    assert not src.configured and not check.ok and "not configured" in check.reason and calls == []


def test_apify_budget_passes_and_caps_the_run():
    t, calls = fake_api(limits=limits(1.00))
    s = settings(apify_token="apify_api_TESTTOKEN123456")
    src = ApifySource(s)
    c = FeedClient(s, transport=t)
    check = src.check_budget(25, c)
    assert check.ok and check.max_results == 25
    assert check.estimate_usd == pytest.approx(25 * 0.002 + 0.00005)
    assert check.cap_usd == pytest.approx(round(check.estimate_usd * 1.2, 4))
    run = src.search(ApifyQuery("gameplay programmer", "Los Angeles", 7, 25), c, check)
    assert run.items == 2 and len(run.jobs) == 2
    req = calls[-1]
    assert req.url.params["maxItems"] == "25" and float(req.url.params["maxTotalChargeUsd"]) == pytest.approx(check.cap_usd)
    assert "curious_coder~linkedin-jobs-scraper" in req.url.path
    body = json.loads(req.content)
    assert body == {"keywords": "gameplay programmer", "location": "Los Angeles", "datePosted": "pastWeek",
                    "limitPerSource": 25, "scrapeCompany": False}
    # the token travels only in the Authorization header, never in a URL; no LinkedIn credentials anywhere
    for r in calls:
        assert "TESTTOKEN" not in str(r.url) and r.headers["authorization"] == "Bearer apify_api_TESTTOKEN123456"
    assert not any(k in json.dumps(body).lower() for k in ("cookie", "li_at", "password", "session"))


def test_apify_refuses_near_free_credit():
    t, calls = fake_api(limits=limits(3.97))  # 3.97 + 0.05 > 80% of 5.00
    s = settings(apify_token="apify_api_TESTTOKEN123456")
    src = ApifySource(s)
    c = FeedClient(s, transport=t)
    check = src.check_budget(25, c)
    assert not check.ok and "80%" in check.reason and check.usage.used_usd == 3.97
    with pytest.raises(FeedError):
        src.search(ApifyQuery("x"), c, check)
    assert all("run-sync" not in str(r.url) for r in calls)  # no run was ever started


def test_apify_refuses_when_usage_check_fails():
    t, calls = fake_api(limits_status=500)
    s = settings(apify_token="apify_api_TESTTOKEN123456")
    check = ApifySource(s).check_budget(10, FeedClient(s, transport=t))
    assert not check.ok and "not running to be safe" in check.reason


def test_apify_max_results_is_clamped():
    src = ApifySource(settings())
    assert src.clamp(None) == 25 and src.clamp(500) == 50 and src.clamp(0) == 25 and src.clamp(10) == 10


def test_apify_item_normalisation():
    j = ApifySource(settings()).to_job(load("apify_items.json")[1])
    assert (j.company, j.work_mode, j.job_type) == ("Indie Forge Studios", "onsite", "internship")
    assert (j.pay.min, j.pay.max, j.pay.period) == (28, 34, "hour")
    assert j.posted_at == "2026-11-02T10:00:00+00:00" and j.url.startswith("https://www.linkedin.com/jobs/view/")


# --- normalisation ------------------------------------------------------------------------------

@pytest.mark.parametrize("text,label", [
    ("Base salary range between $246,300.00 - $343,400.00 USD + equity", "$246K–$343K/yr"),
    ("The pay range is $70k-90k.", "$70K–$90K/yr"),
    ("Hourly rate: $35/hr", "$35/hr"),
    ("$22 - $26 per hour", "$22–$26/hr"),
    ("Salary: £45,000 to £55,000", "£45K–£55K/yr"),
    ("$120,000 – $150,000 CAD", "CA$120K–CA$150K/yr"),
    ("We raised $50 million from investors", ""),
    ("Over $3 billion in revenue", ""),
    ("No numbers here", ""),
])
def test_parse_pay(text, label):
    assert pay_label(parse_pay(text)) == label


def test_pay_prefers_the_amount_next_to_pay_words():
    p = parse_pay("We have 100,000 players and raised $40,000,000. Compensation: $90,000 - $110,000 per year.")
    assert (p.min, p.max) == (90000, 110000)


def test_work_mode_and_job_type():
    assert detect_work_mode("Remote - US") == "remote"
    assert detect_work_mode("Seattle (Hybrid)") == "hybrid"
    assert detect_work_mode("Austin, TX", description="This role is on-site five days a week.") == "onsite"
    assert detect_work_mode("Austin, TX", description="We like remote tools.") is None
    assert detect_job_type("Software Engineering Intern, Summer 2027") == "internship"
    assert detect_job_type("Gameplay Engineer (Contract)", "Full Time") == "contract"
    assert detect_job_type("Community Manager", "Part-time") == "part_time"
    assert detect_job_type("Engineer", "FullTime") == "full_time"
    assert detect_job_type("Engineer") is None


def test_html_to_text_keeps_inline_text_together():
    assert html_to_text("<p>As an <strong>Engineer</strong> you will</p><ul><li>build</li><li>ship</li></ul>") == \
        "As an Engineer you will\n\n- build\n- ship"


# --- eligibility flags ----------------------------------------------------------------------------

def test_flags_each_kind():
    text = """Requirements
- Currently pursuing a Bachelor's degree in Computer Science.
- Expected graduation between December 2026 and June 2027.
- Must be 18 years of age or older.
- Must be authorized to work in the United States without visa sponsorship.
- Must be a U.S. citizen and able to obtain a security clearance.
- 3+ years of professional C++ experience."""
    flags = {f.key: f for f in find_flags(text)}
    assert set(flags) == {"enrolled", "grad_year", "age", "degree", "work_auth", "citizenship", "experience"}
    assert flags["grad_year"].label == "Grad year 2026" and flags["age"].label == "Must be 18+"
    assert flags["degree"].label == "Bachelor's required" and flags["experience"].label == "3+ yrs experience"
    assert "visa sponsorship" in flags["work_auth"].snippet


def test_flags_soft_degree_and_small_experience():
    flags = {f.key: f for f in find_flags("BS in Computer Science or equivalent experience. 1 year of experience with Unity.")}
    assert flags["degree"].label == "Bachelor's preferred" and "experience" not in flags


def test_no_flags_on_plain_text():
    assert find_flags("We make games. You will write gameplay code and work with designers.") == []
    # seen on Riot's live board: a job title, not a degree
    assert find_flags("Experience as a Scrum Master in a technology setting will also be considered.") == []
    assert find_flags("You will work with an Associate Producer in Seattle.") == []
    assert find_flags("Master’s degree in Computer Science required.")[0].label == "Master's required"


# --- match + filters --------------------------------------------------------------------------------

def make_job(**kw):
    base = dict(source="greenhouse", source_id="1", company="Acme", title="Gameplay Programmer", url="https://x/1",
                location="Los Angeles, CA", description="We use Unity, C# and Git. Experience with C++ is a plus.")
    base.update(kw)
    return Job(**base)


def test_skills_matching_is_careful():
    assert skills_in("Unity, C#, C++ and Git") == {"unity", "c#", "c++", "git"}
    assert skills_in("go to the store, R&D team, rest of the day") == set()
    assert "Go" in skills_in("Backend services in Go and Rust") and not skills_in("Series C funding")
    assert "C/C++" in skills_in("Strong C/C++ skills") and "C/C++" in skills_in("C programming")


def test_match_score_with_and_without_resume():
    job = make_job(title="Tools Programmer")
    ev = evaluate(job, SearchSpec(locations=["Los Angeles"]))
    m = match(job.title, job.description, ev, skills_in("Projects in Unity and C#. Git daily."))
    assert m.matched == ["c#", "git", "unity"] and m.missing == ["c++"] and m.keyword_share == 0.6  # 3 of max(4, 5)
    assert m.score == round(100 * (0.7 * 0.6 + 0.3 * 1.0))
    thin = match("Sales", "Great communication skills.", ev, {"communication"})
    assert thin.keyword_share == 0.05 and thin.score < 50  # 0.25 weight over a minimum of 5
    none = match(job.title, job.description, ev, None)
    assert none.score <= 50 and "no default resume" in none.notes[0]


def test_live_linkedin_shapes():
    """Shapes seen in the real Apify run on 2026-11-05: one salary string, date-only posted, mode only in the text."""
    src = ApifySource(settings())
    j = src.to_job({"id": "1", "title": "Game Engine Engineer", "companyName": "Worldscape", "location": "Redmond, WA",
                    "link": "https://www.linkedin.com/jobs/view/1", "postedAt": "2026-11-04",
                    "salary": "$100,000.00/yr - $130,000.00/yr", "employmentType": "Full-time",
                    "descriptionText": "This position is hybrid in Redmond, WA. Hybrid positions require regular onsite days."})
    assert (j.pay.min, j.pay.max, j.pay.period) == (100000, 130000, "year") and j.work_mode == "hybrid"
    assert detect_work_mode("Irvine, CA", description="a mix of work-from-home and on-site days, based in Irvine") == "hybrid"
    assert detect_work_mode("Middleton, WI", description="follows an onsite work schedule of four days per week") == "hybrid"
    from datetime import datetime, timezone
    now = datetime(2026, 11, 6, 1, 0, tzinfo=timezone.utc)
    assert evaluate(make_job(posted_at="2026-10-30T00:00:00+00:00"), SearchSpec(posted_within_days=7), now=now).passes
    us = SearchSpec(locations=["United States"])
    assert evaluate(make_job(location="Redmond, WA"), us).passes and evaluate(make_job(location="Austin, Texas"), us).passes
    assert not evaluate(make_job(location="Toronto, ON"), us).passes
    assert evaluate(make_job(location="Irvine, CA"), SearchSpec(locations=["California"])).passes


def test_filters_hide_clear_misses_only():
    spec = SearchSpec(include_keywords=["programmer"], work_modes=["remote"], min_pay=60000, locations=["USA", "Remote"],
                      posted_within_days=7)
    unknown = make_job(location="", work_mode=None, pay=None, posted_at=None)
    ev = evaluate(unknown, spec)
    assert ev.passes and ev.fit == 0.5 and "pay not listed" in ev.fit_notes
    assert not evaluate(make_job(work_mode="onsite"), spec).passes
    assert not evaluate(make_job(title="Senior Gameplay Programmer"), SearchSpec()).passes  # default excludes senior
    assert not evaluate(make_job(pay=Pay(20, 25, period="hour")), spec).passes  # 25 x 2080 < 60k
    assert evaluate(make_job(pay=Pay(30, 35, period="hour"), work_mode="remote", location="Remote"), spec).passes
    assert not evaluate(make_job(), SearchSpec(exclude_experience=["3+ years"]), years_required=4).passes
    assert evaluate(make_job(location="Los Angeles, USA"), SearchSpec(locations=["United States"])).passes


def test_dedupe_merges_across_sources_only():
    feed = make_job(company="Jam City", title="2D Animator", location="Montevideo", description="long " * 100)
    linkedin = make_job(source="apify", source_id="li1", company="Jam City, Inc.", title="2D Animator",
                        location="Montevideo, Uruguay", description="short", pay=Pay(1000, 2000, period="month"))
    twin = make_job(source_id="2", company="Jam City", title="2D Animator", location="Montevideo")
    out = dedupe([feed, linkedin, twin])
    assert len(out) == 2  # the LinkedIn copy folds into a feed copy; the second feed posting stays separate
    merged = next(j for j in out if len(j.sources) == 2)
    assert {s.source for s in merged.sources} == {"greenhouse", "apify"}
    assert merged.description.startswith("long") and merged.pay.min == 1000  # richer text kept, gap filled


# --- run_search ------------------------------------------------------------------------------------

WATCH = [WatchCompany("Riot Games", "greenhouse", "riotgames", id=1), WatchCompany("Jam City", "lever", "jamcity", id=2),
         WatchCompany("Linear", "ashby", "linear", id=3), WatchCompany("Gone", "lever", "gone", id=4),
         WatchCompany("Supergiant Games", None, None, id=5)]


def test_run_search_all_sources():
    t, _ = fake_api(limits=limits(0.10))
    s = settings(apify_token="apify_api_TESTTOKEN123456")
    events = []
    report = run_search(SearchSpec(exclude_experience=[], sources=["watchlist", "apify"]), WATCH,
                        client=FeedClient(s, transport=t), apify=ApifySource(s), resume_text="Unity C# Git animation",
                        progress=events.append)
    status = {st.source: st for st in report.statuses}
    assert status["greenhouse:riotgames"].status == "ok" and status["lever:gone"].status == "error"
    assert "ashby:linear" in status and "supergiant" not in json.dumps([st.source for st in report.statuses])
    assert status["apify"].status == "ok" and report.apify_items == 2
    assert report.fetched == 3 + 3 + 3 + 2 and report.merged == 1  # LinkedIn's Jam City 2D Animator = the Lever job
    titles = [k.job.title for k in report.kept]
    assert "Gameplay Programmer Intern" in titles
    intern = next(k for k in report.kept if k.job.title == "Gameplay Programmer Intern")
    assert {f.key for f in intern.job.flags} >= {"enrolled", "grad_year", "age", "degree"}
    assert any(e.get("total_sources") == 5 for e in events)


def test_run_search_apify_not_configured_and_filters():
    t, _ = fake_api()
    report = run_search(SearchSpec(sources=["watchlist", "apify"], companies=[2], exclude_keywords=["associate"]), WATCH,
                        client=client(t), apify=ApifySource(settings()))
    assert [st.source for st in report.statuses] == ["lever:jamcity", "apify"]
    assert report.statuses[1].status == "not_configured"
    assert report.hidden == 1 and [k.job.title for k in report.kept] == ["2D Animator", "3D Artist"]


# --- ATS detection --------------------------------------------------------------------------------

def test_boards_in_and_guesses():
    html = '<a href="https://job-boards.greenhouse.io/riotgames/jobs/123">Jobs</a> <script src="https://boards.greenhouse.io/embed/job_board/js?for=riotgames"></script>'
    assert boards_in(html) == [("greenhouse", "riotgames")]
    assert boards_in("https://jobs.lever.co/jamcity/abc") == [("lever", "jamcity")]
    assert boards_in("https://jobs.ashbyhq.com/linear?x=1") == [("ashby", "linear")]
    assert board_guesses("Insomniac Games", "insomniac.games") == ["insomniacgames", "insomniac-games", "insomniac"]


def test_detect_from_board_link():
    t, _ = fake_api()
    det = detect_ats("https://jobs.lever.co/jamcity", client(t), resolve_fn=lambda *a: pytest.fail("no lookup needed"))
    assert (det.status, det.ats, det.board, det.jobs) == ("found", "lever", "jamcity", 3)


def test_detect_from_careers_page():
    from finder.resolve import Resolution
    t, _ = fake_api()
    pages = {"https://riotgames.com/careers": ("https://www.riotgames.com/en/work-with-us",
                                               '<iframe src="https://boards.greenhouse.io/embed/job_board?for=riotgames"></iframe>')}
    det = detect_ats("Riot Games", client(t), resolve_fn=lambda q, s: Resolution(q, "riot games", "resolved", domain="riotgames.com"),
                     page_fn=pages.get)
    assert (det.status, det.ats, det.board, det.company_name) == ("found", "greenhouse", "riotgames", "Riot Games")
    assert "linked from" in det.how


def test_detect_guessing_prefers_the_biggest_board():
    from finder.resolve import Resolution

    def handler(req):
        if req.url.host == "boards-api.greenhouse.io" and req.url.path == "/v1/boards/ramp/jobs":
            return httpx.Response(200, json={"jobs": [{"id": 1}]})  # someone else's tiny board
        if req.url.host == "boards-api.greenhouse.io" and req.url.path == "/v1/boards/ramp":
            return httpx.Response(200, json={"name": "Ramp Inc"})
        if req.url.host == "api.ashbyhq.com" and req.url.path == "/posting-api/job-board/ramp":
            return httpx.Response(200, json={"jobs": [{"id": str(i), "title": "x", "isListed": True} for i in range(5)]})
        return httpx.Response(404)
    det = detect_ats("Ramp", client(httpx.MockTransport(handler)),
                     resolve_fn=lambda q, s: Resolution(q, "ramp", "not_found"), page_fn=lambda u: None)
    assert (det.ats, det.board, det.jobs) == ("ashby", "ramp", 5) and "also found Greenhouse 'ramp' (1 jobs)" in det.how


def test_detect_by_guessing_then_not_found():
    from finder.resolve import Resolution
    t, _ = fake_api()
    det = detect_ats("Linear", client(t), resolve_fn=lambda q, s: Resolution(q, "linear", "not_found"), page_fn=lambda u: None)
    assert (det.status, det.ats, det.board) == ("found", "ashby", "linear") and "guessed" in det.how
    det = detect_ats("Supergiant Games", client(t), resolve_fn=lambda q, s: Resolution(q, "x", "resolved", domain="supergiantgames.com"),
                     page_fn=lambda u: None)
    assert det.status == "not_found" and any("guessed board names" in x for x in det.tried)


# --- resumes ---------------------------------------------------------------------------------------

def make_docx(paragraphs):
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


def test_resume_pdf_and_docx():
    kind, text = extract_text("report.pdf", (Path(__file__).parent / "fixtures" / "report.pdf").read_bytes())
    assert kind == "pdf" and "Rachel Green" in text
    kind, text = extract_text("me.docx", make_docx(["Jane Doe", "Skills: Unity, C#, Blender"]))
    assert kind == "docx" and text == "Jane Doe\nSkills: Unity, C#, Blender"
    for name, data, msg in [("a.doc", b"\xd0\xcf", ".docx"), ("a.txt", b"hi", "PDF"), ("a.pdf", b"", "empty")]:
        with pytest.raises(ResumeError, match=msg):
            extract_text(name, data)


# --- website ---------------------------------------------------------------------------------------

@pytest.fixture()
def site(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.routes import jobs as jobs_routes
    import jobsearch.config
    monkeypatch.setattr(jobsearch.config.settings, "apify_token", "")
    monkeypatch.setattr(jobsearch.config.settings, "feed_delay", 0)
    with TestClient(app) as c:
        yield c, jobs_routes


def test_settings_profile_resume_keys(site, monkeypatch):
    c, _ = site
    r = c.post("/settings/profile", data={"name": "Jane Doe", "grad_year": "2027", "about": "Gameplay roles"})
    assert r.status_code == 200 and "Saved" in r.text and 'value="Jane Doe"' in r.text
    r = c.post("/settings/profile", data={"name": "Jane Doe", "linkedin_url": "www.linkedin.com/in/jane-doe", "github_url": ""})
    assert 'value="https://www.linkedin.com/in/jane-doe"' in r.text and 'type="url"' not in r.text
    r = c.post("/settings/resumes", data={"label": "Game dev"},
               files={"file": ("me.docx", make_docx(["Jane Doe", "Unity, C#, Git"]), "application/octet-stream")})
    assert r.status_code == 200 and "Game dev" in r.text and "Default" in r.text and "unity" in r.text
    r = c.post("/settings/resumes", files={"file": ("old.doc", b"\xd0\xcf\x11", "application/msword")})
    assert ".docx" in r.text and "inline-error" in r.text
    monkeypatch.setenv("APIFY_TOKEN", "apify_api_SECRETVALUE9876")
    page = c.get("/settings?tab=keys").text
    assert "APIFY_TOKEN" in page and "SECRETVALUE" not in page and "9876" not in page and 'name="APIFY_TOKEN"' not in page
    assert "badge-valid\">Set" in page
    assert "Not configured" in c.get("/settings/usage/apify").text  # engine settings still have no token


def test_jobs_flow(site, monkeypatch):
    c, jobs_routes = site
    from app.jobs import job_runs
    from storage.jobs_repo import JobRepo

    repo = JobRepo()
    watch = {w.name: w for w in repo.watchlist()}
    assert watch["Riot Games"].board == "riotgames" and watch["Supergiant Games"].status == "no_feed"
    assert watch["Valve"].ats is None  # no-feed companies stay on the list as "check manually"
    wl = c.get("/jobs?tab=watchlist").text
    assert "Check manually" in wl and "Riot Games" in wl

    r = c.post("/jobs/searches", data={"name": "Art and games", "sources": "watchlist", "company_scope": "some",
                                       "companies": [str(watch["Riot Games"].id), str(watch["Jam City"].id)],
                                       "exclude_experience": "senior", "auto_run": "on"})
    sid = int(r.headers["hx-redirect"].split("=")[1])
    assert "hasn't run yet" in c.get(f"/jobs?search={sid}").text

    t, _ = fake_api()
    run = job_runs.execute(job_runs.create(sid), sid, transport=t)
    assert run.status == "done" and run.kept == 6 and run.new_jobs == 6, run.error
    page = c.get(f"/jobs?search={sid}").text
    assert "2D Animator" in page and "Anti-Cheat" in page and page.count('data-new="1"') == 6
    assert "Greenhouse" in page and "Lever" in page

    run2 = job_runs.execute(job_runs.create(sid), sid, transport=t)
    assert run2.new_jobs == 0
    assert c.get(f"/jobs/searches/{sid}/results").text.count('data-new="1"') == 0

    job_id = repo.results(sid)[0][0].id
    detail = c.get(f"/jobs/job/{job_id}?search={sid}").text
    assert "Open original" in detail and "Find people here" in detail and "depts=recruiting%2Cengineering" in detail
    assert "run /score-jobs" in detail
    r = c.post(f"/jobs/job/{job_id}/state?search={sid}", data={"state": "saved"})
    assert "Unsave" in r.text and json.loads(r.headers["HX-Trigger"])["jobStateChanged"]["state"] == "saved"
    c.post(f"/jobs/job/{job_id}/state?search={sid}", data={"state": "dismissed"})
    assert repo.job(job_id).state == "dismissed"
    assert c.post(f"/jobs/job/{job_id}/state", data={"state": "applied"}).status_code == 400

    # the watchlist row was refreshed by the run
    assert repo.company(watch["Riot Games"].id).job_count == 3


def test_watchlist_add_manual_and_remove(site, monkeypatch):
    c, jobs_routes = site
    from jobsearch.detect import Detection
    monkeypatch.setattr(jobs_routes, "detect_ats", lambda q, client, store=None: Detection(
        "found", q, "ashby", "linear", "", 31, "guessed board name 'linear' and confirmed it", "linear.app"))
    r = c.post("/jobs/watchlist", data={"query": "Linear App"})
    assert "already on the watchlist" in r.text  # Linear is in the starter list
    monkeypatch.setattr(jobs_routes, "detect_ats", lambda q, client, store=None: Detection(
        "not_found", q, tried=["company website: moonstudios.com", "guessed board names moonstudios"]))
    r = c.post("/jobs/watchlist", data={"query": "Moonlight Studio"})
    assert "No Greenhouse, Lever or Ashby board found" in r.text and "Tried: company website" in r.text
    from storage.jobs_repo import JobRepo
    row = next(w for w in JobRepo().watchlist() if w.name == "Moonlight Studio")
    t, _ = fake_api()
    real_client = jobs_routes.FeedClient
    monkeypatch.setattr(jobs_routes, "FeedClient", lambda s: real_client(s, transport=t))
    r = c.post(f"/jobs/watchlist/{row.id}/manual", data={"ats": "greenhouse", "board": "https://job-boards.greenhouse.io/riotgames"})
    assert "works, 3 open jobs" in r.text
    r = c.post(f"/jobs/watchlist/{row.id}/test")
    assert "feed works, 3 open jobs" in r.text
    r = c.post("/jobs/watchlist/test-all")  # the fake API only knows riotgames, jamcity and linear
    assert "Checked 24 feeds: 4 working, 20 failed" in r.text and "Feed failed" in r.text
    c.post(f"/jobs/watchlist/{row.id}/delete")
    assert all(w.id != row.id for w in JobRepo().watchlist())
