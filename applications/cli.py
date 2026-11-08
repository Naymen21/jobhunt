"""The handoff between the website's database and the Claude Code commands. The website never calls AI.

    python -m applications pending scores [--limit 15]     jobs that need an AI match score (JSON)
    python -m applications pending drafts [--limit 5]      saved jobs that need a cover letter + answers (JSON)
    python -m applications show <job_id>                   one job with its current drafts (JSON, for /redraft)
    python -m applications save scores <file.json | ->     [{"job_id", "score", "reasoning"}]
    python -m applications save drafts <file.json | -> [--feedback "..."]
                                                           [{"job_id", "cover_letter", "answers": [{"question", "answer"}], "notes"}]

Saves are all-or-nothing: if any item fails a check, nothing is saved and every problem is printed (exit code 2).
"""
from __future__ import annotations

import argparse
import json
import sys

from .validate import check_drafts, check_scores


def _repo():
    from storage.apps_repo import AppRepo  # storage is imported lazily, like finder's CLI
    from storage.db import init_db
    init_db()
    return AppRepo()


def _print_json(data) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def _read(path: str):
    text = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise SystemExit(f"error: that isn't valid JSON ({e})")


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m applications", description="AI handoff for jobhunt")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pending")
    p.add_argument("what", choices=["scores", "drafts"])
    p.add_argument("--limit", type=int)
    p = sub.add_parser("show")
    p.add_argument("job_id", type=int)
    p = sub.add_parser("save")
    p.add_argument("what", choices=["scores", "drafts"])
    p.add_argument("file", help="JSON file, or - for stdin")
    p.add_argument("--feedback", default="", help="drafts: the feedback this redraft answers (kept in history)")
    args = parser.parse_args(argv)
    repo = _repo()

    if args.cmd == "pending":
        limit = max(1, min(args.limit or (15 if args.what == "scores" else 5), 50))
        _print_json(repo.pending_scores(limit) if args.what == "scores" else repo.pending_drafts(limit))
        return 0
    if args.cmd == "show":
        data = repo.show(args.job_id)
        if data is None:
            print(f"error: no job {args.job_id}", file=sys.stderr)
            return 2
        _print_json(data)
        return 0

    items = _read(args.file)
    if args.what == "scores":
        checked = check_scores(items, repo.all_job_ids())
    else:
        checked = check_drafts(items, repo.saved_job_ids())
    if checked.errors:
        print("Not saved. Fix these and run the save again:", file=sys.stderr)
        for e in checked.errors:
            print(f"  - {e}", file=sys.stderr)
        return 2
    n = repo.save_scores(checked.ok) if args.what == "scores" else repo.save_drafts(checked.ok, args.feedback)
    print(f"Saved {n} {'score' if args.what == 'scores' else 'draft set'}{'s' if n != 1 else ''}.")
    for w in checked.warnings:
        print(f"  note: {w}")
    return 0
