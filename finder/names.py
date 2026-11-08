"""Person-name parsing and normalisation for email pattern matching."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

# Letters NFKD can't decompose into ASCII.
_TRANSLIT = str.maketrans({
    "ß": "ss", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe", "ø": "o", "Ø": "o", "ł": "l", "Ł": "l",
    "đ": "d", "Đ": "d", "ð": "d", "þ": "th", "ı": "i",
})

TITLES = {"mr", "mrs", "ms", "miss", "mx", "dr", "prof", "professor", "sir", "dame", "rev", "hon"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "md", "mba", "cpa", "esq", "jd", "pe", "rn", "cfa", "dds", "msc", "bsc"}
PARTICLES = {"van", "von", "de", "der", "den", "del", "della", "di", "da", "du", "la", "le", "dos", "das", "st", "bin", "ibn", "al"}

# Words that show up in headings/link text but are never part of a person's name.
NOT_NAME_WORDS = {
    "contact", "us", "team", "our", "the", "and", "press", "media", "sales", "support", "office", "info",
    "email", "e-mail", "mail", "phone", "tel", "fax", "inc", "llc", "ltd", "gmbh", "company", "department",
    "about", "read", "more", "view", "profile", "linkedin", "twitter", "github", "get", "in", "touch",
    "careers", "jobs", "home", "privacy", "policy", "terms", "legal", "investor", "investors", "relations",
    "news", "newsroom", "blog", "board", "directors", "leadership", "management", "founder", "founders",
    "ceo", "cto", "cfo", "coo", "vp", "head", "of", "for", "with", "to", "at", "by", "on", "from", "all",
    "rights", "reserved", "copyright", "general", "inquiries", "enquiries", "help", "customer", "service",
    "partnerships", "partner", "marketing", "engineering", "design", "product", "operations", "hr",
    "human", "resources", "communications", "editor", "editorial", "write", "send", "message", "click",
    "here", "learn", "meet", "join", "apply", "now", "open", "positions", "location", "address", "street",
}

# Job titles, degrees and org words: "Senior Architect", "Director, Design", "Architect MArch".
NOT_NAME_WORDS |= {
    "architect", "architects", "architecture", "director", "directors", "senior", "junior", "landscape", "interior",
    "manager", "managers", "managing", "engineer", "engineering", "designer", "designers", "partner", "partners",
    "associate", "associates", "principal", "lead", "assistant", "intern", "consultant", "analyst", "specialist",
    "coordinator", "officer", "president", "vice", "chief", "executive", "founding", "owner", "advisor",
    "adviser", "counsel", "attorney", "lawyer", "paralegal", "accountant", "developer", "scientist", "researcher",
    "professor", "lecturer", "writer", "producer", "artist", "curator", "administrator", "secretary", "treasurer",
    "chair", "chairman", "chairwoman", "member", "staff", "studio", "group", "global", "regional", "international",
    "technical", "creative", "strategy", "business", "development", "planner", "planning", "urban", "urbanist",
    "project", "projects", "program", "programme", "graphic", "visual", "digital", "brand", "content", "sales",
    "account", "accounts", "client", "clients", "talent", "recruiter", "recruiting", "people", "culture", "office",
    "operations", "general", "legal", "finance", "financial", "it", "data", "software", "hardware", "research",
    "landscapes", "interiors", "march", "msc", "bsc", "ba", "ma", "mfa", "bfa", "mba", "phd", "di", "dipl", "ing",
    "arch", "riba", "aia", "leed", "ap", "pe", "ra", "fellow", "emeritus", "board", "trustee", "committee",
}

_NAME_TOKEN = re.compile(r"^[^\W\d_][^\W\d_'’.\-]*(?:[-'’][^\W\d_]+)*\.?$", re.UNICODE)


def ascii_fold(text: str) -> str:
    text = text.translate(_TRANSLIT)
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def norm_token(token: str) -> str:
    """'O’Brien' -> 'obrien', 'José' -> 'jose'. Hyphens are kept (callers make variants)."""
    token = ascii_fold(token).lower()
    return re.sub(r"[^a-z\-]", "", token).strip("-")


@dataclass
class ParsedName:
    raw: str
    first: str  # normalised, may contain a hyphen ("jean-luc")
    last: str  # normalised, may contain hyphen; particles joined ("vanderberg")
    middles: list[str] = field(default_factory=list)
    last_parts: list[str] = field(default_factory=list)  # ["van", "der", "berg"] / ["garcia", "lopez"]

    @property
    def display(self) -> str:
        return self.raw

    def first_variants(self, loose: bool = False) -> list[str]:
        """Canonical first name first. With loose (matching only, never for guessing):
        nickname forms (Alexander <-> Alex) and first+middle ("Anne Cecilie" -> annececilie)."""
        extra = [self.first.split("-")[0]] if "-" in self.first else []
        if loose:
            plain = self.first.replace("-", "")
            extra += NICKNAMES.get(plain, []) + [full for full, shorts in NICKNAMES.items() if plain in shorts]
            if self.middles:
                extra.append(plain + self.middles[0].replace("-", ""))
        return _hyphen_variants(self.first, extra)

    def last_variants(self) -> list[str]:
        extra = []
        core = [p for p in self.last_parts if p not in PARTICLES]
        if len(self.last_parts) > 1 and core:
            extra += [core[-1], core[0]]  # "berg" (van der berg), "lopez"/"garcia" (garcia lopez)
        if "-" in self.last:
            pieces = self.last.split("-")
            extra += [pieces[0], pieces[-1]]
        return _hyphen_variants(self.last, extra)


# Common English short forms. Used only to *recognise* addresses like alexk@ for Alexander K.
NICKNAMES: dict[str, list[str]] = {
    "alexander": ["alex", "alec", "sasha"], "alexandra": ["alex", "sasha"], "andrew": ["andy", "drew"],
    "anthony": ["tony"], "benjamin": ["ben"], "catherine": ["cathy", "kate"], "katherine": ["kate", "kathy", "katie"],
    "christopher": ["chris"], "christina": ["chris", "tina"], "christine": ["chris"], "daniel": ["dan", "danny"],
    "david": ["dave"], "edward": ["ed", "ted"], "elizabeth": ["liz", "beth", "eliza", "lisa"], "frederick": ["fred"],
    "gregory": ["greg"], "jacob": ["jake"], "james": ["jim", "jamie"], "jennifer": ["jen", "jenny"],
    "jeffrey": ["jeff"], "jonathan": ["jon"], "joseph": ["joe"], "joshua": ["josh"], "kenneth": ["ken"],
    "lawrence": ["larry"], "margaret": ["maggie", "meg"], "matthew": ["matt"], "maximilian": ["max"],
    "michael": ["mike", "mick"], "nicholas": ["nick"], "nicolas": ["nico", "nick"], "patrick": ["pat"],
    "patricia": ["pat", "trish"], "peter": ["pete"], "rebecca": ["becky", "becca"], "richard": ["rick", "rich", "dick"],
    "robert": ["rob", "bob", "bobby"], "ronald": ["ron"], "samuel": ["sam"], "samantha": ["sam"],
    "stephen": ["steve"], "steven": ["steve"], "susan": ["sue"], "thomas": ["tom"], "timothy": ["tim"],
    "victoria": ["vicky", "tori"], "william": ["will", "bill"], "zachary": ["zach"], "beatrix": ["bea"],
    "beatrice": ["bea"], "johannes": ["hannes", "johan"], "magdalena": ["magda"], "francesca": ["fran"],
    "alessandro": ["sandro"], "giuseppe": ["beppe"], "francisco": ["paco", "fran"], "kristoffer": ["kris"],
    "kristian": ["kris"], "christian": ["chris"], "fredrik": ["fred"],
}


def _hyphen_variants(value: str, extra: list[str]) -> list[str]:
    out = [value.replace("-", ""), value]
    out += extra
    seen, result = set(), []
    for v in out:
        if v and v not in seen:
            seen.add(v)
            result.append(v)
    return result


def _clean_raw(raw: str) -> str:
    raw = re.sub(r"\(.*?\)|\[.*?\]|\".*?\"", " ", raw)  # nicknames, notes
    return " ".join(raw.replace(" ", " ").split())


def parse_name(raw: Optional[str]) -> Optional[ParsedName]:
    """Parse 'Dr. José García-López Jr.' / 'Smith, Jane' / 'Jane Q. van der Berg'."""
    if not raw:
        return None
    raw = _clean_raw(raw)
    if "," in raw:
        before, after = [p.strip() for p in raw.split(",", 1)]
        after_tokens = [norm_token(t) for t in after.split()]
        if after_tokens and all(t in SUFFIXES for t in after_tokens):
            raw = before  # "Jane Doe, PhD"
        elif after and len(before.split()) == 1:
            raw = f"{after} {before}"  # "Doe, Jane"
        else:
            raw = before
    tokens = [t for t in raw.split() if t]
    while tokens and norm_token(tokens[0].rstrip(".")) in TITLES:
        tokens.pop(0)
    while tokens and norm_token(tokens[-1].rstrip(".")) in SUFFIXES:
        tokens.pop()
    normed = [norm_token(t) for t in tokens]
    pairs = [(t, n) for t, n in zip(tokens, normed) if n]
    if len(pairs) < 2:
        return None
    first = pairs[0][1]
    rest = [n for _, n in pairs[1:]]
    # Last name = final token plus any particles directly before it.
    i = len(rest) - 1
    while i > 0 and rest[i - 1] in PARTICLES:
        i -= 1
    last_parts = rest[i:]
    middles = rest[:i]
    if len(last_parts) == 1 and "-" in last_parts[0]:
        last_parts = last_parts[0].split("-")
        last = "-".join(last_parts)
    else:
        last = "".join(last_parts)
    return ParsedName(raw=" ".join(tokens), first=first, last=last, middles=middles, last_parts=last_parts)


def looks_like_name(text: Optional[str]) -> bool:
    """Heuristic: 2-4 capitalised word tokens, none of them a UI/boilerplate word."""
    if not text:
        return False
    text = _clean_raw(text).strip(" -–—|:•·,")
    if not 3 <= len(text) <= 48 or "@" in text or any(ch.isdigit() for ch in text):
        return False
    tokens = text.split()
    while tokens and norm_token(tokens[0].rstrip(".")) in TITLES:
        tokens = tokens[1:]
    while tokens and norm_token(tokens[-1].rstrip(".,")) in SUFFIXES:
        tokens = tokens[:-1]
    if not 2 <= len(tokens) <= 4:
        return False
    for tok in tokens:
        tok = tok.rstrip(",")
        low = norm_token(tok)
        if low in PARTICLES:
            continue
        if not _NAME_TOKEN.match(tok) or not tok[0].isupper():
            return False
        if low in NOT_NAME_WORDS:
            return False
    # all-caps headings like "OUR TEAM" are usually not names (already filtered); allow "JANE DOE" though
    return True


def same_person(a: Optional[str], b: Optional[str]) -> bool:
    pa, pb = parse_name(a), parse_name(b)
    if not pa or not pb:
        return False
    return pa.first.replace("-", "") == pb.first.replace("-", "") and pa.last.replace("-", "") == pb.last.replace("-", "")
