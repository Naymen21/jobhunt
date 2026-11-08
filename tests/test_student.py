"""Student levels and home area: eligibility per level, pitch candidates, the high school bonus, the Local tag,
outreach rules, the profile form and the AI handoff. All offline."""
import os
import tempfile
from datetime import date

os.environ.setdefault("JOBHUNT_DB", os.path.join(tempfile.mkdtemp(), "test.db"))
os.environ["JOBHUNT_WORKER"] = "0"

import pytest  # noqa: E402

from jobsearch import student  # noqa: E402
from jobsearch.flags import add_pitch, eligible_for_high_schooler, find_flags, is_pitch  # noqa: E402
from jobsearch.models import Flag  # noqa: E402

COLLEGE_ONLY = "Must be currently enrolled in a bachelor's degree program. Graduating between December 2027 and June 2028."
MASTERS = "Master's degree required in computer science."
HS_OK = "Open to high school students and incoming freshmen."


def keys(text):
    return find_flags(text)


def test_eligibility_by_level():
    hs = student.Student("high_school", 2027)
    college = student.Student("college", 2028)
    far = student.Student("college", 2031)
    grad = student.Student("grad_school", 2027)
    recent = student.Student("recent_grad", 2025)
    f_college, f_masters, f_hs = keys(COLLEGE_ONLY), keys(MASTERS), keys(HS_OK)
    assert not student.eligible(f_college, hs) and student.eligible(f_hs, hs)
    assert student.eligible(f_college, college)  # enrolled + grad year 2027/2028 fits a 2028 grad
    assert not student.eligible(f_college, far)  # class of 2031 is outside the window
    assert not student.eligible(f_masters, college) and student.eligible(f_masters, grad)
    assert not student.eligible(f_college, recent)  # "currently enrolled": a graduate can't
    assert student.welcomes_you(f_hs, hs) and not student.welcomes_you(f_hs, college)
    # unknown grad year never rules you out on the year alone
    assert student.eligible(keys("Graduating in 2030."), student.Student("college", None))


def test_engine_follows_current_level():
    try:
        student.set_student("college", "2028")
        flags = keys(COLLEGE_ONLY)
        assert eligible_for_high_schooler(flags)  # the old name now means "eligible for you"
        assert not is_pitch(add_pitch(flags, 40))  # nothing to pitch around
        assert is_pitch(add_pitch(keys(MASTERS), 40))  # small company, master's required: pitch it
        from jobsearch.filters import Evaluation
        from jobsearch.match import match
        assert match("Intern", HS_OK, Evaluation(True, [], 1.0, []), None, keys(HS_OK)).notes[:1] != ["+15: open to high school students"]
        student.set_student("high_school", "2027")
        assert not eligible_for_high_schooler(keys(COLLEGE_ONLY)) and is_pitch(add_pitch(keys(COLLEGE_ONLY), 40))
        assert "+15" in match("Intern", HS_OK, Evaluation(True, [], 1.0, []), None, keys(HS_OK)).notes[0]
    finally:
        student.set_student("high_school", "2027", *student.preset("Twin Cities, MN"))


def test_home_area_and_presets():
    tx = student.Student(home_state="TX", home_cities=["austin", "round rock"])
    assert student.is_local("Austin, TX", tx) and student.is_local("Round Rock", tx) and student.is_local("Dallas, Texas", tx)
    assert not student.is_local("Minneapolis, MN", tx) and not student.is_local("Remote", tx)
    assert not student.is_local("Austin, TX", student.Student())  # no home area: nothing is Local
    st, cities = student.preset("Seattle, WA")
    s = student.set_student("college", "2027", st, cities)
    assert s.area_label == "Seattle, WA" and student.is_local("Bellevue, WA")
    assert student.set_student("nonsense", "soon", "ZZ").level == "high_school"  # bad values fall back safely
    student.set_student("high_school", "2027", *student.preset("Twin Cities, MN"))


def test_class_year():
    assert student.class_year(student.Student("high_school", 2027), date(2026, 11, 8)) == "senior"
    assert student.class_year(student.Student("college", 2029), date(2026, 11, 8)) == "sophomore"
    assert student.class_year(student.Student("college", 2027), date(2027, 3, 1)) == "senior"
    assert student.class_year(student.Student("recent_grad", 2025)) == ""


def test_outreach_rules_by_level():
    from outreach.validate import check_outreach
    item = {"contact_id": 1, "hook": "h", "hook_source": "https://x.test/a", "hook_level": "company",
            "subject": "Quiz Kite + a student intern?",
            "email": "Hi Maya,\n\nYour quizzes caught my eye.\n\nI'm a sophomore at State U. I built a site with 600 users.\n\n"
                     "Would you be open to a 15-minute chat about an internship?",
            "followup1": "Hi Maya, one more thing.", "followup2": "Hi Maya, last note.", "linkedin_note": "Hi Maya, quick chat?"}
    known = {1: {"first_name": "Maya", "cold": True}}
    try:
        assert any("high school" in e for e in check_outreach([item], known).errors)  # default level: high school
        student.set_student("college", "2029")
        assert check_outreach([item], known).errors == []
        job = dict(item, email=item["email"].replace("an internship", "the analyst role"))
        assert any("internship" in e for e in check_outreach([job], known).errors)
        student.set_student("recent_grad", "2026")
        assert check_outreach([job], known).errors == []  # recent grads can ask about a role
    finally:
        student.set_student("high_school", "2027", *student.preset("Twin Cities, MN"))


@pytest.fixture()
def site():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_profile_form_sets_level_and_area(site):
    from storage.jobs_repo import PROFILE_FIELDS, JobRepo
    page = site.get("/settings").text
    assert "College student (undergraduate)" in page and "Twin Cities, MN" in page and "Home area" in page
    data = {f: "" for f in PROFILE_FIELDS}
    data.update(name="Alex Rivera", school="State U", grad_year="2028", student_level="college",
                degree_program="BS Data Science", home_state="TX", home_cities="austin, round rock")
    site.post("/settings/profile", data=data)
    assert student.current().level == "college" and student.current().home_state == "TX"
    form = site.get("/jobs/searches/new").text if site.get("/jobs/searches/new").status_code == 200 else ""
    if form:
        assert "Only postings I" in form and "open to high school students" not in form
    you = JobRepo().student_summary()
    assert you["label"].startswith("College") and you["grad_year"] == 2028 and not you["high_school"]
    from storage.outreach_repo import OutreachRepo
    assert OutreachRepo()._you()["student"]["level"] == "college"
    assert "Austin" in site.get("/outreach?view=companies").text or "Texas" in site.get("/outreach?view=companies").text


def test_platform_steps_render(site):
    from pathlib import Path
    from app.templating import _platform
    root = _platform()["root"]
    assert Path(root, "run.py").exists()  # paths come from wherever this copy lives, not a fixed folder
    page = site.get("/settings?tab=autopilot").text
    assert ("Task Scheduler" in page) or ("launchd" in page)
    assert root in page and str(Path.home() / "jobhunt-backups") in page
    keys_page = site.get("/settings?tab=keys").text
    assert _platform()["env_file"] in keys_page
