"""Eligibility flags: requirements in a description you might not meet. Jobs are never hidden for these;
each flag is a warning tag with the sentence that triggered it."""
from __future__ import annotations

import re
from typing import Optional

from .models import Flag
from .normalize import sentences

FLAG_LABELS = {
    "hs_ok": "Open to high school students",  # positive: the one flag that helps
    "pitch": "Pitch candidate",  # small company: pitch the founder instead of being filtered out
    "enrolled": "Degree program required",
    "college": "College enrollment required",
    "grad_year": "Grad year",
    "age": "Age minimum",
    "degree": "Degree",
    "work_auth": "Work authorization",
    "citizenship": "Citizenship / clearance",
    "experience": "Experience",
}

_SCHOOL = r"(?:university|college|degree|bachelor|master|undergraduate|graduate|post-?secondary|higher education|institution)"
_ENROLLED = re.compile(
    r"\b(?:currently|actively) (?:enrolled|pursuing|working towards?|attending)\b|\bmust be (?:a )?(?:current(?:ly)?|full-time|enrolled) "
    r"(?:student|enrolled)|\breturning to (?:school|university|college)\b"
    # "enrolled at an accredited college", "enrolled full-time in a four-year university", "enrolled in a degree-granting program"
    r"|\benrolled (?:as an? )?(?:full[- ]time |part[- ]time )?(?:student )?(?:in|at) (?:an?|the|your) (?:accredited |four[- ]year |4[- ]year |"
    r"two[- ]year |2[- ]year |u\.?s\.?[- ]based |degree[- ]granting |recognized )*" + _SCHOOL +
    r"|\b(?:current|currently an?|must be an?|be an?) (?:full[- ]time )?(?:undergraduate|graduate|college|university|degree[- ]seeking|"
    r"post-?secondary) students?\b|\bdegree[- ]seeking\b|\bpursuing (?:an? |your )?(?:\w+ )?(?:bachelor|master|undergraduate|graduate "
    r"degree|degree|b\.?s\.?\b|b\.?a\.?\b|associate)"
    r"|\bstudents? (?:enrolled )?(?:in|at|of|from) (?:an? |the )?(?:accredited )?(?:four[- ]year |4[- ]year )?(?:college|university)"
    r"|\b(?:completed|finished) (?:at least |a minimum of )?(?:one|two|three|1|2|3|\w+) (?:years?|semesters?) (?:of|at) "
    r"(?:college|university|undergraduate|your degree)|\bsophomore,? junior,? (?:or|and) senior\b|\b(?:junior|senior) standing\b"
    r"|\beducation eligibility\b", re.I)
# "Currently enrolled" that names a degree program, a declared major or a bachelor's: PSEO won't satisfy it.
_DEGREE_PROGRAM = re.compile(
    r"\bbachelor|\bmaster['’]?s\b|\bdegree[- ](?:seeking|program|granting)|\bdegree\b|\bdeclared (?:a )?major\b|\bmajor(?:ing)? in\b"
    r"|\bundergraduate (?:degree|program)\b|\bB\.?S\.? (?:or|in)\b|\bpursuing an? (?:\w+ )?(?:bachelor|degree)", re.I)
_DECLARED_MAJOR = re.compile(r"\bdeclared (?:a )?major\b|\bmust be majoring in\b", re.I)
# Postings that welcome high school students. Not "high school diploma or equivalent", which is a minimum, not an invitation.
_HS_OK = re.compile(
    r"\bhigh[- ]school(?:er)?s?\b (?:students?|seniors?|juniors?|interns?|internships?|programs?|graduates? (?:and|or))\b"
    r"|\bhigh[- ]schoolers\b|\bHS (?:students?|seniors?|interns?)\b|\bpre-?college\b|\bsecondary school students?\b"
    r"|\bincoming (?:college )?(?:freshm[ae]n|first[- ]years?)\b|\bgraduating (?:from )?high[- ]school\b"
    r"|\brising (?:high school )?(?:juniors?|seniors?)\b|\brecent high[- ]school grad(?:uate)?s\b"
    r"|\b(?:currently )?(?:in|attending|enrolled in) high[- ]school\b|\bopen to (?:all )?students (?:in|from) high[- ]school\b"
    r"|\b(?:ages?|aged) (?:1[4-7])\s*(?:-|–|to)\s*(?:1[7-9]|2[0-4])\b|\b(?:students?|youth) (?:ages?|aged) 1[4-7]\b"
    r"|\b(?:at least|must be) 1[4-7] years? (?:old|of age)\b|\byouth (?:employment|internship|career) program\b", re.I)
# "Rising seniors" without "high school" usually means college seniors; ignored when the posting
# talks about college or university anywhere.
_AMBIGUOUS_HS = re.compile(r"rising (?!high)", re.I)
_COLLEGE_CONTEXT = re.compile(r"\b(?:college|university|undergraduate|bachelor|degree[- ]granting|accredited)\b", re.I)
# "High School Intern", "Summer Youth Internship" in the title is the clearest sign of all.
_HS_TITLE = re.compile(r"\bhigh[- ]school\b|\bHS intern|\byouth (?:intern|apprentice|program)|\bpre-?college\b", re.I)
_GRAD_YEAR = re.compile(
    r"\b(?:graduat\w*|grad date|class of)\b[^.\n]{0,40}?\b(20[2-4]\d)\b|\b(20[2-4]\d)\s+(?:graduat\w*|grads?)\b", re.I)
_AGE = re.compile(r"\b(?:must be|at least|minimum age(?: of)?|aged?)\s*(?:of\s*)?(18|21)\b|\b(18|21)\s*(?:\+|years of age|years or older|or older)",
                  re.I)
_DEGREE = re.compile(
    # "master" and "associate" also appear in job titles ("Scrum Master in a ...", "Associate Producer in ..."),
    # so they only count with a possessive or right before "degree".
    r"\b(bachelor['’]?s?|master['’]s|masters|master(?= degree)|ph\.?\s?d|doctorate|associate['’]s|associate(?= degree)"
    r"|b\.?\s?s\.?|b\.?\s?a\.?|m\.?\s?s\.?|bsc|msc|mba)\b[^.\n]{0,25}?\b(?:degree|in\b)"
    r"|\b(?:degree|diploma) (?:in|required)\b", re.I)
_DEGREE_SOFT = re.compile(r"\bpreferred\b|\bor equivalent\b|\bequivalent (?:practical |work )?experience\b|\bnice to have\b|\bplus\b|\bbonus\b",
                          re.I)
_WORK_AUTH = re.compile(
    r"\bauthori[sz]ed to work\b|\bwork authori[sz]ation\b|\bright to work\b|\bvisa sponsorship\b|\bsponsor(?:ship)? "
    r"(?:a |an |any )?(?:visa|work)|\bwithout (?:current or future |the need for )?(?:visa )?sponsorship\b"
    r"|\bunable to sponsor\b|\bnot (?:able to )?(?:provide )?sponsor", re.I)
_CITIZEN = re.compile(r"\bu\.?s\.? citizen(?:ship)?\b|\bcitizenship\b|\bsecurity clearance\b|\bclearance (?:is )?required\b"
                      r"|\b(?:active|secret|top secret) clearance\b|\bgreen card\b|\bpermanent resident\b|\bITAR\b", re.I)
# Equal-opportunity boilerplate lists "citizenship" among things they DON'T discriminate on: not a requirement.
_EEO = re.compile(r"equal (?:employment )?opportunit|regardless of|without regard|discriminat|protected (?:class|status)", re.I)
# "a local college or MBA student", "open to college students": needs current college enrollment.
_COLLEGE_STUDENT = re.compile(r"\bcollege (?:or \w+ )?students?\b|\buniversity students?\b", re.I)
_YEARS = re.compile(r"\b(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)?\s*(?:\d{1,2}\s*)?\+?\s*years?\b(?:'s?)?(?: of)?(?: [\w/+-]+){0,4}? experience",
                    re.I)


def _snip(s: str, limit: int = 240) -> str:
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _degree_name(word) -> str:
    k = re.sub(r"[.'\s]", "", (word or "").lower())
    if k.startswith("bachelor") or k in ("bs", "ba", "bsc"):
        return "Bachelor's"
    if k.startswith("master") or k in ("ms", "msc"):
        return "Master's"
    if k.startswith("associate"):
        return "Associate's"
    return {"phd": "PhD", "doctorate": "PhD", "mba": "MBA"}.get(k, "Degree")


_TITLE_DEGREE = re.compile(r"\bph\.?\s?d\b|\bdoctoral\b|\bmba\b|\bmaster['’]?s\b|\bgraduate (?:student|intern|research)", re.I)


def find_flags(text: str, min_years: int = 2, title: str = "") -> list[Flag]:
    """One flag per kind, from the first sentence that matches. `experience` flags only at >= `min_years` years.
    A degree named in the job title ("PhD Intern", "MBA Intern") counts as required, whatever the text says."""
    found: dict[str, Flag] = {}
    if title and _HS_TITLE.search(title):
        found["hs_ok"] = Flag("hs_ok", FLAG_LABELS["hs_ok"], _snip(f"Job title: {title}"))
    if title and (m := _TITLE_DEGREE.search(title)):
        found["degree"] = Flag("degree", f"{_degree_name(m.group(0))} required", _snip(f"Job title: {title}"))
    years_seen = 0
    college_context = bool(_COLLEGE_CONTEXT.search(text or ""))
    for s in sentences(text):
        if "hs_ok" not in found and (m := _HS_OK.search(s)) and not re.search(r"diploma|\bGED\b|equivalent", s, re.I) \
                and not (_AMBIGUOUS_HS.match(m.group(0)) and college_context):
            found["hs_ok"] = Flag("hs_ok", FLAG_LABELS["hs_ok"], _snip(s))
        if "enrolled" not in found and (_DECLARED_MAJOR.search(s) or (_ENROLLED.search(s) and _DEGREE_PROGRAM.search(s))):
            found["enrolled"] = Flag("enrolled", FLAG_LABELS["enrolled"], _snip(s))
        elif "college" not in found and "enrolled" not in found and (_ENROLLED.search(s) or _COLLEGE_STUDENT.search(s)):
            found["college"] = Flag("college", FLAG_LABELS["college"], _snip(s))
        if "grad_year" not in found and (m := _GRAD_YEAR.search(s)):
            year = m.group(1) or m.group(2)
            found["grad_year"] = Flag("grad_year", f"Grad year {year}", _snip(s))
        if "age" not in found and (m := _AGE.search(s)):
            found["age"] = Flag("age", f"Must be {m.group(1) or m.group(2)}+", _snip(s))
        if "degree" not in found and (m := _DEGREE.search(s)):  # (a degree in the title was set above)
            label = f"{_degree_name(m.group(1))} {'preferred' if _DEGREE_SOFT.search(s) else 'required'}"
            found["degree"] = Flag("degree", label, _snip(s))
        if "work_auth" not in found and _WORK_AUTH.search(s):
            found["work_auth"] = Flag("work_auth", FLAG_LABELS["work_auth"], _snip(s))
        if "citizenship" not in found and _CITIZEN.search(s) and not _EEO.search(s):
            found["citizenship"] = Flag("citizenship", FLAG_LABELS["citizenship"], _snip(s))
        for m in _YEARS.finditer(s):
            n = int(m.group(1))
            if min_years <= n <= 25 and n > years_seen:
                years_seen = n
                found["experience"] = Flag("experience", f"{n}+ yrs experience", _snip(s))
    if "enrolled" in found:
        found.pop("college", None)
    order = list(FLAG_LABELS)
    return sorted(found.values(), key=lambda f: order.index(f.key))


POSITIVE = {"hs_ok", "pitch"}


def blocks_high_schooler(flag) -> bool:
    """Flags that rule YOU out, for the student level set in Settings > Profile (see jobsearch.student.blocks).
    The name is historical: for a high school student it's current college enrollment, a degree program / declared
    major, a college grad year, or a required degree."""
    from .student import blocks
    return blocks(flag)


def open_to_high_school(flags) -> bool:
    """The posting itself says high school students (or incoming freshmen) can apply, and you're in high school."""
    from .student import welcomes_you
    return welcomes_you(flags)


def eligible_for_high_schooler(flags) -> bool:
    """For the "Eligible for me" filter, at your student level. A posting that welcomes your level always stays."""
    from .student import eligible
    return eligible(flags)


def add_pitch(flags: list, employees: Optional[int], small_max: int = 100) -> list:
    """At a company of <= ~100 people, a posting you don't quite qualify for isn't a dead end: the founder or CTO
    can make an exception. Tag it "Pitch candidate" instead of letting it be filtered out."""
    flags = [f for f in flags if (f["key"] if isinstance(f, dict) else f.key) != "pitch"]
    if employees is None or employees > small_max or not any(blocks_high_schooler(f) for f in flags):
        return flags
    pitch = Flag("pitch", FLAG_LABELS["pitch"], f"Small company (about {employees} people): pitch the founder or CTO directly.")
    return [pitch] + flags


def is_pitch(flags) -> bool:
    return any((f["key"] if isinstance(f, dict) else f.key) == "pitch" for f in flags)
