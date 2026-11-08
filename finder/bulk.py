"""Bulk find: parse a pasted/uploaded table of people, then find each person's email."""
from __future__ import annotations

import csv
import io
import re
import threading
from dataclasses import dataclass, field
from typing import Optional

from .config import Settings
from .discovery import BadDomain, search_domain
from .find import BadName, find_email
from .resolve import resolve_company
from .store import Store
from .verify import Verifier

MAX_ROWS = 500

_NAME = {"name", "full name", "fullname", "full_name", "person", "contact", "contact name", "employee"}
_FIRST = {"first name", "first", "firstname", "first_name", "given name", "forename"}
_LAST = {"last name", "last", "lastname", "last_name", "surname", "family name"}
_DOMAIN = {"domain", "website", "url", "site", "company domain", "company website", "email domain"}
_COMPANY = {"company", "company name", "organization", "organisation", "employer", "org", "account", "business"}


@dataclass
class BulkInput:
    name: str
    company: str  # domain or company name


@dataclass
class ParsedTable:
    rows: list[BulkInput] = field(default_factory=list)
    columns: dict = field(default_factory=dict)  # role -> header text, for the preview
    skipped: int = 0  # rows missing a name or company
    truncated: bool = False
    error: Optional[str] = None


def _norm_header(h: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 _]", " ", h.lower()).replace("_", " ").split())


def parse_table(text: str) -> ParsedTable:
    text = (text or "").strip().lstrip("﻿")
    if not text:
        return ParsedTable(error="Paste or upload a list first.")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if not rows:
        return ParsedTable(error="No rows found.")

    header = [_norm_header(c) for c in rows[0]]

    def find(names: set) -> Optional[int]:
        return next((i for i, h in enumerate(header) if h in names), None)

    idx = {"name": find(_NAME), "first": find(_FIRST), "last": find(_LAST), "domain": find(_DOMAIN), "company": find(_COMPANY)}
    has_header = any(v is not None for v in idx.values())
    out = ParsedTable()
    if has_header:
        body = rows[1:]
        org = idx["domain"] if idx["domain"] is not None else idx["company"]
        if org is None or (idx["name"] is None and (idx["first"] is None or idx["last"] is None)):
            return ParsedTable(error="Couldn't find both a name column (name, or first + last) and a company "
                                     "column (domain, website or company).")
        if idx["name"] is not None:
            out.columns["name"] = rows[0][idx["name"]]
        else:
            out.columns["name"] = f"{rows[0][idx['first']]} + {rows[0][idx['last']]}"
        out.columns["company"] = rows[0][org]
    else:  # no header: assume "name, company"
        body = rows
        idx = {"name": 0, "first": None, "last": None, "domain": 1, "company": None}
        org = 1
        out.columns = {"name": "column 1", "company": "column 2"}
        if any(len(r) < 2 for r in body[:5]):
            return ParsedTable(error="Without a header row, each line needs: name, company or domain.")

    def cell(r: list, i: Optional[int]) -> str:
        return r[i].strip() if i is not None and i < len(r) else ""

    for r in body:
        name = cell(r, idx["name"]) if idx["name"] is not None else f"{cell(r, idx['first'])} {cell(r, idx['last'])}".strip()
        company = cell(r, org)
        if not name or not company:
            out.skipped += 1
            continue
        if len(out.rows) >= MAX_ROWS:
            out.truncated = True
            break
        out.rows.append(BulkInput(name, company))
    if not out.rows:
        out.error = "No usable rows (each needs a name and a company or domain)."
    return out


@dataclass
class BulkResult:
    status: str  # "done" | "needs_domain" | "error"
    domain: Optional[str] = None
    email: Optional[str] = None
    score: Optional[int] = None
    evidence: Optional[str] = None
    why: str = ""


def find_one(item: BulkInput, store: Store, verifier: Verifier, settings: Settings, *,
             search_unsearched: bool = False, cancel: Optional[threading.Event] = None) -> BulkResult:
    try:
        res = resolve_company(item.company, store, settings=settings)
    except BadDomain as e:
        return BulkResult("error", why=str(e))
    if not res.domain:
        if res.candidates:
            options = ", ".join(c.domain for c in res.candidates)
            return BulkResult("needs_domain", why=f"Couldn't tell which domain; candidates: {options}. Put the domain in the list.")
        return BulkResult("needs_domain", why="No matching domain found. Put the domain in the list.")
    domain = res.domain
    if search_unsearched and store.load_report(domain) is None:
        search_domain(domain, store, settings=settings, cancel=cancel)
    try:
        found = find_email(item.name, domain, store, verifier)
    except (BadName, BadDomain) as e:
        return BulkResult("error", domain=domain, why=str(e))
    if not found.best:
        return BulkResult("error", domain=domain, why="No candidate addresses could be built for this name.")
    b = found.best
    return BulkResult("done", domain=domain, email=b.email, score=b.score, evidence=b.evidence, why=b.why)
