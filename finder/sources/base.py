"""Pluggable discovery sources. Each one takes a domain and returns sightings of addresses."""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..config import Settings
from ..models import FoundEmail

Progress = Callable[[dict], None]


@dataclass
class SourceContext:
    settings: Settings
    progress: Progress = lambda event: None
    cancel: threading.Event = field(default_factory=threading.Event)
    options: dict = field(default_factory=dict)  # e.g. {"github_org": "acme", "max_pages": 50}


@dataclass
class SourceResult:
    source: str
    status: str  # "ok", "not_configured", "blocked", "not_found", "error", "cancelled", "skipped"
    message: str = ""
    emails: list[FoundEmail] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


class Source(ABC):
    name: str = ""
    label: str = ""

    def configured(self, settings: Settings) -> tuple[bool, str]:
        """(usable, reason). Unconfigured sources are reported, not run."""
        return True, ""

    @abstractmethod
    def run(self, domain: str, ctx: SourceContext) -> SourceResult: ...

    def safe_run(self, domain: str, ctx: SourceContext) -> SourceResult:
        ok, reason = self.configured(ctx.settings)
        if not ok:
            return SourceResult(self.name, "not_configured", reason)
        try:
            return self.run(domain, ctx)
        except Exception as e:  # one broken source must not sink the whole search
            return SourceResult(self.name, "error", f"{type(e).__name__}: {e}")


def domain_matches(email: str, domain: str) -> bool:
    host = email.rsplit("@", 1)[-1].lower()
    return host == domain or host.endswith("." + domain)
