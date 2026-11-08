"""Step 1: email syntax check.

Deliberately stricter than RFC 5322: we only care about addresses a real
company would hand out, so quoted local parts, comments and IP literals are rejected.
"""
from __future__ import annotations

import re
from typing import Optional

_LOCAL_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*$")
_LABEL_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")


def normalize(email: str) -> str:
    return email.strip().strip("<>").strip()


def check_syntax(email: str) -> tuple[bool, str, Optional[str], Optional[str]]:
    """Return (ok, detail, local_part, domain). Domain is lowercased and IDNA-encoded."""
    if not email:
        return False, "empty address", None, None
    if len(email) > 254:
        return False, "longer than 254 characters", None, None
    if email.count("@") != 1:
        return False, "must contain exactly one @", None, None
    local, domain = email.rsplit("@", 1)
    if not local or len(local) > 64:
        return False, "local part empty or longer than 64 characters", None, None
    if not _LOCAL_RE.match(local):
        return False, f"invalid characters or dots in local part '{local}'", None, None
    try:
        domain = domain.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        return False, f"invalid domain '{domain}'", None, None
    labels = domain.split(".")
    if len(labels) < 2 or not all(_LABEL_RE.match(label) for label in labels):
        return False, f"invalid domain '{domain}'", None, None
    if labels[-1].isdigit():
        return False, "top-level domain cannot be numeric", None, None
    return True, "valid format", local, domain
