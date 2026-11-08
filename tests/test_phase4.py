"""Phase 4: the AI handoff CLI, draft checks, the pipeline, exports, the answer bank, and the high-school additions.
Everything is offline: no AI is called anywhere (the CLI only reads and writes the database)."""
import io
import json
import os
import tempfile
import zipfile
from datetime import date

os.environ.setdefault("JOBHUNT_DB", os.path.join(tempfile.mkdtemp(), "test.db"))

import pytest  # noqa: E402

from applications import cli  # noqa: E402
from applications.export import Letterhead, file_name, to_docx, to_pdf  # noqa: E402
from applications.status import StatusError, check_move, follow_up_due  # noqa: E402
from applications.validate import check_drafts, check_scores  # noqa: E402
from jobsearch.filters import SearchSpec, evaluate  # noqa: E402
from jobsearch.flags import eligible_for_high_schooler, find_flags  # noqa: E402
from jobsearch.match import match  # noqa: E402
from jobsearch.models import Job  # noqa: E402

LETTER = ("Dear Riot Games team,\n\nLast summer I rebuilt a local nonprofit's website and added a "
          "chatbot that answers member questions.\n\nI'm a high school senior heading into a CS degree.\n\nSincerely,\nAlex Rivera")


# --- flags for a high school senior ---------------------------------------------------------------

def test_high_school_flags():
    keys = lambda text: [f.key for f in find_flags(text)]  # noqa: E731
    assert keys("This program is open to high school students and incoming freshmen.") == ["hs_ok"]
    assert keys("High school diploma or equivalent required.") == []
    assert keys("Currently enrolled in a bachelor's degree program in Computer Science.")[0] == "enrolled"
    assert keys("Must have a declared major in data science.") == ["enrolled"]
    assert keys("Must be currently enrolled in a college or university.") == ["college"]
    assert find_flags("Must be currently enrolled in a college.")[0].label == "College enrollment required"
    for text in ["For high schoolers ages 16-18.", "Open to rising seniors.", "Students must be at least 16 years old.",
                 "A paid summer youth employment program."]:
        assert keys(text) == ["hs_ok"], text
    assert [f.key for f in find_flags("", title="High School Summer Intern")] == ["hs_ok"]
    assert keys("This is a paid role for a local college or MBA student.") == ["college"]
    assert keys("We are an equal opportunity employer regardless of race, citizenship or disability.") == []
    assert keys("Must be a U.S. citizen.") == ["citizenship"]


def test_eligible_for_me():
    ok = lambda text: eligible_for_high_schooler(find_flags(text))  # noqa: E731
    assert not ok("Must be currently enrolled in a college or university.")  # PSEO doesn't count (owner's call)
    assert ok("Must be currently enrolled in college. Incoming freshmen are welcome to apply.")  # unless it welcomes them
    assert ok("Bachelor's degree preferred.")
    assert not ok("Bachelor's degree in Computer Science required.")
    assert not ok("Expected graduation in 2028.")
    assert not ok("Currently pursuing a bachelor's degree.")
    assert ok("Expected graduation in 2028. High school students are welcome to apply.")  # the positive tag wins
    assert not eligible_for_high_schooler(find_flags("PhD preferred.", title="PhD Intern, Data Science"))
    assert find_flags("", title="MBA Intern")[0].label == "MBA required"


def test_high_school_only_search():
    hs = Job("ashby", "1", "Acme", "Summer Intern", "https://x", description="Open to high school students.")
    other = Job("ashby", "2", "Acme", "Summer Intern", "https://x", description="Python.")
    hs.flags, other.flags = find_flags(hs.description), find_flags(other.description)
    spec = SearchSpec(high_school_only=True, exclude_experience=[])
    assert evaluate(hs, spec).passes and not evaluate(other, spec).passes
    assert "high school" in evaluate(other, spec).hidden_because[0]
    from jobsearch.flags import add_pitch
    small = Job("ashby", "3", "Tiny", "Summer Intern", "https://x", description="Must be currently enrolled in a bachelor's degree program.")
    small.flags = add_pitch(find_flags(small.description), 40)
    assert evaluate(small, spec).passes  # pitch candidates stay in high-school-only searches


def test_high_school_bonus_and_required_keywords():
    job = Job("ashby", "1", "Acme", "Software Engineering Intern", "https://x", description="Python and SQL.")
    spec = SearchSpec(require_keywords=["intern", "internship"], exclude_experience=[])
    ev = evaluate(job, spec)
    base = match(job.title, job.description, ev, {"python"})
    boosted = match(job.title, job.description, ev, {"python"}, find_flags("Open to high school students."))
    assert boosted.score == base.score + 15 and "open to high school" in boosted.notes[0]
    assert not evaluate(Job("ashby", "2", "Acme", "Software Engineer", "https://x"), spec).passes
    assert spec.apify_keywords() == "intern" and SearchSpec(include_keywords=["software"], require_keywords=["intern"]).apify_keywords() == "software intern"


# --- statuses ---------------------------------------------------------------------------------------

def test_status_moves_and_follow_ups():
    check_move("saved", "drafts_ready")
    check_move("approved", "applied")
    check_move("interview", "withdrawn")
    with pytest.raises(StatusError):
        check_move("saved", "applied")  # must be approved first
    with pytest.raises(StatusError):
        check_move("saved", "nonsense")
    assert follow_up_due("2026-11-01", "applied", date(2026, 11, 8))
    assert not follow_up_due("2026-11-20", "applied", date(2026, 11, 8))
    assert not follow_up_due("2026-11-01", "rejected", date(2026, 11, 8))


# --- validation of what the commands send back -------------------------------------------------------

def test_score_checks():
    c = check_scores([{"job_id": 1, "score": 72, "reasoning": "Python and SQL match; asks for college enrollment."},
                      {"job_id": 2, "score": 140, "reasoning": "x" * 40}, {"job_id": 9, "score": 50, "reasoning": "x" * 40},
                      {"job_id": 1, "score": 50, "reasoning": "short"}], {1, 2})
    assert [i["job_id"] for i in c.ok] == [1]
    assert any("0-100" in e for e in c.errors) and any("unknown job_id" in e for e in c.errors) and any("characters" in e for e in c.errors)


def test_draft_checks():
    good = {"job_id": 5, "cover_letter": LETTER, "answers": [{"question": "Why Riot?", "answer": "Their combat systems."}], "notes": ""}
    assert check_drafts([good], {5}).ok and not check_drafts([good], {5}).errors
    bad = dict(good, cover_letter="I am writing to express my interest in [Company]. " + "word " * 400)
    errors = check_drafts([bad], {5}).errors
    assert any("stock phrase" in e for e in errors) and any("placeholder" in e for e in errors) and any("words" in e for e in errors)
    assert check_drafts([good], {6}).errors  # not a saved job


# --- exports -----------------------------------------------------------------------------------------

def test_exports():
    head = Letterhead("Alex Rivera", "me@example.com", "555", "Edina, MN", "linkedin.com/in/x")
    assert file_name("Alex Rivera", "Riot Games", "pdf") == "Alex_CoverLetter_RiotGames.pdf"
    from applications.export import format_phone
    assert format_phone("6125550142") == "(612) 555-0142" and format_phone("+44 20 7946 0000") == "+44 20 7946 0000"
    assert file_name("Alex Rivera", "2K, Inc.", "docx") == "Alex_CoverLetter_2KInc.docx"
    pdf = to_pdf(LETTER + " Curly “quotes” — and dashes.", head, date(2026, 11, 5))
    assert pdf.startswith(b"%PDF-") and len(pdf) > 1000
    from pypdf import PdfReader
    text = PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
    assert "Alex Rivera" in text and "Riot Games team" in text and "November 5, 2026" in text
    docx = to_docx(LETTER, head, date(2026, 11, 5))
    xml = zipfile.ZipFile(io.BytesIO(docx)).read("word/document.xml").decode("utf-8")
    assert "Dear Riot Games team," in xml and "Sincerely," in xml and "November 5, 2026" in xml


# --- the whole flow through the website and the CLI ------------------------------------------------------

@pytest.fixture()
def site():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


def _seed_job(title="Software Engineering Intern", company="Riot Games", description=None):
    from jobsearch.runner import RunReport, ScoredJob
    from jobsearch.filters import Evaluation
    from jobsearch.match import MatchResult
    from storage.jobs_repo import JobRepo
    repo = JobRepo()
    search = repo.save_search(None, f"test {title}", SearchSpec(), auto_run=False)
    job = Job("greenhouse", f"{company}-{title}", company, title, "https://boards.example/job",
              description=description or "Build tools in Python. Open to high school students.")
    job.flags = find_flags(job.description)
    report = RunReport(kept=[ScoredJob(job, Evaluation(True, [], 1.0, []), MatchResult(60, 0.5, 1.0))])
    repo.save_results(search.id, 1000 + search.id, report)
    job_id = repo.results(search.id)[0][0].id
    return job_id, search.id


def _cli(capsys, *args, stdin=None, monkeypatch=None):
    if stdin is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(stdin)))
    code = cli.main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_answer_bank(site):
    from storage.apps_repo import AppRepo
    page = site.get("/settings?tab=answers").text
    assert "Answer bank" in page and "authorized to work" in page
    rows = {a.key: a for a in AppRepo().answers()}
    r = site.post("/settings/answers", data={f"a-{rows['availability'].id}": "June 15, 2027",
                                             "new_question": "Do you have transportation?", "new_answer": "Yes, my own car."})
    assert "Saved" in r.text and "June 15, 2027" in r.text and "Do you have transportation?" in r.text
    custom = next(a for a in AppRepo().answers() if not a.fixed)
    site.post(f"/settings/answers/{custom.id}/delete")
    assert all(a.fixed for a in AppRepo().answers())


def test_cli_round_trip_and_pipeline(site, capsys, monkeypatch, tmp_path):
    from storage.apps_repo import AppRepo
    repo = AppRepo()
    job_id, search_id = _seed_job()

    # scoring: pending -> save -> shown on the Jobs page
    code, out, _ = _cli(capsys, "pending", "scores", "--limit", "50")
    data = json.loads(out)
    item = next(j for j in data["jobs"] if j["job_id"] == job_id)
    assert code == 0 and item["eligible_for_high_schooler"] and item["flags"][0]["label"] == "Open to high school students"
    assert "resume_text" in data["you"] and "answer_bank" not in data["you"]
    code, out, err = _cli(capsys, "save", "scores", "-", monkeypatch=monkeypatch,
                          stdin=[{"job_id": job_id, "score": 140, "reasoning": "too high to be valid, should fail"}])
    assert code == 2 and "0-100" in err
    code, out, _ = _cli(capsys, "save", "scores", "-", monkeypatch=monkeypatch,
                        stdin=[{"job_id": job_id, "score": 81, "reasoning": "Python fits and it welcomes high school students."}])
    assert code == 0 and "Saved 1 score" in out
    assert "Python fits" in site.get(f"/jobs/job/{job_id}?search={search_id}").text

    # saving on Jobs puts it in the pipeline
    site.post(f"/jobs/job/{job_id}/state?search={search_id}", data={"state": "saved"})
    app = repo.for_job(job_id)
    assert app.status == "saved" and "Saved" in site.get(f"/jobs/searches/{search_id}/results").text
    board = site.get("/applications").text
    assert "Software Engineering Intern" in board and "Awaiting drafts" in board

    # drafts: pending includes the answer bank; bad drafts are refused; good ones move it to Drafts ready
    code, out, _ = _cli(capsys, "pending", "drafts")
    data = json.loads(out)
    assert [j["job_id"] for j in data["jobs"]] == [job_id] and data["jobs"][0]["ai_score"] == 81
    assert {a["key"] for a in data["you"]["answer_bank"]} >= {"work_authorization", "availability"}
    path = tmp_path / "drafts.json"
    path.write_text(json.dumps([{"job_id": job_id, "cover_letter": "I am writing to express my interest.", "answers": []}]))
    code, _, err = _cli(capsys, "save", "drafts", str(path))
    assert code == 2 and "stock phrase" in err and repo.for_job(job_id).drafts_version == 0
    path.write_text(json.dumps([{"job_id": job_id, "cover_letter": LETTER, "notes": "Check the start date.",
                                 "answers": [{"question": "When can you start?", "answer": "June 2027", "source": "bank"}]}]))
    code, out, _ = _cli(capsys, "save", "drafts", str(path))
    app = repo.for_job(job_id)
    assert code == 0 and app.status == "drafts_ready" and app.drafts_version == 1 and not app.cover_edited
    assert json.loads(_cli(capsys, "pending", "drafts")[1])["jobs"] == []

    detail = site.get(f"/applications/{app.id}").text
    assert "Dear Riot Games team" in detail and "Check the start date." in detail and "June 2027" in detail
    assert "Alex_CoverLetter_RiotGames.pdf" in detail or "_CoverLetter_RiotGames.pdf" in detail

    # editing is tracked
    r = site.post(f"/applications/{app.id}/cover", data={"cover_letter": LETTER.replace("Last summer", "This summer")})
    assert "Edited by you" in r.text and repo.get(app.id).cover_edited

    # redraft keeps history and the feedback
    code, out, _ = _cli(capsys, "show", str(job_id))
    assert json.loads(out)["job"]["application"]["edited_by_you"] is True
    path.write_text(json.dumps([{"job_id": job_id, "cover_letter": LETTER.replace("chatbot", "AI chatbot"), "answers": []}]))
    assert _cli(capsys, "save", "drafts", str(path), "--feedback", "mention the AI part")[0] == 0
    app = repo.get(app.id)
    assert app.drafts_version == 2 and not app.cover_edited and json.loads(app.drafts_history_json)[0]["feedback"] == "mention the AI part"
    assert app.status == "drafts_ready"

    # exports
    pdf = site.get(f"/applications/{app.id}/cover-letter.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-") and "_CoverLetter_RiotGames.pdf" in pdf.headers["content-disposition"]
    docx = site.get(f"/applications/{app.id}/cover-letter.docx")
    assert docx.status_code == 200 and docx.content[:2] == b"PK"

    # status flow: can't skip approval; approve -> applied sets the dates; follow-ups
    r = site.post(f"/applications/{app.id}/move", data={"status": "applied"})
    assert "go from Drafts ready to Applied" in r.text
    site.post(f"/applications/{app.id}/move", data={"status": "approved"})
    r = site.post(f"/applications/{app.id}/move", data={"status": "applied"})
    app = repo.get(app.id)
    assert app.status == "applied" and app.applied_at == date.today().isoformat() and app.follow_up_at > app.applied_at
    assert r.headers["HX-Trigger"] == "appsChanged"
    site.post(f"/applications/{app.id}/follow-up", data={"follow_up_at": date.today().isoformat()})
    assert repo.summary()["follow_ups_due"] == 1 and "Follow up due" in site.get("/applications/board").text
    site.post(f"/applications/{app.id}/follow-up", data={"done": "1"})
    assert repo.get(app.id).follow_up_at is None
    site.post(f"/applications/{app.id}/notes", data={"notes": "Recruiter: Sam"})
    assert repo.get(app.id).notes == "Recruiter: Sam"
    assert repo.summary()["applied_this_week"] == 1
    kinds = [(e.from_status, e.to_status) for e in repo.events(app.id) if e.kind == "status"]
    assert ("approved", "applied") in kinds and ("saved", "drafts_ready") in kinds

    # once applied, the job can't be unsaved from Jobs
    r = site.post(f"/jobs/job/{job_id}/state?search={search_id}", data={"state": ""})
    assert "Withdraw it there instead" in r.text and repo.for_job(job_id) is not None


def test_unsave_removes_early_applications(site):
    from storage.apps_repo import AppRepo
    job_id, search_id = _seed_job("Data Intern", "Acme")
    site.post(f"/jobs/job/{job_id}/state?search={search_id}", data={"state": "saved"})
    assert AppRepo().for_job(job_id).status == "saved"
    site.post(f"/jobs/job/{job_id}/state?search={search_id}", data={"state": ""})
    assert AppRepo().for_job(job_id) is None


def test_linkedin_daily_budget():
    """(80% of the monthly credit - usage before today) / days left in the month, today included."""
    from datetime import date
    from jobsearch.sources.apify import ApifySource, Usage
    u = Usage(used_usd=0.34, limit_usd=5.0, cycle_end="2026-11-30T23:59:59.999Z")
    budget = ApifySource.daily_budget(u, 0.8, spent_today=0.0, today=date(2026, 11, 6))
    assert abs(budget - (4.0 - 0.34) / 25) < 1e-9  # Nov 6..30 = 25 days
    # money already spent today doesn't shrink today's budget a second time
    u2 = Usage(used_usd=0.44, limit_usd=5.0, cycle_end="2026-11-30T23:59:59.999Z")
    assert abs(ApifySource.daily_budget(u2, 0.8, 0.10, date(2026, 11, 6)) - budget) < 1e-9
    assert ApifySource.daily_budget(Usage(4.2, 5.0, cycle_end="2026-11-30T00:00:00Z"), 0.8, 0, date(2026, 11, 6)) == 0


def test_linkedin_budget_refuses_when_today_is_spent():
    import httpx
    from jobsearch.config import JobSettings
    from jobsearch.sources import ApifySource, FeedClient
    s = JobSettings()
    s.apify_token, s.feed_delay = "apify_api_TEST", 0
    limits = {"data": {"monthlyUsageCycle": {"endAt": "2026-11-30T23:59:59.999Z"},
                       "limits": {"maxMonthlyUsageUsd": 5.0}, "current": {"monthlyUsageUsd": 0.50}}}
    client = FeedClient(s, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=limits)))
    src = ApifySource(s)
    assert src.check_budget(25, client, spent_today=0.0).ok
    refused = src.check_budget(25, client, spent_today=0.20)
    assert not refused.ok and "today's LinkedIn budget" in refused.reason
