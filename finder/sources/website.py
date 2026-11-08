from __future__ import annotations

from ..crawl import crawl_domain
from .base import Source, SourceContext, SourceResult


class WebsiteSource(Source):
    name = "website"
    label = "Company website"

    def run(self, domain: str, ctx: SourceContext) -> SourceResult:
        res = crawl_domain(domain, ctx.settings, progress=ctx.progress, cancel=ctx.cancel,
                           max_pages=ctx.options.get("max_pages"), fetcher=ctx.options.get("fetcher"),
                           start_url=ctx.options.get("start_url"))
        stats = {
            "pages_crawled": res.pages_crawled, "pdfs_parsed": res.pdfs_parsed, "base_url": res.base_url,
            "robots": res.robots_status, "skipped_by_robots": res.skipped_by_robots, "stopped": res.stopped,
            "errors": res.errors[:10],
        }
        if res.blocked:
            return SourceResult(self.name, "blocked", f"Skipped: {res.robots_detail}", [], stats)
        if res.stopped == "unreachable":
            return SourceResult(self.name, "error", "Site unreachable: " + "; ".join(res.errors[:3]), [], stats)
        if res.stopped == "cancelled":
            return SourceResult(self.name, "cancelled", f"Cancelled after {res.pages_crawled} pages", res.emails, stats)
        msg = f"{res.pages_crawled} pages ({res.pdfs_parsed} PDFs), {len({e.email for e in res.emails})} addresses"
        if res.skipped_by_robots:
            msg += f"; {res.skipped_by_robots} pages skipped per robots.txt"
        return SourceResult(self.name, "ok", msg, res.emails, stats)
