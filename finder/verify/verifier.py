"""Orchestrates the verification checks and scores the result."""
from __future__ import annotations

import time
from typing import Callable, Optional

from ..cache import DomainCache
from ..config import Settings, settings as default_settings
from ..deadline import Deadline
from ..models import CheckResult, VerificationResult
from . import providers, smtp, syntax
from .gibberish import looks_random
from .mx import MxLookup, lookup_mx
from .ratelimit import DomainRateLimiter

FREE_PROVIDER_PENALTY = 10
GIBBERISH_PENALTY = 15

MxResolver = Callable[[str], MxLookup]


class Verifier:
    """Holds the shared state (settings, domain cache, rate limiter) for verification.

    One instance should be shared per process so caching and rate limiting
    apply across calls. `mx_resolver` can be swapped out in tests.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        cache: Optional[DomainCache] = None,
        limiter: Optional[DomainRateLimiter] = None,
        mx_resolver: Optional[MxResolver] = None,
    ):
        self.settings = settings or default_settings
        self.cache = cache if cache is not None else DomainCache(self.settings.cache_path)
        self.limiter = limiter or DomainRateLimiter(self.settings.smtp_min_interval)
        self._mx_resolver = mx_resolver  # None = real DNS via lookup_mx

    def verify(self, email: str) -> VerificationResult:
        started = time.monotonic()
        result = self._verify(syntax.normalize(email))
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result

    def _verify(self, email: str) -> VerificationResult:
        # 1. Syntax
        ok, detail, _local, domain = syntax.check_syntax(email)
        result = VerificationResult(email=email, domain=domain or "", status="invalid", confidence=0)
        result.checks.append(CheckResult("syntax", ok, detail))
        if not ok:
            result.reason = f"bad syntax: {detail}"
            return result
        email = f"{_local}@{domain}"
        result.email = email
        random_, signals = looks_random(_local)
        result.is_gibberish = random_
        result.checks.append(CheckResult("random", not random_,
                                         f"looks randomly generated ({', '.join(signals)})" if random_ else "reads like a real name or word"))

        # 2. Free / disposable provider
        result.is_free = providers.is_free_provider(domain)
        result.is_disposable = providers.is_disposable(domain)
        if result.is_disposable:
            result.checks.append(CheckResult("provider", False, "disposable / throwaway domain"))
        elif result.is_free:
            result.checks.append(CheckResult("provider", False, "free webmail provider (not a work email)"))
        else:
            result.checks.append(CheckResult("provider", True, "company domain"))

        # Network budget for this verification (DNS + SMTP). Rate-limit waits are added back on.
        deadline = Deadline(self.settings.verify_deadline)

        # 3. MX
        if self._mx_resolver is not None:
            mx = self._mx_resolver(domain)
        else:
            mx = lookup_mx(domain, self.settings, self.cache, deadline)
        result.mx_hosts = mx.hosts
        if not mx.ok:
            result.checks.append(CheckResult("mx", False if mx.definitive else None, mx.detail))
            if mx.definitive:
                result.reason = f"domain cannot receive mail: {mx.detail}"
                return result
            return self._finish(result, "unknown", 10, f"DNS lookup failed: {mx.detail}")
        result.checks.append(CheckResult("mx", True, mx.detail))

        # 4 + 5. SMTP probe and catch-all detection
        cached_catch_all = self.cache.get(domain, "catch_all", self.settings.catch_all_cache_ttl)
        if self.settings.smtp_transport != "disabled":
            deadline.extend(self.limiter.wait(domain))
        probe = smtp.probe(mx.hosts, email, self.settings, test_catch_all=cached_catch_all is None, deadline=deadline)
        result.smtp = probe.responses
        return self._score_smtp(result, probe, cached_catch_all)

    def _score_smtp(self, result: VerificationResult, probe: smtp.ProbeResult, cached_catch_all) -> VerificationResult:
        domain = result.domain

        if probe.skipped:
            result.checks.append(CheckResult("smtp", None, probe.detail))
            result.checks.append(CheckResult("catch_all", None, "needs SMTP"))
            return self._finish(result, "unknown", 40, "MX looks fine but mailbox was not checked (SMTP disabled)")
        if probe.error:
            result.checks.append(CheckResult("smtp", None, probe.error))
            result.checks.append(CheckResult("catch_all", None, "needs SMTP"))
            return self._finish(result, "unknown", 30, f"could not connect to mail server: {probe.error}")

        rcpt = probe.rcpt
        if rcpt is None:  # session failed before RCPT (greeting/EHLO/MAIL rejected, timeout, disconnect)
            last = probe.responses[-1] if probe.responses else None
            note = f"{probe.detail}" + (f" ({last.code} {last.message})" if last else "")
            result.checks.append(CheckResult("smtp", None, note))
            result.checks.append(CheckResult("catch_all", None, "needs SMTP"))
            return self._finish(result, "unknown", 30, f"SMTP session did not reach RCPT: {note}")

        code = rcpt.code
        if 400 <= code < 500:
            result.checks.append(CheckResult("smtp", None, f"temporary failure {code} (greylisting or rate limit)"))
            result.checks.append(CheckResult("catch_all", None, "not tested"))
            return self._finish(result, "unknown", 30, f"server deferred with {code}; try again later")

        if 500 <= code < 600:
            if smtp.is_policy_rejection(code, rcpt.message) or code == 552:
                result.checks.append(CheckResult("smtp", None, f"{code} looks like a policy/IP block, not a missing mailbox"))
                result.checks.append(CheckResult("catch_all", None, "not tested"))
                return self._finish(result, "unknown", 25, f"server refused us ({code}), mailbox existence unknown")
            result.checks.append(CheckResult("smtp", False, f"mailbox rejected: {code}"))
            result.checks.append(CheckResult("catch_all", None, "not tested"))
            result.reason = f"mail server says this mailbox does not exist ({code})"
            return self._finish(result, "invalid", 5, result.reason)

        if not 200 <= code < 300:
            result.checks.append(CheckResult("smtp", None, f"unexpected reply {code}"))
            return self._finish(result, "unknown", 30, f"unexpected RCPT reply {code}")

        result.checks.append(CheckResult("smtp", True, f"mailbox accepted: {code}"))

        # Catch-all: was the random gibberish address also accepted?
        catch_all = cached_catch_all
        source = " (cached)"
        if catch_all is None and probe.random_rcpt is not None:
            rcode, rmsg = probe.random_rcpt.code, probe.random_rcpt.message
            source = f" (random address got {rcode})"
            if 200 <= rcode < 300:
                catch_all = True
            elif 500 <= rcode < 600 and not smtp.is_policy_rejection(rcode, rmsg):
                catch_all = False
            if catch_all is not None:
                self.cache.set(domain, "catch_all", catch_all)
        result.is_catch_all = catch_all

        if catch_all:
            result.checks.append(CheckResult("catch_all", False, "domain accepts every address" + source))
            return self._finish(result, "catch_all", 40, "domain is catch-all; acceptance proves nothing")
        if catch_all is False:
            result.checks.append(CheckResult("catch_all", True, "domain rejects unknown addresses" + source))
            return self._finish(result, "valid", 95, "mail server accepted this mailbox and rejects random ones")
        result.checks.append(CheckResult("catch_all", None, "catch-all test inconclusive" + source))
        return self._finish(result, "valid", 75, "mailbox accepted; could not confirm the domain isn't catch-all")

    @staticmethod
    def _finish(result: VerificationResult, status, confidence: int, reason: str) -> VerificationResult:
        if result.is_disposable:
            status, confidence, reason = "invalid", min(confidence, 5), "disposable domain: " + reason
        elif result.is_free and status != "invalid":
            confidence = max(0, confidence - FREE_PROVIDER_PENALTY)
            reason += " (free webmail, not a work address)"
        if result.is_gibberish and status != "invalid":
            confidence = max(0, confidence - GIBBERISH_PENALTY)
            reason += " (address looks randomly generated)"
        result.status = status
        result.confidence = confidence
        result.reason = reason
        return result


_default: Optional[Verifier] = None


def default_verifier() -> Verifier:
    global _default
    if _default is None:
        _default = Verifier()
    return _default


def verify_email(email: str, verifier: Optional[Verifier] = None) -> VerificationResult:
    """Verify one email address. Shares cache and rate limits across calls."""
    return (verifier or default_verifier()).verify(email)
