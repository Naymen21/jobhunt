"""All confidence rules for discovery and Find Email, in one place.

Port 25 is blocked, so confidence comes from evidence (addresses seen in public
sources and the pattern they reveal). Verifier results adjust the score when
they say something definite: no MX, or a real SMTP accept/reject once SMTP is on.
The README's scoring table mirrors this file; keep them in sync.
"""
from __future__ import annotations

from typing import Optional

from .models import VerificationResult

# Find Email: evidence tiers
SEEN_WITH_NAME = 97  # this exact person + address found in a source
SEEN_WITHOUT_NAME = 85  # address found, but no name was attached to it
PATTERN_MATCH_CAP = 90  # candidate built from the detected pattern: pattern confidence, capped
COLLISION_SHARE_FLOOR = 0.4  # a collision-fallback format scores at least 40% of the pattern's score
OTHER_TEMPLATE_CAP = 25  # candidate from a different template when a pattern exists
NO_EVIDENCE_CAP = 35  # domain has no name+email pairs: base rate only

# Verifier adjustments
SMTP_VALID_FLOOR = 95  # mailbox accepted and domain not catch-all
SMTP_INVALID = 5  # mailbox rejected

# Pattern confidence ceiling by number of examples: one example can't prove a format.
_EVIDENCE_CAPS = [(1, 60), (2, 70), (3, 78), (4, 82), (5, 85), (9, 90), (19, 94)]
_EVIDENCE_MAX = 97


def _evidence_cap(total: int) -> int:
    for limit, cap in _EVIDENCE_CAPS:
        if total <= limit:
            return cap
    return _EVIDENCE_MAX


def pattern_confidence(support: int, total: int, prior: float,
                       runner_up_support: int = 0, exclusive_support: Optional[int] = None) -> int:
    """How sure we are that a domain uses its best template.

    agreement = (support + prior) / (total + 1): the share of examples that fit, pulled
    slightly toward the template's base rate so 1-of-1 isn't treated as certainty.
    Then capped by sample size, and reduced when the evidence is ambiguous (examples
    that also fit other templates) or a second format is in real use.
    """
    if total <= 0 or support <= 0:
        return 0
    score = 100 * (support + prior) / (total + 1)
    if exclusive_support is not None and support:
        ambiguity = 1 - exclusive_support / support
        score *= 1 - 0.3 * ambiguity
    if runner_up_support and runner_up_support >= 0.5 * support:
        score -= 5
    return max(0, min(round(score), _evidence_cap(total)))


def candidate_score(template: str, prior: float, pattern_template: Optional[str],
                    pattern_conf: int, pattern_total: int, template_support: int,
                    variant_of_pattern: bool = False, pattern_consistent: int = 0) -> int:
    """Evidence score for a generated (not seen) candidate address."""
    if pattern_template and pattern_total > 0:
        if template == pattern_template:
            return min(PATTERN_MATCH_CAP, pattern_conf)
        if variant_of_pattern and template_support:
            # The company's fallback when the main address is taken: plausible for a common first name.
            share = template_support / max(1, pattern_consistent)
            main = min(PATTERN_MATCH_CAP, pattern_conf)
            return min(main - 5, round(main * max(COLLISION_SHARE_FLOOR, share)))
        # Another template: how much of the data it explains, plus its base rate, kept low.
        return min(OTHER_TEMPLATE_CAP, round(60 * (template_support + prior) / (pattern_total + 1)))
    return min(NO_EVIDENCE_CAP, round(prior * 110))


NAMESAKE_MAIN_FACTOR = 0.6  # main format when another person with this first name already works there
NAMESAKE_VARIANT_FACTOR = 0.75  # collision fallback format in the same situation


def namesake_adjust(score: int, is_main: bool, why: str, namesakes: list[str]) -> tuple[int, str]:
    """Shift weight from the main format to its collision fallback when the first name is shared."""
    shown = ", ".join(namesakes[:3]) + (" ..." if len(namesakes) > 3 else "")
    if is_main:
        return round(score * NAMESAKE_MAIN_FACTOR), f"{why}; may already be taken (same first name: {shown})"
    return max(score, round(PATTERN_MATCH_CAP * NAMESAKE_VARIANT_FACTOR)), f"{why}; likely here since the first name is shared ({shown})"


def apply_verification(score: int, vr: Optional[VerificationResult]) -> tuple[int, Optional[str]]:
    """Adjust an evidence score with what the verifier proved. Returns (score, note)."""
    if vr is None:
        return score, None
    mx = vr.check("mx")
    if mx is not None and mx.passed is False:
        return 0, "domain has no working mail server"
    smtp = vr.check("smtp")
    if smtp is not None and smtp.passed is True and vr.is_catch_all is False:
        return max(score, SMTP_VALID_FLOOR), "mail server confirmed the mailbox"
    if smtp is not None and smtp.passed is False:
        return SMTP_INVALID, "mail server rejected the mailbox"
    if vr.is_catch_all:
        return score, "domain is catch-all, server accepts any address"
    return score, None


def found_email_score(kind: str, has_name: bool) -> int:
    """Confidence for an address seen in a public source (Domain Search list)."""
    return SEEN_WITH_NAME if kind == "personal" and has_name else SEEN_WITHOUT_NAME
