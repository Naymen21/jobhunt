"""Step 3: MX lookup with caching and timeouts."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import dns.exception
import dns.resolver

from ..cache import DomainCache
from ..config import Settings
from ..deadline import Deadline, DeadlineExceeded


@dataclass
class MxLookup:
    hosts: list[str] = field(default_factory=list)  # sorted by preference, best first
    ok: bool = False  # domain can receive mail
    definitive: bool = True  # False when DNS timed out / errored (don't call it invalid)
    detail: str = ""


def _resolver(timeout: float) -> dns.resolver.Resolver:
    r = dns.resolver.Resolver()
    r.timeout = timeout
    r.lifetime = timeout
    return r


def lookup_mx(domain: str, settings: Settings, cache: DomainCache, deadline: Optional[Deadline] = None) -> MxLookup:
    cached = cache.get(domain, "mx", settings.mx_cache_ttl)
    if cached is not None:
        result = MxLookup(**cached)
        result.detail += " (cached)"
        return result

    try:
        timeout = deadline.cap(settings.dns_timeout) if deadline else settings.dns_timeout
    except DeadlineExceeded as e:
        return MxLookup([], False, False, str(e))
    result = _lookup(domain, timeout)
    if result.definitive:  # don't cache transient DNS failures
        cache.set(domain, "mx", result.__dict__)
    return result


def _lookup(domain: str, timeout: float) -> MxLookup:
    resolver = _resolver(timeout)
    try:
        answers = resolver.resolve(domain, "MX")
        records = sorted((r.preference, str(r.exchange).rstrip(".")) for r in answers)
        hosts = [h for _, h in records if h]  # "." exchange = null MX (RFC 7505)
        if not hosts:
            return MxLookup([], False, True, "null MX record: domain does not accept mail")
        return MxLookup(hosts, True, True, f"{len(hosts)} MX record(s), top: {hosts[0]}")
    except dns.resolver.NXDOMAIN:
        return MxLookup([], False, True, "domain does not exist (NXDOMAIN)")
    except dns.resolver.NoAnswer:
        pass  # fall through to implicit MX (RFC 5321 §5.1: use the A/AAAA record)
    except dns.resolver.NoNameservers as e:
        return MxLookup([], False, False, f"no nameserver answered: {e}")
    except dns.exception.Timeout:
        return MxLookup([], False, False, f"DNS timeout after {timeout}s")
    except dns.exception.DNSException as e:
        return MxLookup([], False, False, f"DNS error: {e}")

    for rtype in ("A", "AAAA"):
        try:
            resolver.resolve(domain, rtype)
            return MxLookup([domain], True, True, f"no MX record; falling back to {rtype} record (implicit MX)")
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            continue
        except dns.exception.DNSException as e:
            return MxLookup([], False, False, f"DNS error on {rtype} fallback: {e}")
    return MxLookup([], False, True, "no MX, A or AAAA records")
