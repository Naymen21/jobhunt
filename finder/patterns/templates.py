"""Email local-part templates and how common each one is.

PRIORS are rough base rates for company email formats (first.last dominates,
then flast and first). They break ties and score guesses when a domain has no
evidence. They only need to be in the right order of magnitude.
"""
from __future__ import annotations

from typing import Callable

from ..names import ParsedName

# name -> (builder(first, last, middle_initial) -> local part, prior)
_T: dict[str, tuple[Callable[[str, str, str], str], float]] = {
    "first.last":   (lambda f, l, m: f"{f}.{l}", 0.32),
    "flast":        (lambda f, l, m: f"{f[0]}{l}", 0.18),
    "first":        (lambda f, l, m: f, 0.14),
    "firstlast":    (lambda f, l, m: f"{f}{l}", 0.07),
    "f.last":       (lambda f, l, m: f"{f[0]}.{l}", 0.05),
    "firstl":       (lambda f, l, m: f"{f}{l[0]}", 0.04),
    "first_last":   (lambda f, l, m: f"{f}_{l}", 0.03),
    "last.first":   (lambda f, l, m: f"{l}.{f}", 0.02),
    "lastf":        (lambda f, l, m: f"{l}{f[0]}", 0.02),
    "first-last":   (lambda f, l, m: f"{f}-{l}", 0.015),
    "last":         (lambda f, l, m: l, 0.015),
    "first.l":      (lambda f, l, m: f"{f}.{l[0]}", 0.01),
    "f_last":       (lambda f, l, m: f"{f[0]}_{l}", 0.01),
    "lastfirst":    (lambda f, l, m: f"{l}{f}", 0.005),
    "last_first":   (lambda f, l, m: f"{l}_{f}", 0.005),
    "fl":           (lambda f, l, m: f"{f[0]}{l[0]}", 0.005),
    # middle-initial formats only apply when the name has a middle name
    "first.m.last": (lambda f, l, m: f"{f}.{m}.{l}" if m else "", 0.01),
    "fmlast":       (lambda f, l, m: f"{f[0]}{m}{l}" if m else "", 0.01),
}

TEMPLATES: list[str] = list(_T)

# Formats companies fall back to when the main one is already taken (two Daniels -> daniel@, danielh@).
# Examples using them are *consistent* with the main pattern rather than contradicting it.
COLLISION_VARIANTS: dict[str, tuple[str, ...]] = {
    "first": ("firstl",),
    "first.last": ("first.m.last",),
    "flast": ("fmlast",),
    "firstlast": ("fmlast",),
}
PRIORS: dict[str, float] = {name: p for name, (_, p) in _T.items()}
_total = sum(PRIORS.values())
PRIORS = {k: v / _total for k, v in PRIORS.items()}


def render(template: str, name: ParsedName, all_variants: bool = False) -> list[str]:
    """Local part(s) for this template and name. First item is the canonical guess.

    With all_variants, also includes hyphen/particle variants (García-López -> garcialopez,
    garcia-lopez, garcia, lopez) used when *matching* found emails.
    """
    builder, _ = _T[template]
    middle = name.middles[0][0] if name.middles else ""
    firsts = name.first_variants(loose=True) if all_variants else name.first_variants()[:1]
    lasts = name.last_variants() if all_variants else name.last_variants()[:1]
    out: list[str] = []
    for f in firsts:
        for l in lasts:
            if not f or not l:
                continue
            local = builder(f, l, middle)
            if local and local not in out:
                out.append(local)
            if all_variants and template == "firstl":
                # collision fallbacks seen in the wild: danielhe@, chrisleu@
                out += [f + l[:k] for k in (2, 3) if len(l) > k and f + l[:k] not in out]
    return out


def normalize_local(local: str) -> str:
    local = local.lower().split("+", 1)[0]  # drop +tags
    return local.rstrip("0123456789") or local  # jsmith2 -> jsmith


def match_templates(name: ParsedName, local: str) -> list[str]:
    """All templates that would produce this local part for this person."""
    local = normalize_local(local)
    return [t for t in TEMPLATES if local in render(t, name, all_variants=True)]


# How each template reads to a person: "flast" -> "{f}{last}"
_DISPLAY = {
    "first.last": "{first}.{last}", "flast": "{f}{last}", "first": "{first}", "firstlast": "{first}{last}",
    "f.last": "{f}.{last}", "firstl": "{first}{l}", "first_last": "{first}_{last}", "last.first": "{last}.{first}",
    "lastf": "{last}{f}", "first-last": "{first}-{last}", "last": "{last}", "first.l": "{first}.{l}",
    "f_last": "{f}_{last}", "lastfirst": "{last}{first}", "last_first": "{last}_{first}", "fl": "{f}{l}",
    "first.m.last": "{first}.{m}.{last}", "fmlast": "{f}{m}{last}",
}


def display_template(template: str, domain: str = "") -> str:
    shown = _DISPLAY.get(template, template)
    return f"{shown}@{domain}" if domain else shown
