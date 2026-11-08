"""Build a shareable zip of this folder: code only, never your .env, database, resumes, caches or venv.

    <venv python> scripts/make_release.py                       -> dist/jobhunt-YYYY-MM-DD.zip
    <venv python> scripts/make_release.py --deny "Jane Doe,555-123-4567,jane@example.com"

It refuses to write the zip if any included file looks like a secret (Apify / GitHub / Anthropic / OpenAI tokens, a
Gmail App Password, a filled-in key line) or contains a --deny word (your name, phone, email, school...). Windows
.bat files get CRLF line endings, Mac .command/.sh files get LF and the executable bit."""
from __future__ import annotations

import argparse
import re
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_TOP = {"data", "dist"}  # your database, resumes, caches and backups; built zips (top level only: finder/data is code)
SKIP_DIRS = {".venv", "venv", "__pycache__", ".pytest_cache", ".git", ".idea", ".vscode", "node_modules"}
SKIP_FILES = {".env", "settings.local.json", ".DS_Store", "Thumbs.db"}
SKIP_SUFFIXES = {".db", ".db-journal", ".sqlite", ".pyc", ".log", ".gz", ".zip", ".pdf", ".docx"}
TEXT_SUFFIXES = {".py", ".md", ".html", ".css", ".js", ".txt", ".json", ".toml", ".cfg", ".ini", ".bat", ".command",
                 ".sh", ".example", ".svg", ".gitignore", ""}
SECRETS = [
    ("Apify token", re.compile(r"apify_api_[A-Za-z0-9]{20,}")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|github_pat)_[A-Za-z0-9_]{20,}")),
    ("Anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}")),
    ("Gmail App Password", re.compile(r"GMAIL_APP_PASSWORD\s*=\s*['\"]?[a-z]{4} ?[a-z]{4} ?[a-z]{4} ?[a-z]{4}(?!x)", re.I)),
    ("filled-in key", re.compile(r"^(?:APIFY_TOKEN|GITHUB_TOKEN|GMAIL_APP_PASSWORD|ANTHROPIC_API_KEY)[ \t]*=[ \t]*\S{8,}", re.M)),
]
ALLOWED_SECRET_LOOKALIKES = {"xxxx xxxx xxxx xxxx", "abcd efgh ijkl mnop"}


def included(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if rel.parts[0] in SKIP_TOP or any(part in SKIP_DIRS for part in rel.parts[:-1]):
        return False
    fixture = rel.parts[:2] == ("tests", "fixtures")  # test fixtures (a sample PDF) are made up, not yours
    return path.is_file() and path.name not in SKIP_FILES and (fixture or path.suffix.lower() not in SKIP_SUFFIXES)


def scan(files: list[Path], deny: list[str]) -> list[str]:
    problems = []
    for f in files:
        rel = f.relative_to(ROOT).as_posix()
        if f.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            problems.append(f"{rel}: not UTF-8 text, check it by hand")
            continue
        for label, rx in SECRETS:
            for m in rx.finditer(text):
                if m.group(0).split("=")[-1].strip().strip("'\"").lower() not in ALLOWED_SECRET_LOOKALIKES:
                    problems.append(f"{rel}: looks like a {label}")
        low = text.lower()
        problems += [f"{rel}: contains \"{w}\"" for w in deny if w.lower() in low]
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--deny", default="", help="comma-separated words that must not appear (your name, email, phone...)")
    ap.add_argument("--out", default=str(ROOT / "dist"))
    args = ap.parse_args(argv)
    deny = [w.strip() for w in args.deny.split(",") if w.strip()]
    files = sorted(p for p in ROOT.rglob("*") if included(p))
    problems = scan(files, deny)
    if problems:
        print("Not building the zip. Fix these first:")
        print("\n".join(f"  - {p}" for p in problems))
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    zpath = out / f"jobhunt-{date.today().isoformat()}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            rel = f.relative_to(ROOT).as_posix()
            data = f.read_bytes()
            if f.suffix in (".bat",):
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            elif f.suffix in (".command", ".sh"):
                data = data.replace(b"\r\n", b"\n")
            info = zipfile.ZipInfo(f"jobhunt/{rel}", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = ((0o755 if f.suffix in (".command", ".sh") else 0o644) | 0o100000) << 16
            z.writestr(info, data)
        z.writestr(zipfile.ZipInfo("jobhunt/data/.keep", date_time=(2026, 1, 1, 0, 0, 0)), b"")
    print(f"Wrote {zpath} ({len(files)} files, {zpath.stat().st_size // 1024} KB). Checked for secrets"
          + (f" and {len(deny)} personal words." if deny else "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
