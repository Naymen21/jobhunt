"""Flag local parts that look machine-generated (xkq7zt@, a8f3kd9q@) rather than a person's name.

Several weak signals are combined; one alone (e.g. few vowels in "xzhang") is never enough.
"""
from __future__ import annotations

import re

VOWELS = set("aeiouy")
# Letter pairs that almost never occur in names or words.
RARE_BIGRAMS = {
    "bx", "cj", "cv", "cx", "dx", "fq", "fv", "fx", "fz", "gq", "gv", "gx", "hx", "jb", "jc", "jd", "jf", "jg",
    "jh", "jk", "jl", "jm", "jn", "jp", "jq", "jr", "js", "jt", "jv", "jw", "jx", "jy", "jz", "kq", "kx", "kz",
    "mx", "pq", "pv", "px", "qb", "qc", "qd", "qe", "qf", "qg", "qh", "qj", "qk", "ql", "qm", "qn", "qo", "qp",
    "qr", "qs", "qt", "qv", "qw", "qx", "qy", "qz", "sx", "tq", "vb", "vc", "vd", "vf", "vg", "vh", "vj", "vk",
    "vm", "vn", "vp", "vq", "vt", "vw", "vx", "vz", "wq", "wv", "wx", "xd", "xg", "xj", "xk", "xq", "xr", "xv",
    "xz", "yq", "zb", "zc", "zf", "zg", "zj", "zk", "zq", "zx",
}
_SEPARATORS = re.compile(r"[._\-+]")


def gibberish_signals(local: str) -> list[str]:
    local = local.lower().split("+", 1)[0]
    parts = [p for p in _SEPARATORS.split(local) if p]
    if not parts:
        return []
    signals = []
    letters = "".join(c for c in local if c.isalpha())
    core = max(parts, key=len)
    if len(letters) >= 5:
        vowels = sum(1 for c in letters if c in VOWELS)
        if vowels / len(letters) < 0.2:
            signals.append("almost no vowels")
    if re.search(r"[bcdfghjklmnpqrstvwxz]{5,}", core):
        signals.append("long consonant run")
    # letters and digits interleaved (a8f3kd), as opposed to a trailing number (jdoe2)
    if len(re.findall(r"[a-z]\d|\d[a-z]", core)) >= 2:
        signals.append("letters and digits mixed")
    rare = sum(1 for i in range(len(letters) - 1) if letters[i:i + 2] in RARE_BIGRAMS)
    if rare >= 1 and len(letters) >= 4:
        signals.append("unusual letter pairs")
    if len(core) >= 16 and not any(len(p) <= 12 for p in parts):
        signals.append("very long unbroken string")
    return signals


def _initials_plus_name(letters: str) -> bool:
    """xzhang, sjtbrown: 1-3 initials followed by something that reads like a surname."""
    for k in (1, 2, 3):
        rest = letters[k:]
        if len(rest) < 4:
            break
        vowel_ratio = sum(1 for c in rest if c in VOWELS) / len(rest)
        if (vowel_ratio >= 0.2 and not re.search(r"[bcdfghjklmnpqrstvwxz]{4,}", rest)
                and not any(rest[i:i + 2] in RARE_BIGRAMS for i in range(len(rest) - 1))):
            return True
    return False


def looks_random(local: str) -> tuple[bool, list[str]]:
    signals = gibberish_signals(local)
    core = local.lower().split("+", 1)[0]
    if not re.search(r"\d", core) and len(_SEPARATORS.split(core)) == 1 and _initials_plus_name(core):
        return False, []
    strong = len(re.findall(r"[a-z]\d|\d[a-z]", core)) >= 3  # a8f3kd9q
    return strong or len(signals) >= 2, signals
