"""Greenhouse, Lever and Ashby public job board feeds: official, free, no account needed.

- Greenhouse: https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true
- Lever:      https://api.lever.co/v0/postings/{board}?mode=json
- Ashby:      https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true
One request per company per run."""
from __future__ import annotations

from ..models import Job, Pay, WatchCompany
from ..normalize import (detect_job_type, detect_work_mode, html_to_text, map_job_type, map_work_mode, parse_pay,
                         to_iso)
from .base import FeedClient, FeedError, FeedSource, Probe


class GreenhouseSource(FeedSource):
    name, label = "greenhouse", "Greenhouse"
    api = "https://boards-api.greenhouse.io/v1/boards"

    def board_url(self, board: str) -> str:
        return f"https://job-boards.greenhouse.io/{board}"

    def fetch(self, company: WatchCompany, client: FeedClient) -> list[Job]:
        data = client.get_json(f"{self.api}/{company.board}/jobs", params={"content": "true"})
        if not isinstance(data, dict) or "jobs" not in data:
            raise FeedError("unexpected response shape")
        return [self.to_job(j, company) for j in data["jobs"]]

    def to_job(self, j: dict, company: WatchCompany) -> Job:
        title = j.get("title") or ""
        location = (j.get("location") or {}).get("name") or ""
        description = html_to_text(j.get("content") or "")
        # Pay is often in custom fields ("Location Range 1: Base salary range between $X - $Y") rather than the text.
        meta_text = "\n".join(str(m.get("value")) for m in j.get("metadata") or []
                              if isinstance(m.get("value"), str) and "$" in m.get("value", ""))
        explicit_type = next((str(m.get("value")) for m in j.get("metadata") or []
                              if "employment" in (m.get("name") or "").lower() and m.get("value")), None)
        explicit_mode = next((str(m.get("value")) for m in j.get("metadata") or []
                              if any(w in (m.get("name") or "").lower() for w in ("workplace", "remote", "work type"))
                              and isinstance(m.get("value"), str)), None)
        return Job(
            source=self.name, source_id=str(j.get("id")), company=j.get("company_name") or company.name, title=title,
            url=j.get("absolute_url") or "", apply_url=j.get("absolute_url") or "", location=location,
            work_mode=map_work_mode(explicit_mode) or detect_work_mode(location, title, description),
            job_type=detect_job_type(title, explicit_type),
            pay=parse_pay(meta_text) or parse_pay(description),
            posted_at=to_iso(j.get("first_published") or j.get("updated_at")),
            description=description,
            department=", ".join(d.get("name", "") for d in j.get("departments") or [] if d.get("name")),
        )

    def probe(self, board: str, client: FeedClient) -> Probe:
        try:
            data = client.get_json(f"{self.api}/{board}/jobs")  # without descriptions: much smaller
            count = len(data.get("jobs") or [])
        except (FeedError, AttributeError) as e:
            return Probe(False, message=str(e))
        p = Probe(True, count, message=f"{count} open jobs")
        try:
            p.company_name = client.get_json(f"{self.api}/{board}").get("name") or ""
        except FeedError:
            pass
        return p


class LeverSource(FeedSource):
    name, label = "lever", "Lever"
    api = "https://api.lever.co/v0/postings"

    def board_url(self, board: str) -> str:
        return f"https://jobs.lever.co/{board}"

    def fetch(self, company: WatchCompany, client: FeedClient) -> list[Job]:
        data = client.get_json(f"{self.api}/{company.board}", params={"mode": "json"})
        if not isinstance(data, list):
            raise FeedError("unexpected response shape")
        return [self.to_job(j, company) for j in data]

    def to_job(self, j: dict, company: WatchCompany) -> Job:
        cats = j.get("categories") or {}
        title = j.get("text") or ""
        location = cats.get("location") or ", ".join(cats.get("allLocations") or [])
        parts = [j.get("descriptionPlain") or html_to_text(j.get("description") or "")]
        for block in j.get("lists") or []:
            parts.append(block.get("text", "") + "\n" + html_to_text(block.get("content", "")))
        parts.append(j.get("additionalPlain") or html_to_text(j.get("additional") or ""))
        description = "\n\n".join(p.strip() for p in parts if p and p.strip())
        pay = None
        sr = j.get("salaryRange") or {}
        if sr.get("min") or sr.get("max"):
            interval = (sr.get("interval") or "").lower()
            period = "hour" if "hour" in interval else "month" if "month" in interval else "year"
            pay = Pay(sr.get("min"), sr.get("max"), sr.get("currency") or "USD", period, j.get("salaryDescriptionPlain") or "")
        return Job(
            source=self.name, source_id=str(j.get("id")), company=company.name, title=title,
            url=j.get("hostedUrl") or "", apply_url=j.get("applyUrl") or j.get("hostedUrl") or "", location=location,
            work_mode=map_work_mode(j.get("workplaceType")) or detect_work_mode(location, title, description),
            job_type=detect_job_type(title, cats.get("commitment")),
            pay=pay or parse_pay(description),
            posted_at=to_iso(j.get("createdAt")),
            description=description,
            department=cats.get("department") or cats.get("team") or "",
        )


class AshbySource(FeedSource):
    name, label = "ashby", "Ashby"
    api = "https://api.ashbyhq.com/posting-api/job-board"

    def board_url(self, board: str) -> str:
        return f"https://jobs.ashbyhq.com/{board}"

    def fetch(self, company: WatchCompany, client: FeedClient) -> list[Job]:
        data = client.get_json(f"{self.api}/{company.board}", params={"includeCompensation": "true"})
        if not isinstance(data, dict) or "jobs" not in data:
            raise FeedError("unexpected response shape")
        return [self.to_job(j, company) for j in data["jobs"] if j.get("isListed", True)]

    def to_job(self, j: dict, company: WatchCompany) -> Job:
        title = j.get("title") or ""
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        location = "; ".join(l for l in locs if l)
        description = j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml") or "")
        pay = None
        comp = j.get("compensation") or {}
        for c in comp.get("summaryComponents") or []:
            if c.get("compensationType") == "Salary" and (c.get("minValue") or c.get("maxValue")):
                interval = (c.get("interval") or "").upper()
                period = "hour" if "HOUR" in interval else "month" if "MONTH" in interval else "year"
                pay = Pay(c.get("minValue"), c.get("maxValue"), c.get("currencyCode") or "USD", period,
                          comp.get("scrapeableCompensationSalarySummary") or "")
                break
        mode = map_work_mode(j.get("workplaceType")) or ("remote" if j.get("isRemote") else None)
        return Job(
            source=self.name, source_id=str(j.get("id")), company=company.name, title=title,
            url=j.get("jobUrl") or "", apply_url=j.get("applyUrl") or j.get("jobUrl") or "", location=location,
            work_mode=mode or detect_work_mode(location, title, description),
            job_type=detect_job_type(title, j.get("employmentType")),
            pay=pay or parse_pay(description),
            posted_at=to_iso(j.get("publishedAt")),
            description=description,
            department=j.get("department") or j.get("team") or "",
        )


FEEDS: dict[str, FeedSource] = {s.name: s for s in (GreenhouseSource(), LeverSource(), AshbySource())}
