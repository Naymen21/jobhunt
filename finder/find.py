"""Find Email: name + domain -> most likely address, with reasons and ranked alternatives."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import scoring
from .discovery import normalize_domain
from .models import FoundEmail, PatternResult, VerificationResult
from .names import ParsedName, parse_name, same_person
from .patterns.templates import COLLISION_VARIANTS, PRIORS, TEMPLATES, render
from .store import Store
from .verify import Verifier, default_verifier

MAX_CANDIDATES = 6


@dataclass
class Candidate:
    email: str
    template: Optional[str]  # None for a seen-in-the-wild address that fits no template
    evidence: str  # "seen", "seen-no-name", "pattern", "other-template", "base-rate", "taken"
    evidence_score: int
    score: int
    why: str
    source_url: Optional[str] = None
    sources: int = 0  # how many sightings back this address
    verification: Optional[VerificationResult] = None
    verify_note: Optional[str] = None


@dataclass
class FindResult:
    name: str
    domain: str
    best: Optional[Candidate]
    alternatives: list[Candidate] = field(default_factory=list)
    pattern: Optional[PatternResult] = None
    searched: bool = False  # has this domain had a Domain Search?
    found_count: int = 0  # distinct addresses found for the domain so far
    notes: list[str] = field(default_factory=list)


class BadName(ValueError):
    pass


def _seen_index(emails: list[FoundEmail]) -> dict[str, list[FoundEmail]]:
    idx: dict[str, list[FoundEmail]] = {}
    for e in emails:
        idx.setdefault(e.email, []).append(e)
    return idx


def _support_for(pattern: Optional[PatternResult], template: str) -> int:
    if not pattern:
        return 0
    return next((b.support for b in pattern.breakdown if b.template == template), 0)


def _namesakes(name: ParsedName, raw_name: str, seen: dict[str, list[FoundEmail]]) -> list[str]:
    """Addresses of *other* people at the domain who share this first name."""
    first = name.first.replace("-", "")
    out = []
    for email, sightings in seen.items():
        for s in sightings:
            other = parse_name(s.full_name) if s.full_name and s.kind == "personal" else None
            if other and other.first.replace("-", "") == first and not same_person(s.full_name, raw_name):
                out.append(email)
                break
    return sorted(out)


def _generate(name: ParsedName, domain: str, pattern: Optional[PatternResult],
              seen: dict[str, list[FoundEmail]], raw_name: str) -> list[Candidate]:
    out: dict[str, Candidate] = {}

    # 1. This exact person was seen in a source.
    for email, sightings in seen.items():
        named = [s for s in sightings if s.kind == "personal" and same_person(s.full_name, raw_name)]
        if named:
            out[email] = Candidate(email, None, "seen", scoring.SEEN_WITH_NAME, scoring.SEEN_WITH_NAME,
                                   f"seen with this name at {named[0].source_url}", named[0].source_url, len(sightings))

    # 2. Generated from templates: detected pattern first, then by base rate.
    has_pattern = bool(pattern and pattern.template)
    variants = COLLISION_VARIANTS.get(pattern.template, ()) if has_pattern else ()
    # Other people here with the same first name (Anna Schüller -> annas@): if the company falls back
    # to a variant on collisions, the plain address is probably already somebody's.
    namesakes = _namesakes(name, raw_name, seen) if variants and pattern.variant_support else []
    order = sorted(TEMPLATES, key=lambda t: (t != (pattern.template if has_pattern else None), -PRIORS[t]))
    for template in order:
        locals_ = render(template, name)
        if not locals_:
            continue
        email = f"{locals_[0]}@{domain}"
        if email in out:
            continue
        sightings = seen.get(email, [])
        owners = {s.full_name for s in sightings if s.full_name}
        if owners and not any(same_person(o, raw_name) for o in owners):
            owner = sorted(owners)[0]
            out[email] = Candidate(email, template, "taken", 0, 0, f"already used by {owner} ({sightings[0].source_url})",
                                   sightings[0].source_url, len(sightings))
            continue
        if sightings:
            out[email] = Candidate(email, template, "seen-no-name", scoring.SEEN_WITHOUT_NAME, scoring.SEEN_WITHOUT_NAME,
                                   f"this address was seen at {sightings[0].source_url} (no name attached)",
                                   sightings[0].source_url, len(sightings))
            continue
        is_variant = has_pattern and template in COLLISION_VARIANTS.get(pattern.template, ())
        evidence_score = scoring.candidate_score(
            template, PRIORS[template], pattern.template if has_pattern else None,
            pattern.confidence if has_pattern else 0, pattern.total if pattern else 0, _support_for(pattern, template),
            variant_of_pattern=is_variant,
            pattern_consistent=(pattern.support + pattern.variant_support) if has_pattern else 0)
        if has_pattern and template == pattern.template:
            why = f"matches {template} pattern from {pattern.support} of {pattern.total} examples"
            kind = "pattern"
        elif is_variant and _support_for(pattern, template):
            why = (f"{template}: used at this company when {pattern.template} is already taken "
                   f"({_support_for(pattern, template)} examples)")
            kind = "pattern"
        elif has_pattern:
            sup = _support_for(pattern, template)
            why = f"{template}: {sup} of {pattern.total} examples fit" if sup else f"{template}: no examples at this domain"
            kind = "other-template"
        else:
            why = f"{template}: common format ({round(PRIORS[template] * 100)}% of companies); no examples found for this domain"
            kind = "base-rate"
        if namesakes and kind == "pattern":
            evidence_score, why = scoring.namesake_adjust(evidence_score, template == pattern.template, why, namesakes)
        out[email] = Candidate(email, template, kind, evidence_score, evidence_score, why)

    ranked = sorted(out.values(), key=lambda c: (-c.score, TEMPLATES.index(c.template) if c.template in TEMPLATES else -1))
    return ranked


def find_email(full_name: str, domain: str, store: Store, verifier: Optional[Verifier] = None,
               verify: bool = True) -> FindResult:
    domain = normalize_domain(domain)
    parsed = parse_name(full_name)
    if parsed is None:
        raise BadName("Enter a first and last name.")
    report = store.load_report(domain)
    seen = _seen_index(report.emails if report else [])
    pattern = report.pattern if report else None
    result = FindResult(name=parsed.raw, domain=domain, best=None, pattern=pattern, searched=report is not None,
                        found_count=report.unique_addresses if report else 0)
    if report is None:
        result.notes.append("This domain hasn't been searched yet, so these guesses use general base rates only. "
                            "Run a Domain Search to find real examples.")
    elif not (pattern and pattern.template):
        result.notes.append("Domain Search found no name + email pairs here, so no pattern could be detected.")
    if report and report.robots_blocked:
        result.notes.append("The company website was skipped because robots.txt disallows crawling.")

    generated = _generate(parsed, domain, pattern, seen, full_name)
    candidates = [c for c in generated if c.evidence != "taken"][:MAX_CANDIDATES]
    taken = [c for c in generated if c.evidence == "taken"]

    if verify and candidates:
        verifier = verifier or default_verifier()
        for c in candidates:
            c.verification = verifier.verify(c.email)
            c.score, c.verify_note = scoring.apply_verification(c.evidence_score, c.verification)
        candidates.sort(key=lambda c: -c.score)

    result.best = candidates[0] if candidates else None
    result.alternatives = candidates[1:] + taken[:3]
    return result


__all__ = ["find_email", "FindResult", "Candidate", "BadName"]
