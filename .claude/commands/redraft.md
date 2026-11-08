---
description: Rewrite one job's cover letter and answers using your feedback, e.g. /redraft 42 make it shorter and mention my B+Tree database
argument-hint: "<job_id> <feedback>"
---

Rewrite the drafts for one saved job using the user's feedback. Work from this project's folder (the one with `run.py`) and use the project's Python: `.venv/Scripts/python` on Windows, `.venv/bin/python` on Mac/Linux (commands below show the Windows form).

Arguments: `$ARGUMENTS`. The first word is the job id (the number shown in the Applications detail panel); the rest
is the feedback. If there's no job id or no feedback, ask for it and stop.

1. Read everything for that job, including the current drafts and any earlier feedback:
   ```
   .venv/Scripts/python -m applications show <job_id>
   ```
   If the job has no application yet, tell them to save it on the Jobs page first, and stop.
2. If `edited_by_you` is true, their edited cover letter is the starting point. Keep their changes unless the feedback
   says otherwise.
3. Apply the feedback to **both** versions: the plain `cover_letter` and the `cover_table`
   (opening + 3-5 need/bring rows + closing). Follow every rule in `.claude/commands/draft-applications.md`
   (sections 3, 3b and 4): real facts only, no invented experience, plain language, the same answer format.
   If `table_edited_by_you` is true, start from their edited table. If there's no table yet, write one.
   If the feedback asks for something that isn't in their resume or profile (e.g. a project you can't find), don't
   invent details. Use what's there, and say in `notes` what you couldn't find.
4. Write a one-item JSON list to `data/handoff/redraft.json` (same format as /draft-applications) and save it with
   the feedback attached, so it's kept in the history:
   ```
   .venv/Scripts/python -m applications save drafts data/handoff/redraft.json --feedback "<the feedback>"
   ```
   Fix any errors and save again.
5. Show the new table version in full (opening, rows, closing) and a one-line summary of what changed in each version. Remind them that the previous version
   is kept in the history.
