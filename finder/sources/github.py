"""Commit author name+email pairs from a company's public GitHub repos (free GitHub REST API)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlsplit

import httpx

from ..crawl.extract import is_role
from ..models import FoundEmail
from ..names import looks_like_name
from .base import Source, SourceContext, SourceResult, domain_matches

API = "https://api.github.com"
COMMITS_PER_REPO = 100
LOOKBACK_DAYS = 730


class RateLimited(Exception):
    pass


class GitHubSource(Source):
    name = "github"
    label = "GitHub commits"

    def __init__(self, transport: Optional[httpx.BaseTransport] = None):
        self._transport = transport  # tests inject httpx.MockTransport

    # -- API -----------------------------------------------------------------
    def _client(self, ctx: SourceContext) -> httpx.Client:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": ctx.settings.user_agent}
        if ctx.settings.github_token:
            headers["Authorization"] = f"Bearer {ctx.settings.github_token}"
        return httpx.Client(base_url=API, headers=headers, timeout=ctx.settings.http_timeout, transport=self._transport)

    def _get(self, client: httpx.Client, path: str, params: Optional[dict] = None):
        r = client.get(path, params=params)
        if r.status_code in (403, 429) and r.headers.get("x-ratelimit-remaining") == "0":
            raise RateLimited("GitHub API rate limit reached (60/hour without a token). Add GITHUB_TOKEN to .env.")
        if r.status_code == 404:
            return None
        if r.status_code == 409:  # empty repository
            return []
        r.raise_for_status()
        return r.json()

    # -- org discovery -------------------------------------------------------
    @staticmethod
    def _org_matches_domain(org: dict, domain: str) -> bool:
        blog = (org.get("blog") or "").strip().lower()
        if blog:
            host = urlsplit(blog if "://" in blog else "https://" + blog).hostname or ""
            if host == domain or host.endswith("." + domain):
                return True
        email = (org.get("email") or "").lower()
        return bool(email) and domain_matches(email, domain)

    def find_org(self, client: httpx.Client, domain: str) -> tuple[Optional[str], str]:
        label = domain.split(".")[0]
        tried = []
        for login in dict.fromkeys([label, label.replace("-", ""), f"{label}hq", f"{label}-inc", f"{label}inc"]):
            org = self._get(client, f"/orgs/{login}")
            if org:
                if self._org_matches_domain(org, domain):
                    return org["login"], f"found org '{org['login']}' (website matches {domain})"
                tried.append(f"{login} (website: {org.get('blog') or 'none'})")
        search = self._get(client, "/search/users", {"q": f"{domain} type:org", "per_page": 5}) or {}
        for item in search.get("items", []):
            org = self._get(client, f"/orgs/{item['login']}")
            if org and self._org_matches_domain(org, domain):
                return org["login"], f"found org '{org['login']}' via search (website matches {domain})"
        note = "; ".join(tried) if tried else "no org with a matching name"
        return None, f"no GitHub org whose website is {domain} ({note}). Enter the org name manually to use one."

    # -- main ----------------------------------------------------------------
    def run(self, domain: str, ctx: SourceContext) -> SourceResult:
        found: dict[str, FoundEmail] = {}
        stats = {"repos_scanned": 0, "commits_scanned": 0, "org": None}
        with self._client(ctx) as client:
            try:
                org = (ctx.options.get("github_org") or "").strip() or None
                if org:
                    if self._get(client, f"/orgs/{org}") is None and self._get(client, f"/users/{org}") is None:
                        return SourceResult(self.name, "not_found", f"GitHub org/user '{org}' does not exist", [], stats)
                    how = f"using org '{org}' (entered manually)"
                else:
                    ctx.progress({"phase": "github", "message": "Looking for the company's GitHub org"})
                    org, how = self.find_org(client, domain)
                    if not org:
                        return SourceResult(self.name, "not_found", how, [], stats)
                stats["org"] = org
                repos = self._get(client, f"/orgs/{org}/repos", {"sort": "pushed", "per_page": 50, "type": "public"})
                if repos is None:
                    repos = self._get(client, f"/users/{org}/repos", {"sort": "pushed", "per_page": 50}) or []
                repos = [r for r in repos if not r.get("fork") and not r.get("archived")]
                repos = repos[: ctx.settings.github_max_repos]
                since = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
                for repo in repos:
                    if ctx.cancel.is_set():
                        return SourceResult(self.name, "cancelled", "Cancelled", list(found.values()), stats)
                    ctx.progress({"phase": "github", "message": f"Reading commits in {repo['full_name']}",
                                  "emails_found_github": len(found)})
                    commits = self._get(client, f"/repos/{repo['full_name']}/commits",
                                        {"per_page": COMMITS_PER_REPO, "since": since}) or []
                    stats["repos_scanned"] += 1
                    for c in commits:
                        stats["commits_scanned"] += 1
                        self._collect(c, domain, found)
            except RateLimited as e:
                return SourceResult(self.name, "error", str(e), list(found.values()), stats)
        msg = f"{how}; {stats['repos_scanned']} repos, {stats['commits_scanned']} commits, {len(found)} addresses at {domain}"
        return SourceResult(self.name, "ok", msg, list(found.values()), stats)

    @staticmethod
    def _collect(commit: dict, domain: str, found: dict[str, FoundEmail]) -> None:
        info = commit.get("commit") or {}
        for who in (info.get("author") or {}, info.get("committer") or {}):
            email = (who.get("email") or "").strip().lower()
            if not email or "noreply" in email or not domain_matches(email, domain):
                continue
            name = (who.get("name") or "").strip()
            name = name if looks_like_name(name) else None
            cur = found.get(email)
            if cur is None:
                role = is_role(email)
                found[email] = FoundEmail(
                    email=email, domain=domain, kind="role" if role else "personal", source_type="github",
                    source_url=commit.get("html_url") or "", full_name=None if role else name,
                    name_source="commit" if name and not role else None,
                    context=(info.get("message") or "").split("\n")[0][:120],
                    title=None if role else "Code contributor",
                    department=None if role else "engineering",
                )
            elif name and not cur.full_name and cur.kind == "personal":
                cur.full_name, cur.name_source = name, "commit"
