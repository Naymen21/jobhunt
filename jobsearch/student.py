"""Who is using jobhunt: student level, graduation year and home area. Everything that depends on "can I apply to this?"
reads it from here: eligibility flags, the "Who can apply" filter, the pitch-candidate tag, the high school bonus, the
Local tag, and the outreach rules. The web app sets it from Settings > Profile (`set_student`); the engine never reads
storage. The default is a high school student with no home area, so nothing is tagged Local until you choose one."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Optional

LEVELS = {
    "high_school": "High school student",
    "college": "College student (undergraduate)",
    "community_college": "Community college student",
    "grad_school": "Graduate student (master's / PhD)",
    "recent_grad": "Recent graduate",
}

US_STATES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california", "CO": "colorado",
    "CT": "connecticut", "DE": "delaware", "DC": "district of columbia", "FL": "florida", "GA": "georgia", "HI": "hawaii",
    "ID": "idaho", "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky", "LA": "louisiana",
    "ME": "maine", "MD": "maryland", "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi",
    "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada", "NH": "new hampshire", "NJ": "new jersey",
    "NM": "new mexico", "NY": "new york", "NC": "north carolina", "ND": "north dakota", "OH": "ohio", "OK": "oklahoma",
    "OR": "oregon", "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina", "SD": "south dakota",
    "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont", "VA": "virginia", "WA": "washington",
    "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming",
}

# Ready-made city lists for the Local tag; you can always type your own.
AREA_PRESETS = {
    "Twin Cities, MN": ("MN", "minneapolis, st paul, st. paul, saint paul, bloomington, edina, eden prairie, minnetonka, "
                              "plymouth, maple grove, hopkins, st louis park, st. louis park, saint louis park, "
                              "roseville, eagan, burnsville, golden valley, richfield, brooklyn park, woodbury, shakopee, "
                              "chanhassen, medina, arden hills, lakeville, apple valley, fridley, mendota heights, "
                              "twin cities, minnetrista, excelsior, shoreview, new hope, crystal, chaska, coon rapids, blaine, "
                              "maplewood, inver grove heights, west st paul, savage, prior lake, rogers, orono"),
    "San Francisco Bay Area, CA": ("CA", "san francisco, oakland, berkeley, san jose, palo alto, mountain view, sunnyvale, "
                                         "santa clara, menlo park, redwood city, san mateo, cupertino, fremont, bay area"),
    "New York City, NY": ("NY", "new york, nyc, manhattan, brooklyn, queens, bronx, staten island, jersey city, hoboken"),
    "Seattle, WA": ("WA", "seattle, bellevue, redmond, kirkland, bothell, tacoma, everett"),
    "Boston, MA": ("MA", "boston, cambridge, somerville, waltham, newton, brookline, quincy"),
    "Austin, TX": ("TX", "austin, round rock, cedar park, pflugerville, georgetown"),
    "Chicago, IL": ("IL", "chicago, evanston, oak park, naperville, schaumburg"),
    "Los Angeles, CA": ("CA", "los angeles, santa monica, culver city, pasadena, burbank, irvine, long beach, venice"),
    "Denver / Boulder, CO": ("CO", "denver, boulder, aurora, lakewood, englewood, broomfield, golden"),
    "Atlanta, GA": ("GA", "atlanta, alpharetta, marietta, decatur, sandy springs"),
}


@dataclass
class Student:
    level: str = "high_school"
    grad_year: Optional[int] = None  # high school grad year for high schoolers; degree grad year otherwise
    home_state: str = ""  # two letters, e.g. "MN"
    home_cities: list[str] = field(default_factory=list)

    @property
    def high_school(self) -> bool:
        return self.level == "high_school"

    @property
    def label(self) -> str:
        return LEVELS.get(self.level, LEVELS["high_school"])

    @property
    def area_label(self) -> str:
        if not self.home_state and not self.home_cities:
            return ""
        for name, (st, cities) in AREA_PRESETS.items():
            if st == self.home_state and set(_cities(cities)) == set(self.home_cities):
                return name
        return US_STATES.get(self.home_state, self.home_state).title() or ", ".join(self.home_cities[:2]).title()


_current = Student()


def current() -> Student:
    return _current


def set_student(level: str = "high_school", grad_year=None, home_state: str = "", home_cities="") -> Student:
    """Called by the app from the saved profile. Unknown values fall back to safe defaults."""
    global _current
    year = None
    m = re.search(r"20\d\d", str(grad_year or ""))
    if m:
        year = int(m.group(0))
    state = (home_state or "").strip().upper()
    _current = Student(level if level in LEVELS else "high_school", year, state if state in US_STATES else "",
                       _cities(home_cities) if isinstance(home_cities, str) else [c.lower() for c in home_cities])
    return _current


def _cities(text: str) -> list[str]:
    return [c.strip().lower() for c in re.split(r"[,;\n]", text or "") if c.strip()]


def preset(name: str) -> tuple[str, str]:
    return AREA_PRESETS.get(name, ("", ""))


# --- home area -------------------------------------------------------------------------------------------------------

def is_local(location: str, who: Optional[Student] = None) -> bool:
    who = who or _current
    loc = (location or "").lower()
    if not loc or not (who.home_state or who.home_cities):
        return False
    if who.home_state:
        st = who.home_state.lower()
        name = US_STATES.get(who.home_state, "")
        if (name and re.search(rf"\b{re.escape(name)}\b", loc)) or re.search(rf",\s*{st}\b|\b{st}\s*(?:\d{{5}})?$", loc):
            return True
    return any(re.search(r"(?<![a-z])" + re.escape(city) + r"(?![a-z])", loc) for city in who.home_cities)


# --- eligibility -----------------------------------------------------------------------------------------------------

def _key_label(flag) -> tuple[str, str]:
    return (flag["key"], flag["label"]) if isinstance(flag, dict) else (flag.key, flag.label)


def _grad_year_mismatch(label: str, who: Student) -> bool:
    """A posting's 'Grad year 2027' rules you out only if you know your own year and it's clearly different.
    Postings usually accept a window, so within a year counts as a match."""
    m = re.search(r"20\d\d", label)
    if not m or who.grad_year is None:
        return False
    return abs(int(m.group(0)) - who.grad_year) > 1


def blocks(flag, who: Optional[Student] = None) -> bool:
    """Flags that rule you out, for your level.
    High school: college enrollment, a degree program or declared major, a college grad year, a required degree.
    College / community college: a required master's/PhD/MBA, or a grad year well away from yours.
    Graduate student: a required PhD (for master's students it's usually a real bar) or a far-off grad year.
    Recent graduate: current-enrollment requirements (you've finished) and a far-off grad year."""
    who = who or _current
    key, label = _key_label(flag)
    required = label.endswith("required")
    if who.high_school:
        return key in ("enrolled", "college", "grad_year") or (key == "degree" and required)
    if key == "grad_year":
        return _grad_year_mismatch(label, who)
    if who.level in ("college", "community_college"):
        return key == "degree" and required and label.split(" ")[0] in ("Master's", "PhD", "MBA")
    if who.level == "grad_school":
        return key == "degree" and required and label.startswith("PhD")
    if who.level == "recent_grad":
        return key in ("enrolled", "college") or (key == "degree" and required and label.split(" ")[0] in ("PhD", "MBA"))
    return False


def welcomes_you(flags, who: Optional[Student] = None) -> bool:
    """The posting itself invites your level. Only high school has a dedicated positive flag ("hs_ok")."""
    who = who or _current
    return who.high_school and any(_key_label(f)[0] == "hs_ok" for f in flags)


def eligible(flags, who: Optional[Student] = None) -> bool:
    who = who or _current
    keys = {_key_label(f)[0] for f in flags}
    return welcomes_you(flags, who) or "pitch" in keys or not any(blocks(f, who) for f in flags)


def class_year(who: Optional[Student] = None, today: Optional[date] = None) -> str:
    """'senior', 'junior'... from the grad year (school years end in spring), for prompts and labels."""
    who = who or _current
    if who.grad_year is None or who.level in ("recent_grad", "grad_school"):
        return ""
    today = today or date.today()
    this_year_ends = today.year + (1 if today.month >= 7 else 0)  # the spring the current school year ends
    left = who.grad_year - this_year_ends  # 0 = graduating this school year
    if who.level == "community_college":
        return {0: "second-year", 1: "first-year"}.get(left, "")
    return {0: "senior", 1: "junior", 2: "sophomore", 3: "freshman"}.get(left, "")


def with_level(level: str, who: Optional[Student] = None) -> Student:
    return replace(who or _current, level=level)
