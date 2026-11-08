"""Infer a domain's email format from name + email pairs."""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Optional

from ..models import FoundEmail, PatternResult, TemplateScore
from ..names import parse_name
from ..scoring import pattern_confidence
from .templates import COLLISION_VARIANTS, PRIORS, match_templates


def usable_pairs(emails: Iterable[FoundEmail]) -> list[tuple[str, str]]:
    """(name, email) pairs that can vote: personal addresses with a parseable name, one per address."""
    best: dict[str, str] = {}
    for e in emails:
        if e.kind != "personal" or not e.full_name or not parse_name(e.full_name):
            continue
        # Prefer a name that actually matches the address if several sources disagree.
        current = best.get(e.email)
        if current is None or (not _matches(current, e.email) and _matches(e.full_name, e.email)):
            best[e.email] = e.full_name
    return [(name, email) for email, name in best.items()]


def _matches(name: str, email: str) -> bool:
    parsed = parse_name(name)
    return bool(parsed and match_templates(parsed, email.split("@")[0]))


def detect_pattern(domain: str, emails: Iterable[FoundEmail]) -> PatternResult:
    pairs = usable_pairs(emails)
    result = PatternResult(domain=domain, template=None, total=len(pairs))
    if not pairs:
        return result

    support: dict[str, int] = defaultdict(int)
    share: dict[str, float] = defaultdict(float)
    for name, email in pairs:
        templates = match_templates(parse_name(name), email.split("@")[0])
        result.examples.append({"name": name, "email": email, "templates": templates})
        for t in templates:
            support[t] += 1
            share[t] += 1 / len(templates)

    if not support:
        result.contradict = len(pairs)
        return result

    # Rank by fractional share, then raw support, then how common the template is overall.
    ranked = sorted(support, key=lambda t: (share[t], support[t], PRIORS[t]), reverse=True)
    best = ranked[0]
    result.template = best
    result.support = support[best]
    variants = COLLISION_VARIANTS.get(best, ())
    result.variants = [v for v in variants if support.get(v)]
    result.variant_support = sum(1 for ex in result.examples
                                 if best not in ex["templates"] and any(v in ex["templates"] for v in variants))
    consistent = result.support + result.variant_support
    result.contradict = len(pairs) - consistent
    result.breakdown = [TemplateScore(t, support[t], round(share[t], 2)) for t in ranked]
    runner_up: Optional[str] = ranked[1] if len(ranked) > 1 else None
    runner_up_support = support[runner_up] if runner_up and runner_up not in variants else 0
    result.confidence = pattern_confidence(
        support=consistent,
        total=result.total,
        prior=PRIORS[best],
        runner_up_support=runner_up_support,
        exclusive_support=sum(1 for ex in result.examples
                              if ex["templates"] and set(ex["templates"]) <= {best, *variants}),
    )
    return result
