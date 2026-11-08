import os
import tempfile

os.environ["JOBHUNT_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def test_pages_render():
    with TestClient(app) as client:
        r = client.get("/finder")
        assert r.status_code == 200 and "Finder" in r.text and "By company" in r.text
        r = client.get("/outreach")
        assert r.status_code == 200 and "Outreach" in r.text and "Coming soon" not in r.text
        for path, text in [("/jobs", "Saved searches"), ("/jobs?tab=watchlist", "Check manually"),
                           ("/settings", "Save profile"), ("/settings?tab=resumes", "Upload a resume"),
                           ("/settings?tab=keys", "APIFY_TOKEN")]:
            r = client.get(path)
            assert r.status_code == 200 and text in r.text and "Coming soon" not in r.text
        assert client.get("/", follow_redirects=False).headers["location"] == "/review"
        assert client.get("/review").status_code == 200
        r = client.get("/dashboard")
        assert r.status_code == 200 and "Best matches right now" in r.text and "Getting set up" in r.text


def test_verify_stores_history():
    with TestClient(app) as client:
        r = client.post("/finder/verify", data={"email": "not-an-email"})
        assert r.status_code == 200
        assert "badge-invalid" in r.text and r.headers["HX-Trigger"] == "historyChanged"

        r = client.post("/finder/verify-row", data={"email": "bad..x@acme.com"})
        assert r.status_code == 200 and "<tr" in r.text

        hist = client.get("/finder/history").text
        assert "not-an-email" in hist and "bad..x@acme.com" in hist

        client.post("/finder/history/clear")
        assert "No verifications yet" in client.get("/finder/history").text


def _seed_domain():
    from finder.models import FoundEmail
    from finder.patterns import detect_pattern
    from finder.store import DomainReport, SourceStatus
    from storage.repo import SqlStore

    es = [FoundEmail(f"{l}@acme.test", "acme.test", "personal", "website", f"https://acme.test/team#{i}", full_name=n)
          for i, (n, l) in enumerate([("Jane Doe", "jane.doe"), ("John Smith", "john.smith"), ("Ann Lee", "ann.lee")])]
    es.append(FoundEmail("info@acme.test", "acme.test", "role", "website", "https://acme.test/contact"))
    SqlStore().save_report(DomainReport("acme.test", es, detect_pattern("acme.test", es),
                                        sources=[SourceStatus("website", "ok", "3 pages")]))


def test_domain_results_csv_and_find():
    with TestClient(app) as client:
        _seed_domain()
        r = client.post("/finder/domain-search", data={"domain": "https://www.acme.test/"})
        assert r.status_code == 200 and "{first}.{last}@acme.test" in r.text and "3 of 3" in r.text
        assert 'data-kind="role"' in r.text and "info@acme.test" in r.text

        csv_all = client.get("/finder/domains/acme.test/emails.csv").text
        assert csv_all.splitlines()[0].startswith("email,name,title,department,seniority,type")
        assert "info@acme.test" in csv_all and "jane.doe@acme.test" in csv_all
        csv_personal = client.get("/finder/domains/acme.test/emails.csv?kind=personal").text
        assert "info@acme.test" not in csv_personal and "first.last" in csv_personal

        r = client.post("/finder/find", data={"name": "Jacob Ruiz", "domain": "acme.test"})
        assert r.status_code == 200 and "jacob.ruiz@acme.test" in r.text and "Pattern match" in r.text
        r = client.post("/finder/find", data={"name": "Jane Doe", "domain": "acme.test"})
        assert "Seen in the wild" in r.text and "acme.test/team" in r.text
        r = client.post("/finder/find", data={"name": "Cher", "domain": "acme.test"})
        assert "first and last name" in r.text
        assert "acme.test" in client.get("/finder/domains").text


def test_run_progress_and_bad_domain():
    from sqlmodel import Session

    from storage.db import engine
    from storage.tables import SearchRun

    from app.routes import finder as finder_routes
    from finder.resolve import DomainCandidate, Resolution

    def fake_resolve(query, store=None, **kw):  # no network in tests
        if query == "Acme Widgets":
            return Resolution(query, "acme widgets", "ambiguous", candidates=[
                DomainCandidate("acmewidgets.com", 55, True, False, "", "", ["domain matches the name exactly"]),
                DomainCandidate("acme.com", 40, True, True, "Acme Corp", "", [])])
        return Resolution(query, query.lower(), "not_found")

    finder_routes.resolve_company = fake_resolve
    with TestClient(app) as client:
        assert "Couldn't find a website" in client.post("/finder/domain-search", data={"domain": "nope"}).text
        picker = client.post("/finder/find", data={"name": "Jane Doe", "domain": "Acme Widgets"}).text
        assert 'Which "Acme Widgets" did you mean?' in picker and "acmewidgets.com" in picker and "company_query" in picker
        r = client.post("/finder/find", data={"name": "Jane Doe", "domain": "acmewidgets.com", "company_query": "Acme Widgets"})
        assert "jane.doe@acmewidgets.com" in r.text
        from storage.repo import SqlStore
        assert SqlStore().load_company("acme widgets")["domain"] == "acmewidgets.com"
        with Session(engine) as s:
            run = SearchRun(domain="slow.test", status="running", pages_crawled=7, emails_found=3,
                            sources_json='{"website": {"status": "running", "message": "Fetching /team"}}')
            s.add(run)
            s.commit()
            s.refresh(run)
            rid = run.id
        r = client.get(f"/finder/runs/{rid}")
        assert 'hx-trigger="every 1s"' in r.text and ">7<" in r.text and "Fetching /team" in r.text
