"""Step 2: free-mail and disposable domain detection."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "data"


@lru_cache(maxsize=None)
def _load(name: str) -> frozenset[str]:
    path = _DATA / name
    if not path.exists():
        return frozenset()
    lines = path.read_text(encoding="utf-8").splitlines()
    return frozenset(l.strip().lower() for l in lines if l.strip() and not l.startswith("#"))


def _matches(domain: str, domains: frozenset[str]) -> bool:
    # Match the domain itself or any parent (e.g. "x.mailinator.com" -> "mailinator.com").
    parts = domain.lower().split(".")
    return any(".".join(parts[i:]) in domains for i in range(len(parts) - 1))


def is_free_provider(domain: str) -> bool:
    return _matches(domain, _load("free_providers.txt"))


def is_disposable(domain: str) -> bool:
    return _matches(domain, _load("disposable_domains.txt"))
