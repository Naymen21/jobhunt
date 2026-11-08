"""Phase 2.5: titles/departments, company-name resolution, gibberish, bulk find, last seen. Offline only."""
import os
import tempfile
from pathlib import Path

import pytest

from finder.bulk import BulkInput, find_one, parse_table
from finder.config import Settings
from finder.crawl import extract_from_html, extract_from_text
from finder.models import FoundEmail
from finder.patterns import detect_pattern
from finder.resolve import (Homepage, cache_key, company_tokens, confirm_company, guess_domains, looks_like_domain,
                            registrable, resolve_company)
from finder.store import DomainReport, MemoryStore
from finder.titles import classify, looks_like_title
from finder.verify import Verifier
from finder.verify.gibberish import looks_random
from finder.verify.mx import MxLookup

FIX = Path(__file__).parent / "fixtures"


# --- titles -> department / seniority ---------------------------------------------------

@pytest.mark.parametrize("title,dept,level", [
    ("Senior Software Engineer", "engineering", "senior"),
    ("Technical Recruiter", "recruiting", None),
    ("Head of Talent Acquisition", "recruiting", "executive"),
    ("HR Business Partner", "recruiting", None),
    ("People Operations Manager", "recruiting", "senior"),
    ("Chief People Officer", "recruiting", "executive"),
    ("Co-Founder & CEO", "executive", "executive"),
    ("Managing Partner", "executive", "executive"),
    ("VP of Engineering", "engineering", "executive"),
    ("Software Architect", "engineering", None),
    ("Director, Architect", "design", "senior"),
    ("Junior Designer", "design", "junior"),
    ("Product Manager", "product", "senior"),
    ("Marketing Intern", "marketing", "junior"),
    ("Partnerships Manager", "sales", "senior"),  # "partner" inside a word must not mean executive
    ("Account Executive", "sales", None),         # "executive" here is a sales job, not leadership
    ("Office Manager", "operations", "senior"),
    ("Associate Director, Design", "design", "senior"),
    ("Underwater Basket Weaver", "other", None),
])
def test_classify(title, dept, level):
    assert classify(title) == (dept, level)


@pytest.mark.parametrize("text,ok", [("Senior Landscape Architect", True), ("Head of Talent", True), ("CTO", True),
                                     ("New York", False), ("Aaron Dorf", False), ("jane@acme.com", False),
                                     ("Read more about our projects and approach to design work here", False)])
def test_looks_like_title(text, ok):
    assert looks_like_title(text) is ok


def test_titles_captured_from_cards():
    found = {e.email: e for e in extract_from_html((FIX / "cards_titles.html").read_text(encoding="utf-8"),
                                                    "https://studio.test/people", "studio.test")}
    assert found["oskar@studio.test"].title == "Director, Architect"
    assert (found["oskar@studio.test"].department, found["oskar@studio.test"].seniority) == ("design", "senior")
    assert found["alexm@studio.test"].title == "Senior Landscape Architect"
    assert found["daniel@studio.test"].title == "Project Manager"
    # the name is still the heading, never the title line
    assert found["oskar@studio.test"].full_name == "Oskar Lindqvist"


def test_titles_from_team_page_and_inline_text():
    found = {e.email: e for e in extract_from_html((FIX / "team.html").read_text(encoding="utf-8"),
                                                    "https://acme.test/team", "acme.test")}
    assert found["jane.doe@acme.test"].title == "Chief Executive Officer"
    assert found["jane.doe@acme.test"].department == "executive"
    assert found["john.smith@acme.test"].department == "engineering"  # "CTO"
    assert found["info@acme.test"].title is None  # role inboxes never get a person's title
    pdf_like = "Press office\nMonica Geller\nHead of Communications\nmonica.geller@acme.test\n"
    m = {e.email: e for e in extract_from_text(pdf_like, "u", "acme.test")}["monica.geller@acme.test"]
    assert m.title == "Head of Communications" and m.department == "marketing"


def test_github_people_are_engineering():
    from finder.sources.github import GitHubSource
    found = {}
    GitHubSource._collect({"html_url": "u", "commit": {"message": "x", "author": {"name": "Ada Lovelace", "email": "ada@acme.test"},
                                                       "committer": {}}}, "acme.test", found)
    assert found["ada@acme.test"].department == "engineering"


# --- gibberish --------------------------------------------------------------------------

@pytest.mark.parametrize("local", ["xkq7zt", "a8f3kd9q", "qwzrtpx", "x7k2m9p4", "lmnrtgh"])
def test_gibberish_flagged(local):
    assert looks_random(local)[0]


@pytest.mark.parametrize("local", ["xzhang", "jdoe2", "sjtbrown", "mkwiatkowski", "zbigniew.brzezinski", "alexk",
                                   "wojciech.szczesny", "phuong.nguyen", "thx1138", "info", "jane.doe+news"])
def test_real_names_not_flagged(local):
    assert not looks_random(local)[0]


def test_verifier_penalises_gibberish():
    from dataclasses import replace
    v = Verifier(settings=replace(Settings(), smtp_transport="disabled"), cache=None,
                 mx_resolver=lambda d: MxLookup(["mx.acme.test"], True, True, "fake"))
    v.cache = __import__("finder.cache", fromlist=["DomainCache"]).DomainCache(None)
    junk, real = v.verify("xkq7zt@acme.test"), v.verify("jane.doe@acme.test")
    assert junk.is_gibberish and junk.check("random").passed is False
    assert junk.confidence < real.confidence


# --- company name -> domain -------------------------------------------------------------

def test_name_helpers():
    assert company_tokens("Riot Games, Inc.") == ["riot", "games"]
    assert cache_key("The Snøhetta Group") == "snohetta"
    assert guess_domains("Riot Games")[:3] == ["riotgames.com", "riotgames.io", "riotgames.co"]
    assert "riot.com" in guess_domains("Riot Games")
    assert looks_like_domain("riotgames.com") and not looks_like_domain("Riot Games")
    assert registrable("www.riotgames.com") == "riotgames.com" and registrable("shop.acme.co.uk") == "acme.co.uk"


def _fakes(sites: dict):
    """sites: domain -> (exists, has_mx, Homepage | None)"""
    def dns_fn(domain):
        exists, mx, _ = sites.get(domain, (False, False, None))
        return exists, mx

    def page_fn(domain, settings):
        return sites.get(domain, (False, False, None))[2] or Homepage(final_host=domain)
    return dns_fn, page_fn


def test_resolve_clear_winner_and_cache():
    dns_fn, page_fn = _fakes({
        "riotgames.com": (True, True, Homepage("www.riotgames.com", "Riot Games", "", "Makers of League of Legends", True)),
        "riot.com": (True, True, Homepage("www.riotgames.com", "Riot Games", "", "", True)),  # redirects
        "riotgames.io": (True, False, Homepage("riotgames.io", "", "", "", False)),
    })
    store = MemoryStore()
    r = resolve_company("Riot Games", store, dns_fn=dns_fn, page_fn=page_fn)
    assert r.status == "resolved" and r.domain == "riotgames.com"
    assert any("redirects here" in why for c in r.candidates for why in c.reasons)
    boom = lambda *a: (_ for _ in ()).throw(AssertionError("network used despite cache"))
    again = resolve_company("riot games", store, dns_fn=boom, page_fn=boom)
    assert again.cached and again.domain == "riotgames.com"


def test_resolve_ambiguous_then_confirm():
    dns_fn, page_fn = _fakes({
        "epicgames.com": (True, True, Homepage("www.epicgames.com", bot_blocked=True)),
        "epic.com": (True, True, Homepage("www.epic.com", "Epic | With the patient at the heart", "", "", True)),
    })
    store = MemoryStore()
    r = resolve_company("Epic Games", store, dns_fn=dns_fn, page_fn=page_fn)
    assert r.status == "ambiguous" and r.domain is None
    assert [c.domain for c in r.candidates][0] == "epicgames.com"  # exact name beats the "Epic" healthcare company
    confirm_company("Epic Games", "epicgames.com", store)
    again = resolve_company("Epic Games", store, dns_fn=dns_fn, page_fn=page_fn)
    assert again.domain == "epicgames.com" and again.confirmed


def test_resolve_ignores_parked_and_handles_nothing():
    dns_fn, page_fn = _fakes({"tinyco.com": (True, False, Homepage("tinyco.com", "tinyco.com is for sale | HugeDomains", "", "", True))})
    r = resolve_company("TinyCo", MemoryStore(), dns_fn=dns_fn, page_fn=page_fn)
    assert r.status == "not_found" and r.domain is None


def test_typed_domain_skips_resolution():
    boom = lambda *a: (_ for _ in ()).throw(AssertionError("no lookup needed"))
    r = resolve_company("https://www.Snohetta.com/people", MemoryStore(), dns_fn=boom, page_fn=boom)
    assert r.status == "domain" and r.domain == "snohetta.com"


# --- bulk find ---------------------------------------------------------------------------

def test_parse_table_with_headers():
    p = parse_table("Full Name,Company Website,Notes\nJane Doe,acme.com,x\n,missing.com,\nJohn Roe,Riot Games,\n")
    assert [(r.name, r.company) for r in p.rows] == [("Jane Doe", "acme.com"), ("John Roe", "Riot Games")]
    assert p.skipped == 1 and p.columns == {"name": "Full Name", "company": "Company Website"}


def test_parse_table_first_last_semicolon_and_no_header():
    p = parse_table("first name;last name;company\nJane;Doe;Acme\n")
    assert p.rows[0].name == "Jane Doe" and p.rows[0].company == "Acme"
    q = parse_table("Jane Doe, acme.com\nJohn Roe, riotgames.com\n")
    assert len(q.rows) == 2 and q.columns["name"] == "column 1"
    assert parse_table("email\nx@y.com\n").error
    assert parse_table("").error


def test_find_one_uses_domain_and_pattern():
    es = [FoundEmail(f"{l}@acme.test", "acme.test", "personal", "website", "u", full_name=n)
          for n, l in [("Jane Doe", "jane.doe"), ("Ann Lee", "ann.lee"), ("Tom Fox", "tom.fox")]]
    store = MemoryStore()
    store.save_report(DomainReport("acme.test", es, detect_pattern("acme.test", es)))
    from dataclasses import replace
    v = Verifier(settings=replace(Settings(), smtp_transport="disabled"),
                 cache=__import__("finder.cache", fromlist=["DomainCache"]).DomainCache(None),
                 mx_resolver=lambda d: MxLookup(["mx.acme.test"], True, True, "fake"))
    res = find_one(BulkInput("Kim Park", "acme.test"), store, v, Settings())
    assert res.status == "done" and res.email == "kim.park@acme.test" and res.evidence == "pattern"
    bad = find_one(BulkInput("Cher", "acme.test"), store, v, Settings())
    assert bad.status == "error"


# --- storage: new columns, upgrade, last seen ----------------------------------------------

def test_old_database_gets_new_columns(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE found_email (id INTEGER PRIMARY KEY, domain VARCHAR, email VARCHAR, kind VARCHAR, "
                "full_name VARCHAR, name_source VARCHAR, source_type VARCHAR, source_url VARCHAR, context VARCHAR, "
                "first_seen VARCHAR, last_seen VARCHAR)")
    con.execute("INSERT INTO found_email (domain, email, kind, source_type, source_url, context, first_seen, last_seen) "
                "VALUES ('a.test', 'x@a.test', 'personal', 'website', 'u', '', '2026', '2026')")
    con.commit()
    con.close()
    from sqlmodel import create_engine
    from storage import db as dbmod
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        dbmod._upgrade(conn)
    with engine.connect() as conn:
        cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(found_email)")]
        assert {"title", "department", "seniority"} <= set(cols)
        assert conn.exec_driver_sql("SELECT email FROM found_email").scalar() == "x@a.test"  # data kept


def test_stale_addresses_and_incomplete_searches(tmp_path):
    from sqlmodel import SQLModel, create_engine
    import storage.tables  # noqa: F401
    from storage.repo import SqlStore
    engine = create_engine(f"sqlite:///{tmp_path / 's.db'}")
    SQLModel.metadata.create_all(engine)
    store = SqlStore(engine)
    a = FoundEmail("ann@acme.test", "acme.test", "personal", "website", "u1", full_name="Ann Lee")
    b = FoundEmail("bob@acme.test", "acme.test", "personal", "website", "u2", full_name="Bob Ray")
    store.save_report(DomainReport("acme.test", [a, b], detect_pattern("acme.test", [a, b]), searched_at="2026-11-01T00:00:00+00:00"))
    a2 = FoundEmail("ann@acme.test", "acme.test", "personal", "website", "u1", full_name="Ann Lee")
    store.save_report(DomainReport("acme.test", [a2], detect_pattern("acme.test", [a2]), searched_at="2026-11-03T00:00:00+00:00"))
    r = store.load_report("acme.test")
    assert not r.is_stale("ann@acme.test") and r.is_stale("bob@acme.test")
    # a cancelled search must not make everything look stale
    a3 = FoundEmail("ann@acme.test", "acme.test", "personal", "website", "u1", full_name="Ann Lee")
    store.save_report(DomainReport("acme.test", [a3], detect_pattern("acme.test", [a3]), searched_at="2026-11-04T00:00:00+00:00",
                                   complete=False))
    assert store.load_report("acme.test").searched_at == "2026-11-03T00:00:00+00:00"
    assert [(d.domain, n) for d, n in store.list_domains()] == [("acme.test", 2)]


def test_redirecting_guesses_strengthen_a_candidate():
    dns_fn, page_fn = _fakes({
        "epicgames.com": (True, True, Homepage("www.epicgames.com", bot_blocked=True)),
        "epicgames.dev": (True, True, Homepage("www.epicgames.com", bot_blocked=True)),  # owned, redirects
        "epic.com": (True, True, Homepage("www.epic.com", "Epic | With the patient at the heart", "", "", True)),
    })
    r = resolve_company("Epic Games", MemoryStore(), dns_fn=dns_fn, page_fn=page_fn)
    assert r.status == "resolved" and r.domain == "epicgames.com"
