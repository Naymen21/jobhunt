import time
from dataclasses import replace

import pytest

from finder.cache import DomainCache
from finder.config import Settings
from finder.verify import Verifier
from finder.verify.mx import MxLookup
from finder.verify.ratelimit import DomainRateLimiter
from finder.verify.syntax import check_syntax

from . import fake_smtp


@pytest.fixture(scope="module")
def server():
    controller, handler, port = fake_smtp.start()
    yield handler, port
    controller.stop()


def make_verifier(port, min_interval=0.0, transport="direct"):
    settings = replace(
        Settings(), smtp_transport=transport, smtp_port=port, smtp_timeout=3,
        smtp_min_interval=min_interval, helo_host="test.local",
    )
    return Verifier(
        settings=settings,
        cache=DomainCache(None),
        limiter=DomainRateLimiter(min_interval),
        mx_resolver=lambda d: MxLookup(["127.0.0.1"], True, True, "fake MX"),
    )


@pytest.mark.parametrize("email,ok", [
    ("jane.doe@acme.com", True),
    ("j+tag@sub.acme.co.uk", True),
    ("no-at-sign.acme.com", False),
    ("two@@acme.com", False),
    (".lead@acme.com", False),
    ("a..b@acme.com", False),
    ("x@acme", False),
    ("x@-acme.com", False),
    ("x@acme.123", False),
])
def test_syntax(email, ok):
    assert check_syntax(email)[0] is ok


def test_valid_mailbox(server):
    handler, port = server
    r = make_verifier(port).verify("alice@normal.test")
    assert r.status == "valid" and r.confidence == 95
    assert r.is_catch_all is False
    codes = {s.stage: s.code for s in r.smtp}
    assert codes["connect"] == 220 and codes["rcpt_to"] == 250 and codes["rcpt_to_random"] == 550
    assert codes["quit"] == 221
    assert handler.data_calls == 0


def test_missing_mailbox(server):
    _, port = server
    r = make_verifier(port).verify("nobody@normal.test")
    assert r.status == "invalid" and r.confidence == 5
    assert r.check("smtp").passed is False


def test_catch_all(server):
    _, port = server
    v = make_verifier(port)
    r = v.verify("anyone@catchall.test")
    assert r.status == "catch_all" and r.is_catch_all is True and r.confidence == 40
    # second lookup uses the cached catch-all result and skips the random RCPT
    r2 = v.verify("other@catchall.test")
    assert r2.status == "catch_all"
    assert "rcpt_to_random" not in {s.stage for s in r2.smtp}
    assert "(cached)" in r2.check("catch_all").detail


def test_greylisting_is_unknown(server):
    _, port = server
    r = make_verifier(port).verify("alice@grey.test")
    assert r.status == "unknown"
    assert r.smtp[-2].code == 451  # last before QUIT


def test_policy_block_is_unknown_not_invalid(server):
    _, port = server
    r = make_verifier(port).verify("alice@blocked.test")
    assert r.status == "unknown"


def test_never_sends_data(server):
    handler, port = server
    v = make_verifier(port)
    for e in ["alice@normal.test", "x@catchall.test", "y@normal.test"]:
        v.verify(e)
    assert handler.data_calls == 0


def test_rate_limit_spaces_same_domain(server):
    _, port = server
    v = make_verifier(port, min_interval=1.0)
    t0 = time.monotonic()
    v.verify("alice@normal.test")
    v.verify("bob@normal.test")
    assert time.monotonic() - t0 >= 1.0


def test_connection_refused_is_unknown():
    port = fake_smtp.free_port()  # nothing listening here
    r = make_verifier(port).verify("alice@normal.test")
    assert r.status == "unknown"
    assert r.check("smtp").passed is None


def test_disabled_transport(server):
    _, port = server
    r = make_verifier(port, transport="disabled").verify("alice@normal.test")
    assert r.status == "unknown" and r.confidence == 40 and r.smtp == []


def test_no_mx_is_invalid():
    v = Verifier(
        settings=replace(Settings(), smtp_transport="disabled"),
        cache=DomainCache(None),
        mx_resolver=lambda d: MxLookup([], False, True, "domain does not exist (NXDOMAIN)"),
    )
    r = v.verify("x@nowhere.test")
    assert r.status == "invalid" and r.confidence == 0


def test_dns_timeout_is_unknown():
    v = Verifier(
        settings=replace(Settings(), smtp_transport="disabled"),
        cache=DomainCache(None),
        mx_resolver=lambda d: MxLookup([], False, False, "DNS timeout after 5s"),
    )
    assert v.verify("x@slow.test").status == "unknown"


def test_free_and_disposable_flags():
    v = Verifier(
        settings=replace(Settings(), smtp_transport="disabled"),
        cache=DomainCache(None),
        mx_resolver=lambda d: MxLookup(["mx." + d], True, True, "fake"),
    )
    g = v.verify("someone@gmail.com")
    assert g.is_free and g.confidence == 30
    m = v.verify("someone@mailinator.com")
    assert m.is_disposable and m.status == "invalid"


def test_overall_deadline_caps_slow_hosts():
    # 10.255.255.x is non-routable: connects hang until timeout. Two hosts x 10s would be 20s+;
    # the 3s overall deadline must cut that short.
    settings = replace(Settings(), smtp_transport="direct", smtp_timeout=10, verify_deadline=3,
                       smtp_min_interval=0)
    v = Verifier(settings=settings, cache=DomainCache(None), limiter=DomainRateLimiter(0),
                 mx_resolver=lambda d: MxLookup(["10.255.255.1", "10.255.255.2"], True, True, "fake"))
    t0 = time.monotonic()
    r = v.verify("alice@slow.test")
    assert time.monotonic() - t0 < 4.5
    assert r.status == "unknown"
    assert "deadline" in r.check("smtp").detail
