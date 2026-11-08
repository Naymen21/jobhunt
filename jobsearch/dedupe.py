"""The same job from two sources (a company's Greenhouse board and a LinkedIn listing of it) becomes one job.

Two postings are the same job when they come from different sources and share company + title + city.
Within one source, different posting ids stay separate jobs (companies do post the same title twice)."""
from __future__ import annotations

import re

from .models import Job
from .normalize import norm_words

_COMPANY_SUFFIX = re.compile(r"\b(?:inc|llc|ltd|limited|corp|corporation|co|company|gmbh|ag|sa|plc)\b")


def norm_company(name: str) -> str:
    return re.sub(r"\s+", " ", _COMPANY_SUFFIX.sub(" ", norm_words(name))).strip()


def norm_title(title: str) -> str:
    t = norm_words(re.sub(r"\(.*?\)", " ", title or ""))
    return re.sub(r"\s+", " ", t).strip()


def city(job: Job) -> str:
    if job.work_mode == "remote" and not job.location:
        return "remote"
    first = re.split(r"[;,|/]| - ", job.location or "")[0]
    return norm_words(first) or ("remote" if job.work_mode == "remote" else "")


def match_key(job: Job) -> str:
    return f"{norm_company(job.company)}|{norm_title(job.title)}|{city(job)}"


def richness(job: Job) -> int:
    """Prefer the copy with more detail when merging (company feeds usually beat LinkedIn)."""
    return (len(job.description) > 200) * 4 + (job.pay is not None) * 2 + (job.work_mode is not None) + \
        (job.job_type is not None) + (job.source != "apify")


def merge_into(base: Job, other: Job) -> Job:
    """Combine two copies of one job; keeps the richer one's fields and fills gaps from the other."""
    if richness(other) > richness(base):
        base, other = other, base
    for attr in ("work_mode", "job_type", "pay", "posted_at", "location", "department", "apply_url"):
        if not getattr(base, attr) and getattr(other, attr):
            setattr(base, attr, getattr(other, attr))
    if other.posted_at and base.posted_at and other.posted_at < base.posted_at:
        base.posted_at = other.posted_at
    seen = {(s.source, s.source_id) for s in base.sources}
    base.sources += [s for s in other.sources if (s.source, s.source_id) not in seen]
    return base


def dedupe(jobs: list[Job]) -> list[Job]:
    out: list[Job] = []
    by_key: dict[str, list[int]] = {}
    by_id: dict[tuple, int] = {}
    for job in jobs:
        ident = (job.source, job.source_id)
        if ident in by_id:  # exact same posting listed twice
            out[by_id[ident]] = merge_into(out[by_id[ident]], job)
            continue
        key = match_key(job)
        target = next((i for i in by_key.get(key, [])
                       if job.source not in {s.source for s in out[i].sources}), None)
        if target is not None:
            out[target] = merge_into(out[target], job)
            idx = target
        else:
            out.append(job)
            idx = len(out) - 1
            by_key.setdefault(key, []).append(idx)
        for s in out[idx].sources:
            by_id[(s.source, s.source_id)] = idx
    return out
