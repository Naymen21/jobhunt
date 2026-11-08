"""The handoff between the database and the outreach commands. Nothing here writes or sends email.

    python -m outreach pending [--limit 5]           picked contacts that need drafts (JSON)
    python -m outreach show <contact_id>             one contact with current drafts, history, reply (JSON)
    python -m outreach save drafts <file|-> [--feedback "..."]
        [{"contact_id", "hook", "hook_source", "hook_level", "subject", "email", "followup1", "followup2",
          "linkedin_note", "notes"}]
    python -m outreach save prep <file|->            [{"contact_id", "call_prep"}]   (/prep-call)
    python -m outreach save thanks <file|->          [{"contact_id", "thanks"}]      (/draft-thanks)

Saves are all-or-nothing; problems are printed and the exit code is 2.
"""
from __future__ import annotations

import argparse
import json
import sys

from .validate import check_outreach, check_text

PREP_MAX_WORDS = 650
THANKS_MAX_WORDS = 120


def _repo():
    from storage.db import init_db
    from storage.outreach_repo import OutreachRepo  # lazy, like the other CLIs
    init_db()
    return OutreachRepo()


def _read(path: str):
    text = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise SystemExit(f"error: that isn't valid JSON ({e})")


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m outreach", description="Outreach handoff for jobhunt")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pending")
    p.add_argument("--limit", type=int, default=5)
    p = sub.add_parser("show")
    p.add_argument("contact_id", type=int)
    p = sub.add_parser("save")
    p.add_argument("what", choices=["drafts", "prep", "thanks"])
    p.add_argument("file")
    p.add_argument("--feedback", default="")
    args = parser.parse_args(argv)
    repo = _repo()

    if args.cmd == "pending":
        print(json.dumps(repo.pending(max(1, min(args.limit, 10))), ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "show":
        data = repo.show(args.contact_id)
        if data is None:
            print(f"error: no contact {args.contact_id}", file=sys.stderr)
            return 2
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0

    items = _read(args.file)
    if args.what == "drafts":
        checked = check_outreach(items, repo.known_for_drafts())
    elif args.what == "prep":
        checked = check_text(items, set(repo.known_for_drafts(any_status=True)), "call_prep", PREP_MAX_WORDS)
    else:
        checked = check_text(items, set(repo.known_for_drafts(any_status=True)), "thanks", THANKS_MAX_WORDS)
    if checked.errors:
        print("Not saved. Fix these and run the save again:", file=sys.stderr)
        for e in checked.errors:
            print(f"  - {e}", file=sys.stderr)
        return 2
    if args.what == "drafts":
        n = repo.save_drafts(checked.ok, args.feedback)
    else:
        n = repo.save_text("call_prep" if args.what == "prep" else "thanks", checked.ok)
    print(f"Saved {n} item{'s' if n != 1 else ''}.")
    return 0
