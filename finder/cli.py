"""Command line interface: python -m finder verify someone@company.com [--json]"""
from __future__ import annotations

import argparse
import json
import sys

from .models import VerificationResult
from .verify import verify_email

_MARK = {True: "PASS", False: "FAIL", None: "----"}


def _print_human(r: VerificationResult) -> None:
    print(f"{r.email}")
    print(f"  status:     {r.status.upper()}  (confidence {r.confidence}/100)")
    print(f"  reason:     {r.reason}")
    for c in r.checks:
        print(f"  [{_MARK[c.passed]}] {c.name:<10} {c.detail}")
    if r.mx_hosts:
        print(f"  mx:         {', '.join(r.mx_hosts[:3])}")
    for s in r.smtp:
        print(f"  smtp {s.stage:<15} {s.code} {s.message[:100]}")
    print(f"  took {r.duration_ms} ms")


def _store():
    # The CLI is a front end like the website, so it may use the shared SQLite storage.
    # The engine modules themselves only see the Store protocol.
    from storage.db import init_db
    from storage.repo import SqlStore

    init_db()
    return SqlStore()


def _print_report(report) -> None:
    p = report.pattern
    print(f"{report.domain}  ({'cached ' + report.searched_at if report.cached else 'searched ' + report.searched_at})")
    for s in report.sources:
        print(f"  [{s.status:>14}] {s.source:<10} {s.message}")
    print(f"  pattern: {p.summary}")
    for b in p.breakdown[:4]:
        print(f"      {b.template:<12} {b.support} example(s)")
    print(f"  {report.unique_addresses} addresses ({len({e.email for e in report.personal})} personal):")
    seen = set()
    for e in sorted(report.emails, key=lambda e: (e.kind, e.email)):
        if e.email in seen:
            continue
        seen.add(e.email)
        print(f"    {e.email:<38} {e.kind:<8} {e.full_name or '':<24} {e.source_url}")


def _print_find(r) -> None:
    for note in r.notes:
        print(f"  note: {note}")
    if r.pattern and r.pattern.template:
        print(f"  pattern: {r.pattern.summary}")
    if r.best:
        b = r.best
        print(f"  BEST  {b.email}  {b.score}/100  {b.why}" + (f"  [{b.verify_note}]" if b.verify_note else ""))
    for c in r.alternatives:
        print(f"        {c.email:<38} {c.score:>3}  {c.why}" + (f"  [{c.verify_note}]" if c.verify_note else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m finder", description="Email finder engine")
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("verify", help="verify one or more email addresses")
    v.add_argument("emails", nargs="+", metavar="EMAIL")
    v.add_argument("--json", action="store_true", help="print results as JSON")
    sp = sub.add_parser("search", help="crawl a company's site + GitHub for addresses and detect its email pattern")
    sp.add_argument("domain")
    sp.add_argument("--refresh", action="store_true", help="ignore cached results and search again")
    sp.add_argument("--github-org", help="GitHub org to use instead of guessing")
    sp.add_argument("--max-pages", type=int, help="page budget for the crawl")
    fp = sub.add_parser("find", help="most likely address for a person at a domain")
    fp.add_argument("name", help='full name, e.g. "Jane Doe"')
    fp.add_argument("domain")
    for p in (sp, fp):
        p.add_argument("--json", action="store_true", help="print results as JSON")
    args = parser.parse_args(argv)

    if args.command == "search":
        from .discovery import search_domain

        def progress(e):
            if not args.json and e.get("message"):
                print(f"  .. {e['message'][:110]}", file=sys.stderr)

        report = search_domain(args.domain, _store(), refresh=args.refresh, progress=progress,
                               options={"github_org": args.github_org, "max_pages": args.max_pages})
        if args.json:
            json.dump(report.to_dict(), sys.stdout, indent=2)
            print()
        else:
            _print_report(report)
        return 0

    if args.command == "find":
        from dataclasses import asdict

        from .find import find_email

        r = find_email(args.name, args.domain, _store())
        if args.json:
            json.dump(asdict(r), sys.stdout, indent=2, default=str)
            print()
        else:
            print(f"{r.name} @ {r.domain}")
            _print_find(r)
        return 0

    if args.command == "verify":
        results = []
        for email in args.emails:
            r = verify_email(email)
            results.append(r)
            if not args.json:
                _print_human(r)
                print()
        if args.json:
            data = [r.to_dict() for r in results]
            json.dump(data[0] if len(data) == 1 else data, sys.stdout, indent=2)
            print()
        return 0 if all(r.status != "invalid" for r in results) else 1
    return 2
