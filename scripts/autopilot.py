"""The daily autopilot (Windows Task Scheduler runs this; see README "Daily autopilot").

    .venv\\Scripts\\python scripts\\autopilot.py                 # morning run: everything
    .venv\\Scripts\\python scripts\\autopilot.py --kind catch-up # afternoon: only the AI steps left unfinished
    .venv\\Scripts\\python scripts\\autopilot.py --no-searches   # skip saved searches (testing)

AI steps go through Claude Code (`claude -p`) on your subscription, within the daily caps in Settings > Autopilot.
At a usage limit they stop cleanly and the catch-up run finishes them. Nothing is sent or submitted by this script;
it prepares drafts for your morning review. Refuses to run if ANTHROPIC_API_KEY is set.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.autopilot_run import Autopilot  # noqa: E402
from storage.db import init_db  # noqa: E402
from storage.jobs_repo import JobRepo  # noqa: E402


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--kind", choices=["morning", "catch-up", "manual"], default="morning")
    parser.add_argument("--no-searches", action="store_true")
    args = parser.parse_args(argv)
    init_db()
    JobRepo().seed_watchlist_once()
    run = Autopilot(run_searches=not args.no_searches).run(args.kind)
    print(f"autopilot {args.kind}: {run.status} (Claude: {run.claude_status}{', resets ' + run.claude_reset if run.claude_reset else ''})")
    for item in json.loads(run.log_json):
        counts = ", ".join(f"{k} {v}" for k, v in item["counts"].items())
        print(f"  {item['step']:16} {item['status']:8} {counts}" + (f" | {item['detail'][:160]}" if item["detail"] else ""))
    return 0 if run.status in ("done", "stopped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
