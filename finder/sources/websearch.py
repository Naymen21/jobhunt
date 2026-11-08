"""Web search for "@domain" mentions, through an official search API only.

Project rule: no paid APIs and never scrape search engine result pages. No free
provider is wired in yet, so this source reports "not configured". To add one,
implement SearchProvider and register it in PROVIDERS.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ..config import Settings
from .base import Source, SourceContext, SourceResult


class SearchProvider(ABC):
    name: str = ""

    @abstractmethod
    def available(self, settings: Settings) -> bool: ...

    @abstractmethod
    def search(self, query: str, settings: Settings, limit: int = 20) -> list[str]:
        """Return result page URLs for a query."""


PROVIDERS: list[SearchProvider] = []  # intentionally empty: paid search APIs are off by project rule


class WebSearchSource(Source):
    name = "websearch"
    label = "Web search"

    def _provider(self, settings: Settings) -> Optional[SearchProvider]:
        return next((p for p in PROVIDERS if p.available(settings)), None)

    def configured(self, settings: Settings) -> tuple[bool, str]:
        if self._provider(settings) is None:
            return False, "Not configured: no free search API is enabled (paid APIs are off by project rule)."
        return True, ""

    def run(self, domain: str, ctx: SourceContext) -> SourceResult:  # pragma: no cover - no provider yet
        provider = self._provider(ctx.settings)
        urls = provider.search(f'"@{domain}"', ctx.settings)
        return SourceResult(self.name, "ok", f"{provider.name}: {len(urls)} result pages (fetching not implemented)", [])
