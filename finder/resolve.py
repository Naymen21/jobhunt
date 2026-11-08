"""Company name -> domain, for free: guess likely domains, check each has mail servers and a live
homepage, and confirm with the page's title/metadata. Ambiguous results are returned for the
owner to pick from; every outcome is cached in the Store.

No search engines are queried (project rule), so companies whose domain doesn't resemble their
name (Alphabet -> abc.xyz) won't be found; typing the domain always works.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field, replace
from typing import Callable, Optional
from urllib.parse import urlsplit

import dns.exception
import dns.resolver
from bs4 import BeautifulSoup

from .config import Settings, settings as default_settings
from .crawl.robots import load_robots
from .discovery import BadDomain, normalize_domain
from .http import PoliteFetcher
from .models import now_iso
from .names import ascii_fold
from .store import Store

SUFFIXES = {"inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "gmbh", "ag",
            "sa", "sas", "plc", "bv", "nv", "oy", "ab", "as", "pty", "srl", "spa", "kk", "group", "holdings", "the"}
STOPWORDS = {"the", "and", "of", "for", "a", "an", "&"}
TLDS = ["com", "io", "co", "ai", "gg", "net", "org", "dev"]
PARKED_HINTS = ("domain is for sale", "buy this domain", "this domain may be for sale", "domain for sale",
                "parked free", "parkingcrew", "sedo", "hugedomains", "dan.com", "godaddy", "afternic",
                "domain has expired", "coming soon", "under construction", "index of /")
MAX_GUESSES = 14
DEADLINE = 25.0  # seconds for checking all guesses
AUTO_MIN_SCORE = 65
AUTO_MIN_GAP = 15
SHOW_MIN_SCORE = 25
REDIRECT_BONUS = 10  # per other guessed domain that redirects to this one
REDIRECT_BONUS_MAX = 20

# Second-level suffixes where the registrable domain has three labels (example.co.uk).
_TWO_LEVEL = {"co.uk", "org.uk", "ac.uk", "com.au", "net.au", "co.nz", "co.jp", "com.br", "co.in", "com.sg", "co.za", "com.mx"}


@dataclass
class DomainCandidate:
    domain: str
    score: int = 0
    has_mx: bool = False
    reachable: bool = False
    title: str = ""
    description: str = ""
    reasons: list[str] = field(default_factory=list)


@dataclass
class Resolution:
    query: str  # what was typed
    key: str  # normalised name used for caching
    status: str  # "domain" (typed a domain) | "resolved" | "ambiguous" | "not_found"
    domain: Optional[str] = None
    candidates: list[DomainCandidate] = field(default_factory=list)
    cached: bool = False
    confirmed: bool = False  # chosen by the owner

    @property
    def needs_choice(self) -> bool:
        return self.status == "ambiguous"


def looks_like_domain(text: str) -> bool:
    text = text.strip()
    return "." in text and " " not in text


def company_tokens(name: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", ascii_fold(name).lower().replace("&", " and "))
    while words and words[-1] in SUFFIXES:
        words.pop()
    return [w for w in words if w not in STOPWORDS] or words


def cache_key(name: str) -> str:
    return " ".join(company_tokens(name))


def guess_domains(name: str) -> list[str]:
    tokens = company_tokens(name)
    if not tokens:
        return []
    slug = "".join(tokens)
    first = tokens[0]
    out = [f"{slug}.com", f"{slug}.io", f"{slug}.co", f"{slug}.ai", f"{slug}.gg"]
    if len(tokens) > 1:
        out += [f"{'-'.join(tokens)}.com", f"{first}.com"]
    out += [f"{slug}hq.com", f"get{slug}.com", f"{slug}inc.com", f"{slug}.net", f"{slug}.org", f"try{slug}.com",
            f"{slug}.dev", f"{slug}app.com"]
    seen, result = set(), []
    for d in out:
        if d not in seen:
            seen.add(d)
            result.append(d)
    return result[:MAX_GUESSES]


def registrable(host: str) -> str:
    host = (host or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    n = 3 if ".".join(labels[-2:]) in _TWO_LEVEL else 2
    return ".".join(labels[-n:])


# --- network checks (replaceable in tests) ------------------------------------------

def dns_info(domain: str, timeout: float = 4.0) -> tuple[bool, bool]:
    """(domain exists, has MX). Any DNS answer for A/AAAA/MX counts as existing."""
    r = dns.resolver.Resolver()
    r.timeout = r.lifetime = timeout
    has_mx = exists = False
    try:
        has_mx = any(str(x.exchange).rstrip(".") for x in r.resolve(domain, "MX"))
        exists = True
    except dns.resolver.NXDOMAIN:
        return False, False
    except dns.exception.DNSException:
        pass
    if not exists:
        for rtype in ("A", "AAAA"):
            try:
                r.resolve(domain, rtype)
                exists = True
                break
            except dns.exception.DNSException:
                continue
    return exists, has_mx


@dataclass
class Homepage:
    final_host: str = ""
    title: str = ""
    site_name: str = ""
    description: str = ""
    reachable: bool = False
    blocked: bool = False  # robots.txt disallows the homepage
    bot_blocked: bool = False  # answered 401/403/429/503: the site exists but refuses automated visits


def fetch_homepage(domain: str, settings: Settings) -> Homepage:
    fetcher = PoliteFetcher(replace(settings, http_timeout=min(settings.http_timeout, 8.0)), delay=0)
    try:
        for base in (f"https://{domain}", f"https://www.{domain}", f"http://{domain}"):
            robots = load_robots(base, fetcher)
            if robots.status == "unavailable" and "could not fetch" in robots.detail:
                continue
            if not robots.allowed(base + "/"):
                return Homepage(final_host=domain, blocked=True)
            res = fetcher.get(base + "/", max_bytes=2 * 1024 * 1024, polite=False)
            if res.status in (401, 403, 429, 503):
                return Homepage(final_host=urlsplit(res.url).hostname or domain, bot_blocked=True)
            if not res.ok:
                continue
            soup = BeautifulSoup(res.text, "lxml")

            def meta(**attrs) -> str:
                tag = soup.find("meta", attrs=attrs)
                return (tag.get("content") or "") if tag else ""

            return Homepage(
                final_host=urlsplit(res.url).hostname or domain,
                title=" ".join((soup.title.get_text() if soup.title else "").split())[:200],
                site_name=meta(property="og:site_name")[:120],
                description=(meta(name="description") or meta(property="og:description"))[:300],
                reachable=True,
            )
        return Homepage(final_host=domain)
    finally:
        fetcher.close()


# --- scoring --------------------------------------------------------------------------

def _norm_text(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", ascii_fold(text or "").lower()))


def score_candidate(name: str, cand: DomainCandidate, page: Homepage) -> None:
    tokens = company_tokens(name)
    slug = "".join(tokens)
    label = cand.domain.split(".")[0]
    head = _norm_text(f"{page.title} {page.site_name}")
    head_compact = head.replace(" ", "")
    desc = _norm_text(page.description)
    score = 0
    if page.reachable and (any(h in head for h in PARKED_HINTS) or any(h in desc for h in PARKED_HINTS[:6])):
        cand.reasons.append("looks like a parked / for-sale page")
        cand.score = 0
        return
    if tokens and head:
        hit = sum(1 for t in tokens if re.search(rf"\b{re.escape(t)}\b", head) or t in head_compact)
        if hit == len(tokens):
            score += 50 + (10 if " ".join(tokens) in head or slug in head_compact else 0)
            cand.reasons.append("page title names the company")
        elif hit:  # "Epic" for "Epic Games" is weak evidence: often a different company
            score += round(15 * hit / len(tokens))
            cand.reasons.append(f"page title mentions only {hit} of {len(tokens)} name words")
    if tokens and desc:
        score += round(10 * sum(1 for t in tokens if re.search(rf"\b{re.escape(t)}\b", desc)) / len(tokens))
    if label == slug:
        score += 35 if len(tokens) > 1 else 20  # "epicgames" is far more specific than "epic"
        cand.reasons.append("domain matches the name exactly")
    elif tokens and label == tokens[0]:
        score += 10
    elif slug and slug in label:
        score += 8
    if cand.has_mx:
        score += 10
        cand.reasons.append("receives email")
    if page.reachable:
        score += 10
    elif page.bot_blocked:
        score += 5
        cand.reasons.append("site exists but blocks automated visits")
    elif page.blocked:
        cand.reasons.append("homepage not checked (robots.txt)")
    else:
        score -= 10  # no website at all: parked registration or mail-only domain
        cand.reasons.append("no website")
    if cand.domain.endswith(".com"):
        score += 5  # companies overwhelmingly use .com when they own it
    cand.score = max(0, min(100, score))


# --- main ---------------------------------------------------------------------------------

def _check(name: str, domain: str, settings: Settings, dns_fn, page_fn) -> Optional[DomainCandidate]:
    exists, has_mx = dns_fn(domain)
    if not exists:
        return None
    page = page_fn(domain, settings)
    final = registrable(page.final_host) if page.final_host else domain
    cand = DomainCandidate(domain=final, has_mx=has_mx, reachable=page.reachable, title=page.title or page.site_name,
                           description=page.description)
    if final != domain:
        cand.reasons.append(f"{domain} redirects here")
        cand.has_mx = dns_fn(final)[1] if final else has_mx
    score_candidate(name, cand, page)
    return cand


def resolve_company(query: str, store: Optional[Store] = None, *, settings: Optional[Settings] = None,
                    refresh: bool = False, dns_fn: Callable = dns_info,
                    page_fn: Callable = fetch_homepage) -> Resolution:
    query = (query or "").strip()
    if looks_like_domain(query):
        domain = normalize_domain(query)  # raises BadDomain
        return Resolution(query, domain, "domain", domain=domain, confirmed=True)
    key = cache_key(query)
    if not key:
        raise BadDomain("Enter a company name or domain.")
    if store is not None and not refresh:
        cached = store.load_company(key)
        if cached is not None:
            return _from_cache(query, key, cached)

    settings = settings or default_settings
    guesses = guess_domains(query)
    found: dict[str, DomainCandidate] = {}
    redirects: dict[str, set] = {}  # final domain -> other guesses that redirect to it
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="resolve") as pool:
        futures = {pool.submit(_check, query, d, settings, dns_fn, page_fn): d for d in guesses}
        done, _ = wait(futures, timeout=DEADLINE)
        for f in done:
            try:
                cand = f.result()
            except Exception:  # one broken site must not sink the lookup
                continue
            if not cand:
                continue
            if cand.domain != futures[f]:
                redirects.setdefault(cand.domain, set()).add(futures[f])
            prev = found.get(cand.domain)
            if prev is None or cand.score > prev.score:
                if prev:
                    cand.reasons = list(dict.fromkeys(cand.reasons + prev.reasons))
                found[cand.domain] = cand
            else:
                prev.reasons = list(dict.fromkeys(prev.reasons + cand.reasons))
    # A company that owns several of the guessed names and points them all at one site
    # (epicgames.dev -> epicgames.com) is strong evidence that site is the company.
    for domain, sources in redirects.items():
        cand = found[domain]
        cand.score = min(100, cand.score + min(REDIRECT_BONUS_MAX, REDIRECT_BONUS * len(sources)))
    ranked = sorted(found.values(), key=lambda c: -c.score)
    shown = [c for c in ranked if c.score >= SHOW_MIN_SCORE][:3]

    res = Resolution(query, key, "not_found", candidates=shown)
    if shown and shown[0].score >= AUTO_MIN_SCORE and (len(shown) == 1 or shown[0].score - shown[1].score >= AUTO_MIN_GAP):
        res.status, res.domain = "resolved", shown[0].domain
    elif shown:
        res.status = "ambiguous"
    if store is not None:
        store.save_company(key, res.domain, False, _to_cache(res), now_iso())
    return res


def confirm_company(query: str, domain: str, store: Store) -> Resolution:
    """The owner picked a domain for this company name: remember it."""
    domain = normalize_domain(domain)
    key = cache_key(query)
    prev = store.load_company(key)
    res = _from_cache(query, key, prev) if prev else Resolution(query, key, "resolved")
    res.status, res.domain, res.confirmed = "resolved", domain, True
    store.save_company(key, domain, True, _to_cache(res), now_iso())
    return res


def _to_cache(res: Resolution) -> dict:
    return {"status": res.status, "candidates": [asdict(c) for c in res.candidates]}


def _from_cache(query: str, key: str, data: dict) -> Resolution:
    cands = [DomainCandidate(**c) for c in data.get("candidates", [])]
    status = "resolved" if data.get("domain") else data.get("status", "not_found")
    return Resolution(query, key, status, domain=data.get("domain"), candidates=cands, cached=True,
                      confirmed=bool(data.get("confirmed")))
