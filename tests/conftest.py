import os

# The outreach worker thread stays off in tests; tests call OutreachRepo.tick() directly with fakes.
os.environ["JOBHUNT_WORKER"] = "0"

import pytest


@pytest.fixture(autouse=True)
def twin_cities_high_schooler():
    """The tests were written for a high school student in the Twin Cities (the default level + a home area)."""
    from jobsearch.student import preset
    from storage.db import init_db
    from storage.jobs_repo import JobRepo
    init_db()
    state, cities = preset("Twin Cities, MN")
    repo = JobRepo()
    p = repo.profile()
    if (p.home_state, p.student_level) != (state, "high_school"):
        repo.save_profile({"home_state": state, "home_cities": cities, "student_level": "high_school"})
    repo.apply_student()
    yield
