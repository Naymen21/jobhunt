from .base import Source, SourceContext, SourceResult
from .github import GitHubSource
from .website import WebsiteSource
from .websearch import WebSearchSource


def default_sources() -> list[Source]:
    """Order matters only for progress display; results are merged afterwards."""
    return [WebsiteSource(), GitHubSource(), WebSearchSource()]


__all__ = ["Source", "SourceContext", "SourceResult", "WebsiteSource", "GitHubSource", "WebSearchSource", "default_sources"]
