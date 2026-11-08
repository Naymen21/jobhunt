"""Domain search: run every source, merge what they find, detect the pattern, save."""
from __future__ import annotations

import re
import threading
from dataclasses import replace
from typing import Callable, Optional
from urllib.parse import urlsplit

from .config import Settings, settings as default_settings
from .crawl.extract import merge
from .models import FoundEmail
from .names import parse_name
from .patterns import detect_pattern, match_templates
from .sources import Source, SourceContext, default_sources
from .store import DomainReport, SourceStatus, Store

Progress = Callable[[dict], None]


class BadDomain(ValueError):
    pass


def normalize_domain(value: str) -> str:
    """'https://www.Acme.com/team' / 'jane@acme.com' / 'acme.com' -> 'acme.com'"""
    value = (value or "").strip().lower()
    if "@" in value and "/" not in value:
        value = value.rsplit("@", 1)[1]
    if "://" not in value:
        value = "http://" + value
    host = urlsplit(value).hostname or ""
    host = host.rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise BadDomain(f"invalid domain: {value!r}")
    if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", host):
        raise BadDomain(f"invalid domain: {value!r}")
    return host


def _share_names(emails: list[FoundEmail]) -> None:
    """If one source has a name for an address and another doesn't, copy it over."""
    names: dict[str, str] = {}
    for e in emails:
        if e.full_name and e.kind == "personal":
            p = parse_name(e.full_name)
            fits = bool(p and match_templates(p, e.email.split("@")[0]))
            if e.email not in names or fits:
                names[e.email] = e.full_name
    for e in emails:
        if not e.full_name and e.email in names and e.kind == "personal":
            e.full_name, e.name_source = names[e.email], "other-source"
    # A real title (from the website) beats GitHub's generic "Code contributor".
    titles: dict[str, FoundEmail] = {}
    for e in emails:
        if e.title and (e.email not in titles or titles[e.email].title == "Code contributor"):
            titles[e.email] = e
    for e in emails:
        best = titles.get(e.email)
        if best and (not e.title or e.title == "Code contributor") and best is not e:
            e.title, e.department, e.seniority = best.title, best.department, best.seniority


def search_domain(domain: str, store: Store, *, refresh: bool = False, sources: Optional[list[Source]] = None,
                  options: Optional[dict] = None, settings: Optional[Settings] = None,
                  progress: Optional[Progress] = None, cancel: Optional[threading.Event] = None) -> DomainReport:
    domain = normalize_domain(domain)
    options = dict(options or {})
    if not refresh:
        cached = store.load_report(domain)
        if cached is not None:
            return replace(cached, cached=True)

    settings = settings or default_settings
    progress = progress or (lambda e: None)
    cancel = cancel or threading.Event()
    sources = sources if sources is not None else default_sources()

    found: list[FoundEmail] = []
    statuses: list[SourceStatus] = []
    for src in sources:
        if cancel.is_set():
            statuses.append(SourceStatus(src.name, "cancelled", "Not run (search cancelled)"))
            continue
        progress({"phase": src.name, "message": f"Running {src.label}", "source": src.name})
        ctx = SourceContext(settings=settings, progress=progress, cancel=cancel, options=options)
        res = src.safe_run(domain, ctx)
        found.extend(res.emails)
        statuses.append(SourceStatus(res.source, res.status, res.message, res.stats))
        progress({"phase": src.name, "message": f"{src.label}: {res.message}", "source": src.name,
                  "source_status": res.status, "emails_found_total": len({e.email for e in found})})

    emails = merge(found)
    _share_names(emails)
    pattern = detect_pattern(domain, emails)
    github = next((s for s in statuses if s.source == "github"), None)
    complete = not cancel.is_set() and not any(s.status in ("error", "cancelled") for s in statuses)
    report = DomainReport(domain=domain, emails=emails, pattern=pattern, sources=statuses, complete=complete,
                          github_org=(github.stats.get("org") if github else None) or options.get("github_org"))
    store.save_report(report)
    return report
