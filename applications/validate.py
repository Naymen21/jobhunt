"""Checks on what the Claude Code commands send back, before anything is saved.

Errors stop the save and are printed for the command to fix and retry. Warnings are saved with the draft."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

SCORE_REASON_MIN, SCORE_REASON_MAX = 20, 600
COVER_SOFT_MAX = 250  # words: warn above this
COVER_HARD_MAX = 320  # words: refuse above this
NOTES_MAX = 1200

# Template openers and buzzword stacks the owner asked to avoid. Matched case-insensitively.
BANNED_PHRASES = [
    "i am writing to express", "i'm writing to express", "i am excited to apply", "i am thrilled to apply",
    "to whom it may concern", "i believe i would be a great fit", "i am confident that i would be",
    "hit the ground running", "team player", "fast-paced environment", "results-driven", "go-getter",
    "synergy", "leverage my skills", "leveraging my", "passionate about leveraging", "detail-oriented self-starter",
    "think outside the box", "dynamic team", "perfect fit", "as a seasoned", "i am writing to apply",
    "i'm writing to apply", "please find attached",
]
TABLE_ROWS = (3, 5)
TABLE_CELL_MAX_WORDS = 55  # "What I bring": 1-2 plain sentences
TABLE_OPENING_MAX_WORDS = 90


def sentences_in(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s.strip()])


def check_table(table) -> tuple[dict, list[str]]:
    """The requirements-table cover letter: opening, 3-5 rows of need | bring, closing."""
    problems = []
    if not isinstance(table, dict):
        return {}, ["cover_table must be an object with opening, rows and closing"]
    opening = (table.get("opening") or "").strip()
    closing = (table.get("closing") or "").strip()
    rows = table.get("rows") or []
    if not opening:
        problems.append("cover_table.opening is empty")
    elif sentences_in(opening) > 3 or words(opening) > TABLE_OPENING_MAX_WORDS:
        problems.append("cover_table.opening should be 2-3 sentences")
    if not closing:
        problems.append("cover_table.closing is empty")
    elif sentences_in(closing) > 2:
        problems.append("cover_table.closing should be 1-2 sentences")
    if not isinstance(rows, list) or not TABLE_ROWS[0] <= len(rows) <= TABLE_ROWS[1]:
        problems.append(f"cover_table needs {TABLE_ROWS[0]}-{TABLE_ROWS[1]} rows")
        rows = rows if isinstance(rows, list) else []
    clean_rows = []
    for i, r in enumerate(rows, 1):
        need = (r.get("need") or "").strip() if isinstance(r, dict) else ""
        bring = (r.get("bring") or "").strip() if isinstance(r, dict) else ""
        if not need or not bring:
            problems.append(f"cover_table row {i} needs both 'need' and 'bring'")
            continue
        if sentences_in(bring) > 2 or words(bring) > TABLE_CELL_MAX_WORDS:
            problems.append(f"cover_table row {i}: 'bring' should be 1-2 short sentences")
        clean_rows.append({"need": need, "bring": bring})
    everything = " ".join([opening, closing] + [r["need"] + " " + r["bring"] for r in clean_rows])
    problems += [f"cover_table uses a stock phrase: \"{p}\"" for p in BANNED_PHRASES if p in everything.lower()]
    if PLACEHOLDER.search(everything):
        problems.append(f"cover_table has a placeholder: {PLACEHOLDER.search(everything).group(0)}")
    return {"opening": opening, "rows": clean_rows, "closing": closing}, problems
PLACEHOLDER = re.compile(r"\[(?!TODO)[A-Z][^\]]{0,40}\]|\{\{|\bXX+\b|<(?:company|name|role)>", re.I)


@dataclass
class Checked:
    ok: list = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def words(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text or ""))


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def check_scores(items, known_job_ids: set[int]) -> Checked:
    out = Checked()
    if not isinstance(items, list):
        out.errors.append("expected a JSON list of {job_id, score, reasoning}")
        return out
    for n, item in enumerate(items, 1):
        where = f"item {n}"
        if not isinstance(item, dict):
            out.errors.append(f"{where}: not an object")
            continue
        job_id, score = _int(item.get("job_id")), _int(item.get("score"))
        reasoning = " ".join(str(item.get("reasoning") or "").split())
        if job_id is None or job_id not in known_job_ids:
            out.errors.append(f"{where}: unknown job_id {item.get('job_id')!r}")
            continue
        if score is None or not 0 <= score <= 100:
            out.errors.append(f"job {job_id}: score must be a whole number 0-100")
            continue
        if not SCORE_REASON_MIN <= len(reasoning) <= SCORE_REASON_MAX:
            out.errors.append(f"job {job_id}: reasoning must be {SCORE_REASON_MIN}-{SCORE_REASON_MAX} characters (2-3 sentences)")
            continue
        out.ok.append({"job_id": job_id, "score": score, "reasoning": reasoning})
    return out


def check_drafts(items, known_job_ids: set[int]) -> Checked:
    out = Checked()
    if not isinstance(items, list):
        out.errors.append("expected a JSON list of {job_id, cover_letter, answers, notes}")
        return out
    for n, item in enumerate(items, 1):
        if not isinstance(item, dict):
            out.errors.append(f"item {n}: not an object")
            continue
        job_id = _int(item.get("job_id"))
        if job_id is None or job_id not in known_job_ids:
            out.errors.append(f"item {n}: job_id {item.get('job_id')!r} isn't a saved job")
            continue
        where = f"job {job_id}"
        cover = (item.get("cover_letter") or "").strip().replace("\r\n", "\n")
        notes = (item.get("notes") or "").strip()
        problems = []
        if not cover:
            problems.append("cover_letter is empty")
        n_words = words(cover)
        if n_words > COVER_HARD_MAX:
            problems.append(f"cover letter is {n_words} words; keep it under {COVER_SOFT_MAX}")
        lowered = cover.lower()
        problems += [f"cover letter uses a stock phrase: \"{p}\"" for p in BANNED_PHRASES if p in lowered]
        if PLACEHOLDER.search(cover):
            problems.append(f"cover letter has a placeholder: {PLACEHOLDER.search(cover).group(0)}")
        answers = item.get("answers") or []
        if not isinstance(answers, list):
            problems.append("answers must be a list of {question, answer}")
            answers = []
        clean_answers = []
        for a in answers:
            q = (a.get("question") or "").strip() if isinstance(a, dict) else ""
            ans = (a.get("answer") or "").strip() if isinstance(a, dict) else ""
            if not q:
                problems.append("an answer is missing its question")
                continue
            if PLACEHOLDER.search(ans):
                problems.append(f"answer to \"{q}\" has a placeholder")
            low = ans.lower()
            problems += [f"answer to \"{q}\" uses a stock phrase: \"{p}\"" for p in BANNED_PHRASES if p in low]
            clean_answers.append({"question": q, "answer": ans, "source": a.get("source") or "ai"})
        if len(notes) > NOTES_MAX:
            problems.append(f"notes are over {NOTES_MAX} characters")
        table = None
        if item.get("cover_table") is not None:
            table, table_problems = check_table(item["cover_table"])
            problems += table_problems
        if problems:
            out.errors += [f"{where}: {p}" for p in problems]
            continue
        if n_words > COVER_SOFT_MAX:
            out.warnings.append(f"{where}: cover letter is {n_words} words (aim for {COVER_SOFT_MAX} or fewer)")
        out.ok.append({"job_id": job_id, "cover_letter": cover, "answers": clean_answers, "notes": notes,
                       "words": n_words, "cover_table": table})
    return out
