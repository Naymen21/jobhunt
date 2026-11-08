"""Run every saved job search marked "Include in the daily run", one after another, and print a summary.

Meant for Windows Task Scheduler (see README: "Daily auto-run"). Nothing is scheduled automatically.
Uses the same engine, database and Apify budget guard as the website's Run now button, so it can run
while the site is open or closed.

    .venv\\Scripts\\python scripts\\run_saved_searches.py            # searches marked for the daily run
    .venv\\Scripts\\python scripts\\run_saved_searches.py --all      # every saved search
    .venv\\Scripts\\python scripts\\run_saved_searches.py --search 3 # just one
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.jobs import job_runs  # noqa: E402
from storage.db import init_db  # noqa: E402
from storage.jobs_repo import JobRepo  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true", help="run every saved search, not just the daily ones")
    parser.add_argument("--search", type=int, help="run only this saved search id")
    args = parser.parse_args(argv)

    init_db()
    repo = JobRepo()
    repo.seed_watchlist_once()
    searches = repo.searches()
    if args.search:
        searches = [s for s in searches if s.id == args.search]
    elif not args.all:
        searches = [s for s in searches if s.auto_run]
    print(f"{datetime.now():%Y-%m-%d %H:%M} jobhunt daily run: {len(searches)} saved search(es)")
    failed = 0
    for s in searches:
        if job_runs.active_for(s.id):
            print(f"  - {s.name}: already running in the website, skipped")
            continue
        run = job_runs.execute(job_runs.create(s.id, trigger="scheduled"), s.id)
        if run.status == "done":
            print(f"  - {s.name}: {run.kept} jobs, {run.new_jobs} new")
        else:
            failed += 1
            print(f"  - {s.name}: {run.status} ({run.error or run.message})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
