"""Phase 2 tests: extraction, names, patterns, scoring, crawler, GitHub, find. Fixtures only, no live sites."""
import json
import threading
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from finder.config import Settings
from finder.crawl import cf_decode, crawl_domain, extract_from_html, extract_from_text, find_addresses, is_role
from finder.crawl.pdf import pdf_text
from finder.crawl.prioritize import score_url
from finder.crawl.robots import parse_robots
from finder.crawl.sitemap import parse_sitemap
from finder.discovery import normalize_domain, search_domain
from finder.find import find_email
from finder.http import PoliteFetcher
from finder.models import FoundEmail
from finder.names import looks_like_name, parse_name, same_person
from finder.patterns import detect_pattern, match_templates
from finder.scoring import apply_verification, pattern_confidence
from finder.sources import GitHubSource, SourceContext, WebSearchSource, WebsiteSource
from finder.store import MemoryStore
from finder.models import CheckResult, VerificationResult

FIX = Path(__file__).parent / "fixtures"
DOMAIN = "acme.test"


def fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def by_email(found):
    return {f.email: f for f in found}


# --- extraction ----------------------------------------------------------------------

def test_team_page_extraction():
    found = by_email(extract_from_html(fixture("team.html"), "https://acme.test/team", DOMAIN))
    expected_names = {
        "jane.doe@acme.test": "Jane Doe",  # name in card heading, mailto text is just "Email"
        "john.smith@acme.test": "John Smith",  # mixed-case mailto with ?subject
        "jose.garcialopez@acme.test": "José García-López",
        "sean.obrien@acme.test": "Seán O'Brien",  # Cloudflare data-cfemail
        "maria.chen@acme.test": "Maria Chen",  # Cloudflare /cdn-cgi/l/email-protection#hex link
        "wei.zhang@acme.test": "Wei Zhang",  # mailto with display name
        "laura.kim@acme.test": "Laura Kim",  # "laura.kim [at] acme [dot] test"
        "tom.ruiz@acme.test": "Tom Ruiz",  # "(at) ... (dot)"
        "ben.ode@acme.test": "Ben Ode",  # "ben dot ode at acme dot test"
        "priya.natarajan@acme.test": "Priya Natarajan",  # schema.org JSON-LD
    }
    for email, name in expected_names.items():
        assert email in found, email
        assert found[email].full_name == name, (email, found[email].full_name)
        assert found[email].kind == "personal"
    for role in ["info@acme.test", "careers@acme.test", "hello@acme.test", "sales.eu@eu.acme.test"]:
        assert found[role].kind == "role" and found[role].full_name is None
    assert "attr.only@acme.test" in found  # only present in a data- attribute
    # other domains, free mail, "contact us at acme.test" and image names must not appear
    assert not any(e.endswith(("othercorp.com", "gmail.com", ".png")) for e in found)
    assert "us@acme.test" not in found


def test_obfuscation_variants():
    text = ("a: jo.ann [at] acme [dot] test; b: max(at)acme(dot)test; c: liz {at} acme {dot} test; "
            "d: sam at acme dot test; e: kai@sub.acme.test; f: meet us at acme.test; g: bob@acme.test.evil.org")
    got = {e for e, _, _ in find_addresses(text, DOMAIN)}
    assert got == {"jo.ann@acme.test", "max@acme.test", "liz@acme.test", "sam@acme.test", "kai@sub.acme.test"}


def test_cloudflare_decode():
    encoded = "42" + "".join(f"{ord(c) ^ 0x42:02x}" for c in "x.y@acme.test")
    assert cf_decode(encoded) == "x.y@acme.test"
    assert cf_decode("zz") is None


def test_inline_names_in_prose():
    found = by_email(extract_from_html(fixture("about.html"), "https://acme.test/about", DOMAIN))
    assert found["grace.hopper@acme.test"].full_name == "Grace Hopper"
    assert found["alan.turing@acme.test"].full_name in (None, "Alan Turing")
    assert found["ada.lovelace@acme.test"].full_name == "Ada Lovelace"
    assert found["sales@acme.test"].kind == "role"


def test_pdf_extraction():
    text = pdf_text((FIX / "report.pdf").read_bytes())
    found = by_email(extract_from_text(text, "https://acme.test/files/report.pdf", DOMAIN))
    assert found["rachel.green@acme.test"].full_name == "Rachel Green"  # name on the line above
    assert found["monica.geller@acme.test"].full_name == "Monica Geller"
    assert found["ir@acme.test"].kind == "role"
    assert all(f.source_type == "pdf" for f in found.values())


@pytest.mark.parametrize("email,role", [
    ("info@x.com", True), ("press-uk@x.com", True), ("support.team@x.com", True), ("no-reply@x.com", True),
    ("jane.doe@x.com", False), ("dev.patel@x.com", False), ("people.smith@x.com", False), ("jsmith@x.com", False),
])
def test_role_detection(email, role):
    assert is_role(email) is role


# --- names ---------------------------------------------------------------------------

def test_parse_name_variants():
    p = parse_name("Dr. José García-López Jr.")
    assert (p.first, p.last) == ("jose", "garcia-lopez")
    assert set(p.last_variants()) >= {"garcialopez", "garcia-lopez", "garcia", "lopez"}
    assert (parse_name("Smith, Jane").first, parse_name("Smith, Jane").last) == ("jane", "smith")
    p = parse_name("Jan van der Berg")
    assert p.last == "vanderberg" and "berg" in p.last_variants() and "van" not in p.last_variants()
    p = parse_name("Jane Q. Public, PhD")
    assert (p.first, p.middles, p.last) == ("jane", ["q"], "public")
    assert parse_name("Zoë Ångström").last == "angstrom"
    assert parse_name("Søren Kierkegaard").first == "soren"
    assert parse_name("Madonna") is None


@pytest.mark.parametrize("text,ok", [
    ("Jane Doe", True), ("JANE DOE", True), ("Mary-Kate O'Neil", True), ("Dr. Ana Ruiz", True),
    ("Contact Us", False), ("Our Team", False), ("Read more", False), ("jane doe", False), ("Jane", False),
    ("Jane Doe 2024", False), ("Head of Sales", False),
])
def test_looks_like_name(text, ok):
    assert looks_like_name(text) is ok


def test_same_person():
    assert same_person("José García", "Jose Garcia")
    assert same_person("Dr. Jane Doe", "jane DOE")
    assert not same_person("Jane Doe", "John Doe")


# --- patterns ------------------------------------------------------------------------

@pytest.mark.parametrize("name,local,template", [
    ("Jane Doe", "jane.doe", "first.last"), ("Jane Doe", "jdoe", "flast"), ("Jane Doe", "j.doe", "f.last"),
    ("Jane Doe", "janedoe", "firstlast"), ("Jane Doe", "jane", "first"), ("Jane Doe", "doe", "last"),
    ("Jane Doe", "janed", "firstl"), ("Jane Doe", "jane_doe", "first_last"), ("Jane Doe", "doe.jane", "last.first"),
    ("Jane Doe", "doej", "lastf"), ("Jane Doe", "jane-doe", "first-last"), ("Jane Doe", "Jane.Doe", "first.last"),
    ("José García-López", "jose.garcialopez", "first.last"), ("José García-López", "jose.garcia-lopez", "first.last"),
    ("José García-López", "jgarcia", "flast"), ("Jane Q. Public", "jane.q.public", "first.m.last"),
    ("Jane Q. Public", "jqpublic", "fmlast"), ("Jan van der Berg", "jan.vanderberg", "first.last"),
    ("Jan van der Berg", "jberg", "flast"), ("Seán O'Brien", "sobrien", "flast"),
    ("Jane Doe", "jane.doe+news", "first.last"), ("Jane Doe", "jdoe2", "flast"),
])
def test_template_matching(name, local, template):
    assert template in match_templates(parse_name(name), local)


def _emails(pairs, domain="acme.test", kind="personal"):
    return [FoundEmail(f"{l}@{domain}", domain, kind, "website", f"https://acme.test/{i}", full_name=n)
            for i, (n, l) in enumerate(pairs)]


def test_detect_pattern_majority():
    pairs = [("Jane Doe", "jane.doe"), ("John Smith", "john.smith"), ("Ann Lee", "ann.lee"), ("Tom Fox", "tom.fox"),
             ("Kim Park", "kim.park"), ("Sam Hill", "sam.hill"), ("Al Roy", "al.roy"), ("Ed Wu", "ed.wu"),
             ("Bob Ray", "bray")]
    r = detect_pattern("acme.test", _emails(pairs))
    assert r.template == "first.last" and r.support == 8 and r.contradict == 1 and r.total == 9
    assert 75 <= r.confidence <= 90
    assert r.summary.startswith("first.last, 8 of 9 examples")


def test_detect_pattern_ignores_roles_and_nameless():
    es = _emails([("Jane Doe", "jdoe")]) + _emails([(None, "info")], kind="role") + \
        [FoundEmail("x.y@acme.test", "acme.test", "personal", "website", "u")]
    r = detect_pattern("acme.test", es)
    assert r.total == 1 and r.template == "flast"


def test_detect_pattern_no_pairs():
    r = detect_pattern("acme.test", [])
    assert r.template is None and r.confidence == 0


def test_pattern_confidence_grows_with_evidence():
    c1 = pattern_confidence(1, 1, 0.3)
    c3 = pattern_confidence(3, 3, 0.3)
    c20 = pattern_confidence(20, 20, 0.3)
    assert c1 < c3 < c20 and c1 <= 60 and c20 >= 90
    assert pattern_confidence(5, 10, 0.3) < pattern_confidence(9, 10, 0.3)


def test_apply_verification():
    no_mx = VerificationResult("a@b.c", "b.c", "invalid", 0, checks=[CheckResult("mx", False, "")])
    assert apply_verification(80, no_mx)[0] == 0
    smtp_ok = VerificationResult("a@b.c", "b.c", "valid", 95, checks=[CheckResult("mx", True), CheckResult("smtp", True)],
                                 is_catch_all=False)
    assert apply_verification(40, smtp_ok)[0] == 95
    rejected = VerificationResult("a@b.c", "b.c", "invalid", 5, checks=[CheckResult("mx", True), CheckResult("smtp", False)])
    assert apply_verification(80, rejected)[0] == 5
    disabled = VerificationResult("a@b.c", "b.c", "unknown", 40, checks=[CheckResult("mx", True), CheckResult("smtp", None)])
    assert apply_verification(63, disabled)[0] == 63


# --- robots / sitemap / prioritising -------------------------------------------------

def test_robots_rules():
    r = parse_robots("https://acme.test/robots.txt", fixture("robots.txt"))
    assert r.allowed("https://acme.test/team") and not r.allowed("https://acme.test/private/staff")
    assert r.sitemaps == ["https://acme.test/sitemap.xml"]
    blocked = parse_robots("https://x.test/robots.txt", "User-agent: *\nDisallow: /\n")
    assert blocked.blocks_everything
    ours = parse_robots("https://x.test/robots.txt", "User-agent: JobHuntFinder\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
    assert ours.blocks_everything


def test_sitemap_parse():
    pages, children = parse_sitemap(fixture("sitemap.xml").encode())
    assert "https://acme.test/team" in pages and not children
    pages, children = parse_sitemap(b"<sitemapindex><sitemap><loc>https://a.test/s1.xml</loc></sitemap></sitemapindex>")
    assert children == ["https://a.test/s1.xml"] and not pages


def test_url_priorities():
    assert score_url("https://a.test/team") > score_url("https://a.test/blog/some-post")
    assert score_url("https://a.test/contact") > score_url("https://a.test/products/widget")
    assert score_url("https://a.test/blog/tag/news") < 0


# --- crawler (mock transport, no network) --------------------------------------------

def _site(routes: dict[str, tuple[int, str, bytes]], log: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url).rstrip("/") or str(request.url)
        log.append(url)
        status, ctype, body = routes.get(url, (404, "text/html", b"not found"))
        return httpx.Response(status, headers={"content-type": ctype}, content=body)
    return httpx.MockTransport(handler)


def _acme_routes(robots: str = None):
    html = "text/html; charset=utf-8"
    return {
        "https://acme.test/robots.txt": (200, "text/plain", (robots or fixture("robots.txt")).encode()),
        "https://acme.test": (200, html, fixture("home.html").encode()),
        "https://acme.test/sitemap.xml": (200, "application/xml", fixture("sitemap.xml").encode()),
        "https://acme.test/team": (200, html, fixture("team.html").encode()),
        "https://acme.test/about": (200, html, fixture("about.html").encode()),
        "https://acme.test/private/staff": (200, html, b"<a href='mailto:secret.person@acme.test'>x</a>"),
        "https://acme.test/files/report.pdf": (200, "application/pdf", (FIX / "report.pdf").read_bytes()),
    }


def _settings(**kw):
    return replace(Settings(), crawl_delay=0, **kw)


def test_crawl_obeys_robots_and_collects():
    log: list[str] = []
    settings = _settings()
    fetcher = PoliteFetcher(settings, transport=_site(_acme_routes(), log))
    res = crawl_domain(DOMAIN, settings, fetcher=fetcher)
    emails = {e.email for e in res.emails}
    assert "jane.doe@acme.test" in emails and "grace.hopper@acme.test" in emails
    assert "rachel.green@acme.test" in emails  # from the linked PDF
    assert "secret.person@acme.test" not in emails
    assert not any("/private/" in u for u in log)  # never even requested
    assert not any("other.example" in u or "logo.png" in u for u in log)
    assert res.pdfs_parsed == 1 and res.robots_status == "ok"


def test_crawl_blocked_by_robots():
    log: list[str] = []
    settings = _settings()
    fetcher = PoliteFetcher(settings, transport=_site(_acme_routes("User-agent: *\nDisallow: /\n"), log))
    res = crawl_domain(DOMAIN, settings, fetcher=fetcher)
    assert res.blocked and not res.emails
    assert log == ["https://acme.test/robots.txt"]


def test_crawl_page_budget_and_cancel():
    log: list[str] = []
    settings = _settings()
    res = crawl_domain(DOMAIN, settings, fetcher=PoliteFetcher(settings, transport=_site(_acme_routes(), log)), max_pages=2)
    assert res.pages_crawled == 2 and res.stopped == "budget"
    cancel = threading.Event()
    cancel.set()
    res = crawl_domain(DOMAIN, settings, fetcher=PoliteFetcher(settings, transport=_site(_acme_routes(), [])), cancel=cancel)
    assert res.stopped == "cancelled" and res.pages_crawled == 1  # homepage only


# --- GitHub source (mock API) ---------------------------------------------------------

def _github_transport(org_blog="https://acme.test"):
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/orgs/acme":
            return httpx.Response(200, json={"login": "acme", "blog": org_blog, "email": None})
        if p == "/orgs/acme/repos":
            return httpx.Response(200, json=[
                {"full_name": "acme/widget", "fork": False, "archived": False},
                {"full_name": "acme/forked", "fork": True, "archived": False},
            ])
        if p == "/repos/acme/widget/commits":
            return httpx.Response(200, json=json.loads(fixture("github_commits.json")))
        if p == "/search/users":
            return httpx.Response(200, json={"items": []})
        return httpx.Response(404, json={"message": "Not Found"})
    return httpx.MockTransport(handler)


def test_github_source_collects_commit_authors():
    res = GitHubSource(transport=_github_transport()).run(DOMAIN, SourceContext(settings=_settings()))
    found = by_email(res.emails)
    assert res.status == "ok" and res.stats["org"] == "acme" and res.stats["repos_scanned"] == 1
    assert found["linus.pauling@acme.test"].full_name == "Linus Pauling"
    assert found["margaret.hamilton@acme.test"].full_name == "Margaret Hamilton"  # username author, real committer name
    assert found["releases@acme.test"].kind == "role"
    assert not any("noreply" in e or "gmail" in e for e in found)
    assert found["linus.pauling@acme.test"].source_url.startswith("https://github.com/acme/widget/commit/")


def test_github_rejects_org_with_other_website():
    res = GitHubSource(transport=_github_transport("https://acme-games.example")).run(DOMAIN, SourceContext(settings=_settings()))
    assert res.status == "not_found" and "manually" in res.message


def test_websearch_not_configured():
    res = WebSearchSource().safe_run(DOMAIN, SourceContext(settings=_settings()))
    assert res.status == "not_configured"


# --- discovery + find ------------------------------------------------------------------

def test_search_domain_end_to_end_and_cache():
    settings = _settings()
    store = MemoryStore()
    options = {"fetcher": PoliteFetcher(settings, transport=_site(_acme_routes(), []))}
    sources = [WebsiteSource(), GitHubSource(transport=_github_transport()), WebSearchSource()]
    events = []
    report = search_domain("https://www.ACME.test/team", store, sources=sources, options=options,
                           settings=settings, progress=events.append)
    assert report.domain == "acme.test" and not report.cached
    assert report.pattern.template == "first.last" and report.pattern.support >= 10
    assert {s.source: s.status for s in report.sources} == {"website": "ok", "github": "ok", "websearch": "not_configured"}
    assert any(e.get("pages_crawled") for e in events)
    again = search_domain("acme.test", store, sources=[])
    assert again.cached and again.pattern.template == "first.last"


def test_find_email_tiers():
    pairs = [("Jane Doe", "jane.doe"), ("John Smith", "john.smith"), ("Ann Lee", "ann.lee"), ("Tom Fox", "tom.fox")]
    es = _emails(pairs) + [FoundEmail("jsmith@acme.test", "acme.test", "personal", "github", "https://gh/1",
                                      full_name="Jill Smith")]
    from finder.store import DomainReport
    store = MemoryStore()
    store.save_report(DomainReport("acme.test", es, detect_pattern("acme.test", es)))

    seen = find_email("Jane Doe", "acme.test", store, verify=False)
    assert seen.best.email == "jane.doe@acme.test" and seen.best.score == 97 and seen.best.evidence == "seen"

    guess = find_email("Jacob Smith", "acme.test", store, verify=False)
    assert guess.best.email == "jacob.smith@acme.test" and guess.best.evidence == "pattern"
    assert "first.last" in guess.best.why
    taken = [c for c in guess.alternatives if c.evidence == "taken"]
    assert taken and taken[0].email == "jsmith@acme.test" and "Jill Smith" in taken[0].why

    blind = find_email("Jane Doe", "never-searched.test", MemoryStore(), verify=False)
    assert blind.best.email == "jane.doe@never-searched.test" and blind.best.score <= 35 and blind.notes


def test_normalize_domain():
    assert normalize_domain("https://www.Acme.com/team?x=1") == "acme.com"
    assert normalize_domain("jane@acme.com") == "acme.com"
    with pytest.raises(ValueError):
        normalize_domain("not a domain")


# --- real-world layouts found during the live run (synthetic copies) -----------------

def test_card_name_heading_beats_title_lines():
    found = by_email(extract_from_html(fixture("cards_titles.html"), "https://studio.test/people", "studio.test"))
    assert found["oskar@studio.test"].full_name == "Oskar Lindqvist"  # not "Director, Architect" or "New York"
    assert found["alexm@studio.test"].full_name == "Alexander Moreau"
    assert found["ingrid@studio.test"].full_name == "Ingrid Solberg"


@pytest.mark.parametrize("title", ["Director, Architect", "Senior Landscape Architect", "Architect MArch",
                                   "Project Manager", "Interior Architect, MA"])
def test_job_titles_are_not_names(title):
    assert not looks_like_name(title)


def test_collision_variants_support_main_pattern():
    found = extract_from_html(fixture("cards_titles.html"), "https://studio.test/people", "studio.test")
    r = detect_pattern("studio.test", found)
    assert r.template == "first"
    assert r.support == 3 and r.variant_support == 2 and r.contradict == 0  # alexm (nickname) + danielhe
    assert "when the name is taken" in r.summary


@pytest.mark.parametrize("name,local", [("Alexander Moreau", "alexm"), ("Christopher Leung", "chrisl"),
                                        ("Daniel Herzog", "danielhe"), ("Anne Cecilie Haug", "annececilie")])
def test_loose_first_name_matching(name, local):
    assert match_templates(parse_name(name), local)


def test_nicknames_not_used_for_guessing():
    from finder.patterns import render
    assert render("first", parse_name("Alexander Moreau")) == ["alexander"]


def test_find_prefers_fallback_when_first_name_shared():
    from finder.store import DomainReport
    pairs = [("Oskar Lind", "oskar"), ("Ingrid Sol", "ingrid"), ("Mia Berg", "mia"), ("Anna Holm", "annah"),
             ("Anna Vik", "annav"), ("Per Dahl", "per")]
    es = _emails(pairs, domain="studio.test")
    store = MemoryStore()
    store.save_report(DomainReport("studio.test", es, detect_pattern("studio.test", es)))
    r = find_email("Anna Berg", "studio.test", store, verify=False)
    assert r.best.email == "annab@studio.test" and "shared" in r.best.why
    plain = next(c for c in r.alternatives if c.email == "anna@studio.test")
    assert plain.score < r.best.score
    other = find_email("Kari Nord", "studio.test", store, verify=False)
    assert other.best.email == "kari@studio.test"
