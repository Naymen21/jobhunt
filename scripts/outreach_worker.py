"""Step the outreach queue from Windows Task Scheduler when the site isn't open (optional).

    .venv\\Scripts\\python scripts\\outreach_worker.py           # one step: schedule, check replies, send at most 1
    .venv\\Scripts\\python scripts\\outreach_worker.py --minutes 15   # keep stepping once a minute for 15 minutes

Every rule still applies: only approved emails, warmup daily caps, weekdays 8am-5pm, random gaps, bounce pause.
Running it while the site is also open is safe (both use the same queue and limits), just unnecessary.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage.db import init_db  # noqa: E402
from storage.outreach_repo import OutreachRepo  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutes", type=int, default=0, help="keep stepping once a minute for this long")
    args = parser.parse_args(argv)
    init_db()
    repo = OutreachRepo()
    end = time.monotonic() + args.minutes * 60
    while True:
        r = repo.tick(check_replies=True)
        print(f"scheduled {r['scheduled']}, replies {r['replies']}, sent {r['sent'] or 'nothing'}"
              + (f" ({r['reason']})" if r["reason"] else ""))
        if time.monotonic() >= end:
            return 0
        time.sleep(60)


if __name__ == "__main__":
    raise SystemExit(main())
