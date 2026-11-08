"""Basic match score, no AI: skill keyword overlap between the default resume and the posting, plus how
well the job fits the saved search. The AI score comes later from a Claude Code command and is stored separately.

score = 70% x (share of the posting's skills that are on your resume, over at least 5; generic skills count 1/4)
        + 30% x (filter fit)
Without a resume, or when no skills are recognised in the posting, only filter fit counts and the score is capped at 50.
Postings open to high school students get +15 (capped at 100)."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .filters import Evaluation

KEYWORD_WEIGHT = 0.7
FIT_WEIGHT = 0.3
NO_KEYWORDS_CAP = 50
MIN_SKILLS = 5
HS_BONUS = 15  # the posting says high school students are welcome
SOFT_WEIGHT = 0.25  # generic skills (communication, teamwork, ...) count a quarter as much
SOFT_SKILLS: set[str] = set()
SKILLS_FILE = Path(__file__).parent / "data" / "skills.txt"


@lru_cache(maxsize=1)
def _skill_patterns() -> list[tuple[str, re.Pattern]]:
    out = []
    for line in SKILLS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        soft = line.startswith("~")
        names = line.lstrip("~").split("|")
        canonical = names[0].lstrip("=")
        if soft:
            SOFT_SKILLS.add(canonical)
        alts = []
        for n in names:
            exact = n.startswith("=")
            body = re.escape(n.lstrip("="))
            alts.append(body if exact else f"(?i:{body})")
        # boundaries that treat + # . / & - as part of a token, so "c" doesn't match "c++" or "R&D"
        pat = re.compile(r"(?<![\w+#./&-])(?:" + "|".join(alts) + r")(?![\w+#&/-]|\.\w)")
        out.append((canonical, pat))
    return out


def skills_in(text: str) -> set[str]:
    text = text or ""
    _skill_patterns()  # also fills SOFT_SKILLS
    return {name for name, pat in _skill_patterns() if pat.search(text)}


@dataclass
class MatchResult:
    score: int
    keyword_share: Optional[float]  # None when it couldn't be computed
    fit: float
    matched: list[str] = field(default_factory=list)  # skills in both
    missing: list[str] = field(default_factory=list)  # posting asks for these, resume doesn't list them
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def match(title: str, description: str, evaluation: Evaluation, resume_skills: Optional[set[str]], flags=()) -> MatchResult:
    result = _match(title, description, evaluation, resume_skills)
    from .student import welcomes_you
    if welcomes_you(flags):  # only for high school students
        result.score = min(100, result.score + HS_BONUS)
        result.notes.insert(0, f"+{HS_BONUS}: open to high school students")
    return result


def _match(title: str, description: str, evaluation: Evaluation, resume_skills: Optional[set[str]]) -> MatchResult:
    fit = evaluation.fit
    notes = list(evaluation.fit_notes)
    wanted = skills_in(f"{title}\n{description}")
    if resume_skills is None:
        notes.insert(0, "no default resume: add one in Settings for keyword matching")
        return MatchResult(min(NO_KEYWORDS_CAP, round(100 * fit)), None, fit, notes=notes)
    if not wanted:
        notes.insert(0, "no skill keywords recognised in this posting")
        return MatchResult(min(NO_KEYWORDS_CAP, round(100 * fit)), None, fit, notes=notes)
    matched = sorted(wanted & resume_skills, key=str.lower)
    missing = sorted(wanted - resume_skills, key=str.lower)
    # Generic skills count a quarter as much, and a posting that names only one or two skills can't prove a strong
    # fit, so the share is taken over at least MIN_SKILLS worth of weight: matching 1 of 1 counts as 1/5, not 100%.
    weight = lambda names: sum(SOFT_WEIGHT if n in SOFT_SKILLS else 1.0 for n in names)  # noqa: E731
    share = round(weight(matched) / max(weight(wanted), MIN_SKILLS), 3)
    score = round(100 * (KEYWORD_WEIGHT * share + FIT_WEIGHT * fit))
    return MatchResult(score, share, fit, matched, missing, notes)
