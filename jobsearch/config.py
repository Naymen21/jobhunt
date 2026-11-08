"""Job search settings, read from environment variables (or the project's .env, loaded by finder.config)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from finder.config import DATA_DIR, PROJECT_ROOT  # noqa: F401  (importing finder.config loads .env)

RESUME_DIR = DATA_DIR / "resumes"

# The one Apify actor this project is allowed to use (approved by the owner in Phase 3).
# It scrapes LinkedIn's logged-out public job search page; its input has no login, cookie or session field.
APIFY_ACTOR = "curious_coder/linkedin-jobs-scraper"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class JobSettings:
    apify_token: str = field(default_factory=lambda: _env("APIFY_TOKEN", ""))
    apify_api: str = "https://api.apify.com/v2"
    # Results per Apify run. The UI and saved searches can lower it, never raise it above the ceiling.
    apify_max_results: int = field(default_factory=lambda: int(_env("APIFY_MAX_RESULTS", "25")))
    apify_max_results_ceiling: int = 50
    # Refuse a run if this month's usage plus the run's worst case would pass this share of the monthly limit.
    apify_budget_fraction: float = field(default_factory=lambda: float(_env("APIFY_BUDGET_FRACTION", "0.8")))
    # Used only if Apify's limits endpoint doesn't report a monthly limit. The free plan is $5/month.
    apify_free_credit_usd: float = field(default_factory=lambda: float(_env("APIFY_FREE_CREDIT_USD", "5")))
    # Actor prices on Apify's free plan (pay per event): per dataset item and per actor start.
    apify_price_per_result: float = 0.002
    apify_price_per_start: float = 0.00005
    # The hard per-run spending cap sent to Apify is the estimate times this.
    apify_cap_margin: float = 1.2
    apify_timeout: int = 300

    feed_delay: float = field(default_factory=lambda: float(_env("FEED_DELAY", "0.5")))
    http_timeout: float = field(default_factory=lambda: float(_env("HTTP_TIMEOUT", "20")))
    user_agent: str = field(default_factory=lambda: _env(
        "FEED_USER_AGENT", "JobHunt/0.3 (personal job-search tool; reads public job board feeds)"))


settings = JobSettings()
