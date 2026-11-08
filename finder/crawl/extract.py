"""Find addresses at a target domain in HTML / plain text, plus the person's name when nearby.

Handles: plain text, mailto: links, "name [at] company [dot] com" style obfuscation,
Cloudflare email protection (data-cfemail and /cdn-cgi/l/email-protection#...),
schema.org JSON-LD Person records, and addresses sitting in attributes.
"""
from __future__ import annotations

import html as htmllib
import json
import re
from email.utils import getaddresses
from functools import lru_cache
from typing import Iterable, Optional
from urllib.parse import unquote

from bs4 import BeautifulSoup, NavigableString, Tag

from ..models import FoundEmail, SourceType
from ..names import looks_like_name, parse_name
from ..patterns.templates import match_templates
from ..titles import classify, clean_title, looks_like_title

ROLE_LOCALS = {
    "info", "contact", "contacts", "hello", "hi", "hey", "support", "help", "helpdesk", "sales", "press", "media",
    "pr", "news", "newsroom", "careers", "career", "jobs", "job", "hr", "recruiting", "recruitment", "talent",
    "hiring", "admin", "administrator", "office", "team", "marketing", "billing", "accounts", "accounting",
    "finance", "invoices", "legal", "privacy", "security", "abuse", "webmaster", "postmaster", "hostmaster",
    "noreply", "no-reply", "donotreply", "do-not-reply", "feedback", "partners", "partner", "partnerships",
    "investors", "investor", "ir", "enquiries", "enquiry", "inquiries", "inquiry", "service", "services",
    "customerservice", "customercare", "care", "orders", "order", "events", "studio", "general", "reception",
    "booking", "bookings", "editor", "editors", "editorial", "newsletter", "community", "developers", "dev",
    "devrel", "compliance", "dpo", "gdpr", "accessibility", "social", "founders", "hq", "mail", "email",
    "office-manager", "operations", "ops", "it", "tech", "engineering", "product", "design", "ceo", "board",
    "procurement", "purchasing", "vendors", "suppliers", "shop", "store", "returns", "sponsorship", "donate",
    "volunteer", "membership", "members", "subscriptions", "unsubscribe", "bounce", "bounces", "notifications",
    "alerts", "root", "sysadmin", "jobs-us", "talentacquisition", "people", "peopleops", "recruiter",
    # automation identities common in git commits
    "release", "releases", "bot", "bots", "build", "builds", "ci", "deploy", "deployer", "automation", "git",
    "github", "gitlab", "devops", "robot", "service-account", "jenkins", "packages", "opensource", "oss",
}
_ROLE_SPLIT = re.compile(r"[.\-_+]")

_DOT = r"(?:\s*\.\s*|\s*\[\s*dot\s*\]\s*|\s*\(\s*dot\s*\)\s*|\s*\{\s*dot\s*\}\s*|\s*<\s*dot\s*>\s*|\s+dot\s+)"
_DOT_OBF = r"(?:\s*\[\s*dot\s*\]\s*|\s*\(\s*dot\s*\)\s*|\s*\{\s*dot\s*\}\s*|\s*<\s*dot\s*>\s*|\s+dot\s+)"
_AT_STRONG = r"(?:\s*@\s*|\s*＠\s*|\s*\[\s*at\s*\]\s*|\s*\(\s*at\s*\)\s*|\s*\{\s*at\s*\}\s*|\s*<\s*at\s*>\s*|\s*\[@\]\s*|\s*\(@\)\s*)"
_AT_WEAK = r"(?:\s+at\s+)"
_LOCAL = r"(?<![\w.%+\-])([A-Za-z0-9][A-Za-z0-9_%+\-]*(?:" + _DOT + r"[A-Za-z0-9_%+\-]+)*)"
_DOT_WORDS = re.compile(r"\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|<\s*dot\s*>|\s+dot\s+|\.)\s*", re.I)


@lru_cache(maxsize=64)
def _domain_regexes(domain: str) -> tuple[re.Pattern, re.Pattern]:
    labels = [re.escape(l) for l in domain.lower().split(".")]
    sub = r"(?:[a-z0-9-]+" + _DOT + r"){0,2}"
    any_dot_domain = sub + _DOT.join(labels)
    obf_dot_domain = r"(?:[a-z0-9-]+" + _DOT + r"){0,2}" + _DOT_OBF.join(labels)
    tail = r"(?![\w-])(?!\.[\w-])"  # not followed by more domain labels (acme.com.evil.org); "acme.com. Next" is fine
    strong = re.compile(_LOCAL + _AT_STRONG + "(" + any_dot_domain + ")" + tail, re.I)
    # " at " is only trusted when the domain is also obfuscated ("jane at acme dot com"),
    # otherwise "contact us at acme.com" would become us@acme.com.
    weak = re.compile(_LOCAL + _AT_WEAK + "(" + obf_dot_domain + ")" + tail, re.I)
    return strong, weak


def _clean(part: str) -> str:
    return _DOT_WORDS.sub(".", part).strip(".").lower()


def find_addresses(text: str, domain: str) -> list[tuple[str, int, int]]:
    """(email, start, end) for every address at `domain` (or its subdomains) in text."""
    out, seen_spans = [], []
    for rx in _domain_regexes(domain):
        for m in rx.finditer(text):
            if any(s <= m.start() < e for s, e in seen_spans):
                continue
            local, host = _clean(m.group(1)), _clean(m.group(2))
            if not local or ".." in local or not (host == domain or host.endswith("." + domain)):
                continue
            out.append((f"{local}@{host}", m.start(), m.end()))
            seen_spans.append((m.start(), m.end()))
    return out


def is_role(email: str) -> bool:
    local = email.split("@", 1)[0].lower()
    if local in ROLE_LOCALS:
        return True
    # "press-uk", "sales.eu", "support-team" are roles; "dev.patel", "people.smith" are people.
    parts = [p for p in _ROLE_SPLIT.split(local) if p]
    if not parts or len(parts) > 2 or parts[0] not in ROLE_LOCALS:
        return False
    return len(parts) == 1 or parts[1] in ROLE_LOCALS or len(parts[1]) <= 3


def cf_decode(encoded: str) -> Optional[str]:
    """Decode Cloudflare's XOR-obfuscated email (first byte is the key)."""
    try:
        key = int(encoded[:2], 16)
        return "".join(chr(int(encoded[i:i + 2], 16) ^ key) for i in range(2, len(encoded), 2))
    except (ValueError, IndexError):
        return None


def _snippet(text: str, start: int, end: int, width: int = 70) -> str:
    s = text[max(0, start - width): end + width]
    return " ".join(s.split())[:200]


# Name-source quality, best first. Used when one page mentions an address several times.
_NAME_RANK = {"json-ld": 0, "mailto-text": 1, "same-element": 2, "heading": 3, "inline": 4, None: 9}

_INLINE_BEFORE = re.compile(
    r"((?:[A-Z][\w'’\-]+\.?\s+){1,3}[A-Z][\w'’\-]+)\s*(?:[,:|•·(<\[–—-]|\s(?:at|email|e-mail|mail)\b:?)?\s*$")
_INLINE_AFTER = re.compile(r"^\s*[)\]>]?\s*[,:|–—-]?\s*\(?\s*((?:[A-Z][\w'’\-]+\.?\s+){1,3}[A-Z][\w'’\-]+)")


def _name_matches(name: str, email: str) -> bool:
    parsed = parse_name(name)
    return bool(parsed and match_templates(parsed, email.split("@")[0]))


def _strip_name_verbs(text: str) -> str:
    return re.sub(r"^(?:e-?mail|contact|mail|write to|send (?:an )?e-?mail to|message)\s+", "", text.strip(), flags=re.I)


_NAME_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "dt", "figcaption", "caption")


def _name_candidates(container: Tag) -> tuple[list[str], list[str]]:
    """(names in heading/name-like elements, names that are just lines of text)."""
    tagged, lines = [], []
    for el in container.find_all(True):
        if el.name in _NAME_TAGS or any(
            "name" in str(v).lower() for v in (el.get("class") or []) + [el.get("itemprop", ""), el.get("id", "")]
        ):
            txt = el.get_text(" ", strip=True)
            if looks_like_name(txt):
                tagged.append(txt)
    for line in container.get_text("\n", strip=True).split("\n"):
        line = line.strip()
        if looks_like_name(line) and line not in tagged:
            lines.append(line)
    return tagged, lines


def _count_addresses(text: str, domain: str) -> int:
    return len({e for e, _, _ in find_addresses(text, domain)})


def name_near(el: Tag, email: str, domain: str) -> tuple[Optional[str], Optional[str]]:
    """Best guess at the person an address belongs to, from the surrounding markup."""
    if el.name == "a":
        for txt in (el.get_text(" ", strip=True), el.get("title", ""), el.get("aria-label", "")):
            txt = _strip_name_verbs(txt or "")
            if looks_like_name(txt):
                return txt, "mailto-text"
    # Walk up from the address. A name that fits the address wins at once. Otherwise a name
    # in a heading/name element beats a plain line of text, even a level or two further up:
    # cards often put job title and city as plain lines right next to the address.
    node: Optional[Tag] = el
    fallback: tuple[Optional[str], Optional[str]] = (None, None)
    for level in range(6):
        if node is None or node.name in ("body", "html", "[document]"):
            break
        text = node.get_text(" ", strip=True)
        if len(text) > 900:
            break
        many = _count_addresses(text, domain) > 1
        tagged, lines = _name_candidates(node)
        how = "same-element" if level <= 1 else "heading"
        for c in tagged + lines:
            if _name_matches(c, email):
                return c, how
        if many:
            break  # container covers several people: nearest-name guess would be unreliable
        if tagged:
            return tagged[0], how
        if lines and fallback[0] is None:
            fallback = (lines[0], how)
        node = node.parent
    return fallback


def _title_line(line: str, name: Optional[str]) -> Optional[str]:
    line = " ".join(line.split()).strip(" -–—|:•·,")
    if not line or "@" in line or (name and line == name) or looks_like_name(line):
        return None
    title = clean_title(line)  # drop "NCARB, AIA" etc. before judging
    return title if title and looks_like_title(title) else None


def title_near(el: Tag, domain: str, name: Optional[str]) -> Optional[str]:
    """Job title in the same card/block as the address (the line next to the name, usually)."""
    node: Optional[Tag] = el
    for _ in range(6):
        if node is None or node.name in ("body", "html", "[document]"):
            break
        text = node.get_text(" ", strip=True)
        if len(text) > 900 or _count_addresses(text, domain) > 1:
            break
        lines = [l for l in node.get_text("\n", strip=True).split("\n") if l.strip()]
        if name and name in lines:  # prefer the lines right after the name
            i = lines.index(name)
            lines = lines[i + 1:] + lines[:i]
        for line in lines:
            title = _title_line(line, name)
            if title:
                return title
        node = node.parent
    return None


_TITLE_SPLIT = re.compile(r"\s*[,|–—:;()\n]\s*|\s+-\s+")


def _inline_title(text: str, start: int, name: Optional[str]) -> Optional[str]:
    """'Laura Kim, Head of Press: laura@...' -> 'Head of Press'"""
    for seg in reversed(_TITLE_SPLIT.split(text[max(0, start - 120):start])):
        if name and name in seg:
            seg = seg.replace(name, "")
        title = _title_line(seg, name)
        if title:
            return title
    return None


def _inline_name(text: str, start: int, end: int, email: str) -> Optional[str]:
    before = _INLINE_BEFORE.search(text[max(0, start - 80):start])
    after = _INLINE_AFTER.search(text[end:end + 80])
    options = [m.group(1).strip() for m in (before, after) if m]
    options = [o for o in options if looks_like_name(o)]
    for o in options:
        if _name_matches(o, email):
            return o
    return options[0] if options else None


class _Collector:
    def __init__(self, domain: str, url: str, source_type: SourceType):
        self.domain, self.url, self.source_type = domain, url, source_type
        self.by_email: dict[str, FoundEmail] = {}

    def add(self, email: str, name: Optional[str], name_source: Optional[str], context: str,
            title: Optional[str] = None) -> None:
        email = email.lower()
        host = email.rsplit("@", 1)[-1]
        if not (host == self.domain or host.endswith("." + self.domain)):
            return
        role = is_role(email)
        if role:
            name, name_source, title = None, None, None
        cur = self.by_email.get(email)
        department, seniority = classify(title)
        new = FoundEmail(email=email, domain=self.domain, kind="role" if role else "personal",
                         source_type=self.source_type, source_url=self.url, full_name=name,
                         name_source=name_source, context=context, title=title,
                         department=department, seniority=seniority)
        if cur is None:
            self.by_email[email] = new
            return
        if title and not cur.title:
            cur.title, cur.department, cur.seniority = title, department, seniority
        better_rank = _NAME_RANK[name_source] < _NAME_RANK[cur.name_source]
        fixes_mismatch = name and cur.full_name and not _name_matches(cur.full_name, email) and _name_matches(name, email)
        if name and (cur.full_name is None or better_rank or fixes_mismatch):
            cur.full_name, cur.name_source = name, name_source
        if not cur.context:
            cur.context = context


def _walk_jsonld(data, out: list[tuple[str, Optional[str], Optional[str]]]) -> None:
    if isinstance(data, list):
        for item in data:
            _walk_jsonld(item, out)
    elif isinstance(data, dict):
        email = data.get("email")
        if isinstance(email, str):
            name = data.get("name")
            if not isinstance(name, str) and data.get("givenName") and data.get("familyName"):
                name = f"{data['givenName']} {data['familyName']}"
            job = data.get("jobTitle")
            out.append((email.replace("mailto:", "").strip(), name if isinstance(name, str) else None,
                        job if isinstance(job, str) else None))
        for v in data.values():
            if isinstance(v, (dict, list)):
                _walk_jsonld(v, out)


def extract_from_html(html: str, url: str, domain: str) -> list[FoundEmail]:
    domain = domain.lower()
    col = _Collector(domain, url, "website")
    soup = BeautifulSoup(html, "lxml")

    for script in soup.find_all("script", type=re.compile("ld\\+json", re.I)):
        pairs: list[tuple[str, Optional[str], Optional[str]]] = []
        try:
            _walk_jsonld(json.loads(script.string or ""), pairs)
        except (ValueError, TypeError):
            pass
        for email, name, job in pairs:
            ok = looks_like_name(name)
            col.add(email, name if ok else None, "json-ld" if ok else None, "schema.org data", title=job)

    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()

    # Cloudflare email protection: decode in place so the normal passes see real addresses.
    for el in soup.select("[data-cfemail]"):
        decoded = cf_decode(el.get("data-cfemail", ""))
        if decoded:
            el.replace_with(NavigableString(decoded))
    for a in soup.select('a[href*="/cdn-cgi/l/email-protection#"]'):
        decoded = cf_decode(a["href"].split("#", 1)[1])
        if decoded:
            a["href"] = "mailto:" + decoded

    for a in soup.select("a[href]"):
        href = a["href"].strip()
        if not href.lower().startswith("mailto:"):
            continue
        targets = unquote(href[7:].split("?", 1)[0])
        for display, email in getaddresses([targets.replace(";", ",")]):
            email = email.strip()
            if "@" not in email:
                continue
            if looks_like_name(display):
                name, source = display, "mailto-text"
            else:
                name, source = name_near(a, email.lower(), domain)
            col.add(email, name, source, _snippet(a.parent.get_text(" ", strip=True) if a.parent else "", 0, 0, 100),
                    title=title_near(a, domain, name))

    for node in soup.find_all(string=True):
        text = str(node)
        if len(text) < 6:
            continue
        hits = find_addresses(text, domain)
        for email, start, end in hits:
            name = _inline_name(text, start, end, email)
            source = "inline" if name else None
            if not name and isinstance(node.parent, Tag):
                name, source = name_near(node.parent, email, domain)
            title = _inline_title(text, start, name)
            if not title and isinstance(node.parent, Tag):
                title = title_near(node.parent, domain, name)
            col.add(email, name, source, _snippet(text, start, end), title=title)

    # Addresses only present in attributes (data-email="...", value="...", etc.)
    raw = htmllib.unescape(unquote(html))
    for email, start, end in find_addresses(raw, domain):
        if email not in col.by_email:
            col.add(email, None, None, "")
    return list(col.by_email.values())


def extract_from_text(text: str, url: str, domain: str, source_type: SourceType = "pdf") -> list[FoundEmail]:
    domain = domain.lower()
    col = _Collector(domain, url, source_type)
    for email, start, end in find_addresses(text, domain):
        line_start = text.rfind("\n", 0, start) + 1
        line_end = text.find("\n", end)
        line_end = len(text) if line_end == -1 else line_end
        name = _inline_name(text, start, end, email)
        prev_start = text.rfind("\n", 0, max(0, line_start - 1)) + 1
        prev = text[prev_start:max(prev_start, line_start - 1)].strip()
        if not name:  # name on the line above (common in PDF contact blocks)
            name = prev if looks_like_name(prev) else None
        title = _inline_title(text, start, name) or _title_line(prev, name)
        col.add(email, name, "inline" if name else None, _snippet(text, line_start, line_end, 0), title=title)
    return list(col.by_email.values())


def merge(found: Iterable[FoundEmail]) -> list[FoundEmail]:
    """Keep one entry per (email, source_url)."""
    out: dict[tuple[str, str], FoundEmail] = {}
    for f in found:
        key = (f.email, f.source_url)
        cur = out.get(key)
        if cur is None or (f.full_name and not cur.full_name):
            out[key] = f
    return list(out.values())
