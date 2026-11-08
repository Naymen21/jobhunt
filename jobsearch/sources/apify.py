"""LinkedIn jobs through Apify, using only the approved public-listings actor (see config.APIFY_ACTOR).

No LinkedIn login, password or cookies are used or stored: the actor reads LinkedIn's logged-out public job
search and its input has no credential fields. The only secret is APIFY_TOKEN from .env, sent in the
Authorization header (never in a URL).

Budget guard, before every run:
  1. read this month's usage and limit from Apify (GET /users/me/limits). If that fails, don't run;
  2. estimate the worst case for this run (max results x price per result + start fee);
  3. refuse if used + worst case would pass `apify_budget_fraction` (80%) of the monthly limit;
  4. pass a hard cap (maxTotalChargeUsd = worst case x 1.2) and maxItems, so Apify itself stops the run there.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from ..config import APIFY_ACTOR, JobSettings
from ..models import Job, Pay
from ..normalize import detect_job_type, detect_work_mode, html_to_text, map_job_type, map_work_mode, parse_pay, to_iso
from .base import FeedClient, FeedError


@dataclass
class Usage:
    used_usd: float
    limit_usd: float
    cycle_start: Optional[str] = None
    cycle_end: Optional[str] = None
    limit_reported: bool = True  # False when Apify didn't report a limit and the free-plan default was assumed

    @property
    def share(self) -> float:
        return self.used_usd / self.limit_usd if self.limit_usd else 1.0


@dataclass
class BudgetCheck:
    ok: bool
    reason: str
    estimate_usd: float
    cap_usd: float
    max_results: int
    usage: Optional[Usage] = None


@dataclass
class ApifyQuery:
    keywords: str
    location: str = ""
    posted_within_days: Optional[int] = None
    max_results: int = 25


@dataclass
class ApifyRun:
    jobs: list[Job] = field(default_factory=list)
    items: int = 0
    estimate_usd: float = 0.0  # what these results cost by the actor's price list


def date_window(days: Optional[int]) -> str:
    if not days:
        return "anyTime"
    return "past24Hours" if days <= 1 else "pastWeek" if days <= 7 else "pastMonth" if days <= 31 else "anyTime"


class ApifySource:
    name, label = "apify", "LinkedIn (Apify)"

    def __init__(self, settings: JobSettings):
        self.settings = settings

    @property
    def configured(self) -> bool:
        return bool(self.settings.apify_token)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.settings.apify_token}"}

    def clamp(self, max_results: Optional[int]) -> int:
        n = max_results or self.settings.apify_max_results
        return max(1, min(int(n), self.settings.apify_max_results_ceiling))

    def estimate(self, max_results: int) -> float:
        return round(max_results * self.settings.apify_price_per_result + self.settings.apify_price_per_start, 5)

    # -- usage ------------------------------------------------------------------------------
    def usage(self, client: FeedClient) -> Usage:
        if not self.configured:
            raise FeedError("not configured")
        resp = client.request("GET", f"{self.settings.apify_api}/users/me/limits", headers=self._headers())
        if resp.status_code == 401:
            raise FeedError("Apify rejected the token (401)")
        if resp.status_code >= 400:
            raise FeedError(f"Apify usage check failed (HTTP {resp.status_code})")
        data = (resp.json() or {}).get("data") or {}
        limit = (data.get("limits") or {}).get("maxMonthlyUsageUsd")
        used = (data.get("current") or {}).get("monthlyUsageUsd")
        if used is None:
            raise FeedError("Apify didn't report this month's usage")
        cycle = data.get("monthlyUsageCycle") or {}
        return Usage(float(used), float(limit) if limit else self.settings.apify_free_credit_usd,
                     cycle.get("startAt"), cycle.get("endAt"), limit_reported=bool(limit))

    @staticmethod
    def daily_budget(usage: "Usage", fraction: float, spent_today: float, today: Optional[date] = None) -> float:
        """(fraction x monthly limit - usage before today) / days left in the billing month, today included."""
        today = today or date.today()
        end = None
        if usage.cycle_end:
            try:
                end = datetime.fromisoformat(usage.cycle_end.replace("Z", "+00:00")).astimezone().date()
            except ValueError:
                end = None
        if end is None or end < today:
            nxt = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
            end = nxt - timedelta(days=1)
        days_left = max(1, (end - today).days + 1)
        room = usage.limit_usd * fraction - max(0.0, usage.used_usd - spent_today)
        return max(0.0, room / days_left)

    def check_budget(self, max_results: Optional[int], client: FeedClient, spent_today: float = 0.0) -> BudgetCheck:
        n = self.clamp(max_results)
        est = self.estimate(n)
        cap = round(est * self.settings.apify_cap_margin, 4)
        if not self.configured:
            return BudgetCheck(False, "not configured: add APIFY_TOKEN to .env", est, cap, n)
        try:
            usage = self.usage(client)
        except FeedError as e:
            return BudgetCheck(False, f"couldn't check Apify usage, so not running to be safe ({e})", est, cap, n)
        ceiling = usage.limit_usd * self.settings.apify_budget_fraction
        daily = self.daily_budget(usage, self.settings.apify_budget_fraction, spent_today)
        if usage.used_usd + est > ceiling:
            return BudgetCheck(False, f"would pass {self.settings.apify_budget_fraction:.0%} of the monthly credit: "
                                      f"${usage.used_usd:.2f} used + up to ${est:.2f} for this run > ${ceiling:.2f}",
                               est, cap, n, usage)
        if spent_today + est > daily:
            return BudgetCheck(False, f"today's LinkedIn budget is used up (${spent_today:.2f} of ${daily:.2f} spent today; "
                                      f"it resets tomorrow)", est, cap, n, usage)
        return BudgetCheck(True, f"${usage.used_usd:.2f} of ${usage.limit_usd:.2f} used; today's budget ${daily:.2f}, "
                                 f"this run costs at most ${cap:.2f}", est, cap, n, usage)

    # -- run --------------------------------------------------------------------------------
    def search(self, query: ApifyQuery, client: FeedClient, check: BudgetCheck) -> ApifyRun:
        """Run the actor. `check` must be a passing BudgetCheck from just before."""
        if not check.ok:
            raise FeedError(check.reason)
        n = check.max_results
        payload = {"keywords": query.keywords, "datePosted": date_window(query.posted_within_days),
                   "limitPerSource": n, "scrapeCompany": False}
        if query.location:
            payload["location"] = query.location
        actor = APIFY_ACTOR.replace("/", "~")
        resp = client.request(
            "POST", f"{self.settings.apify_api}/acts/{actor}/run-sync-get-dataset-items",
            params={"maxItems": n, "maxTotalChargeUsd": f"{check.cap_usd:.4f}", "timeout": self.settings.apify_timeout},
            json=payload, headers=self._headers(), timeout=self.settings.apify_timeout + 30)
        if resp.status_code >= 400:
            raise FeedError(f"Apify run failed (HTTP {resp.status_code})")
        items = resp.json()
        if not isinstance(items, list):
            raise FeedError("unexpected Apify response")
        items = items[:n]
        return ApifyRun([self.to_job(i) for i in items if isinstance(i, dict)], len(items), self.estimate(len(items)))

    def to_job(self, i: dict) -> Job:
        title = i.get("title") or i.get("jobTitle") or ""
        company = i.get("companyName") or i.get("company") or ""
        location = i.get("location") or ""
        description = i.get("descriptionText") or html_to_text(i.get("descriptionHtml") or i.get("description") or "")
        modes = i.get("workplaceTypes") or i.get("workplaceType") or i.get("workType") or []
        if isinstance(modes, str):
            modes = [modes]
        mode = next((m for m in (map_work_mode(x) for x in modes) if m), None)
        salary = i.get("salaryInfo") or i.get("salary") or ""
        pay: Optional[Pay] = None
        if isinstance(salary, list):  # usually [low, high], e.g. ["$28.00/hr", "$34.00/hr"]
            parts = [parse_pay(str(s)) for s in salary if s]
            parts = [p for p in parts if p]
            if parts:
                pay = parts[0]
                if len(parts) > 1 and parts[-1].period == pay.period:
                    pay.max, pay.text = parts[-1].min, " - ".join(str(s) for s in salary if s)
        elif salary:
            pay = parse_pay(str(salary))
        url = i.get("link") or i.get("jobUrl") or i.get("url") or ""
        dept = i.get("jobFunction") or i.get("industries") or ""
        if isinstance(dept, list):
            dept = ", ".join(str(d) for d in dept)
        return Job(
            source=self.name, source_id=str(i.get("id") or i.get("jobId") or url), company=company, title=title,
            url=url, apply_url=i.get("applyUrl") or url, location=location,
            work_mode=mode or detect_work_mode(location, title, description),
            job_type=detect_job_type(title, i.get("employmentType")) or map_job_type(i.get("employmentType")),
            pay=pay or parse_pay(description),
            posted_at=to_iso(i.get("postedAt") or i.get("publishedAt") or i.get("postedDate")),
            description=description,
            department=str(dept),
        )
