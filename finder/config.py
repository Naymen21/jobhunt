"""Engine settings, read from environment variables (or a .env file at the project root)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so we don't need python-dotenv. Real env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(PROJECT_ROOT / ".env")


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    # SMTP transport: "disabled" (default; port 25 is blocked on this network),
    # "direct" (connect straight to the MX on port 25), or
    # "socks" (connect through a SOCKS5 proxy, e.g. `ssh -D 1080 user@vps`).
    smtp_transport: str = field(default_factory=lambda: _env("SMTP_TRANSPORT", "disabled"))
    socks_proxy: str = field(default_factory=lambda: _env("SOCKS_PROXY", "127.0.0.1:1080"))
    helo_host: str = field(default_factory=lambda: _env("HELO_HOST", "localhost"))
    # Envelope sender for MAIL FROM. Empty = null sender "<>".
    mail_from: str = field(default_factory=lambda: _env("MAIL_FROM", ""))
    smtp_port: int = field(default_factory=lambda: int(_env("SMTP_PORT", "25")))
    # How many MX hosts to try (in preference order) if the top one won't connect.
    smtp_max_hosts: int = field(default_factory=lambda: int(_env("SMTP_MAX_HOSTS", "2")))

    dns_timeout: float = field(default_factory=lambda: float(_env("DNS_TIMEOUT", "5")))
    smtp_timeout: float = field(default_factory=lambda: float(_env("SMTP_TIMEOUT", "10")))
    # Total network time budget for one verification (DNS + every SMTP connect/command,
    # across all MX hosts and IPv4/IPv6 addresses). Rate-limit waiting doesn't count. 0 = no limit.
    verify_deadline: float = field(default_factory=lambda: float(_env("VERIFY_DEADLINE", "20")))
    # Minimum seconds between SMTP sessions to the same domain.
    smtp_min_interval: float = field(default_factory=lambda: float(_env("SMTP_MIN_INTERVAL", "4")))

    # Crawling / discovery
    crawl_max_pages: int = field(default_factory=lambda: int(_env("CRAWL_MAX_PAGES", "50")))
    crawl_max_pdfs: int = field(default_factory=lambda: int(_env("CRAWL_MAX_PDFS", "10")))
    crawl_delay: float = field(default_factory=lambda: float(_env("CRAWL_DELAY", "1.5")))
    http_timeout: float = field(default_factory=lambda: float(_env("HTTP_TIMEOUT", "15")))
    crawl_max_bytes: int = field(default_factory=lambda: int(_env("CRAWL_MAX_BYTES", str(5 * 1024 * 1024))))
    user_agent: str = field(default_factory=lambda: _env(
        "CRAWLER_USER_AGENT", "JobHuntFinder/0.2 (personal job-search tool; respects robots.txt)"))
    github_token: str = field(default_factory=lambda: _env("GITHUB_TOKEN", ""))
    github_max_repos: int = field(default_factory=lambda: int(_env("GITHUB_MAX_REPOS", "8")))

    mx_cache_ttl: int = field(default_factory=lambda: int(_env("MX_CACHE_TTL", str(24 * 3600))))
    catch_all_cache_ttl: int = field(default_factory=lambda: int(_env("CATCH_ALL_CACHE_TTL", str(7 * 24 * 3600))))
    cache_path: Path = field(default_factory=lambda: Path(_env("FINDER_CACHE", str(DATA_DIR / "finder_cache.db"))))


settings = Settings()
