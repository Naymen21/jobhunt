---
description: Rewrite one contact's outreach drafts with your feedback, e.g. /redraft-outreach 7 shorter, and use the robotics teaching
argument-hint: "<contact_id> <feedback>"
---

Rewrite one contact's email, follow-ups and LinkedIn note using the user's feedback. Work from this project's folder (the one with `run.py`) and use the project's Python: `.venv/Scripts/python` on Windows, `.venv/bin/python` on Mac/Linux (commands below show the Windows form).

`$ARGUMENTS`: the first word is the contact id (shown on the Outreach card), the rest is the feedback. If either is
missing, ask and stop.

1. `.venv/Scripts/python -m outreach show <contact_id>`. It has the current drafts, the hook and its source, whether
   they edited them (`edited_by_you`: keep their edits unless the feedback says otherwise), and earlier feedback.
2. Apply the feedback. Follow every rule in `.claude/commands/draft-outreach.md` (sections 3 and 4). Keep the
   existing hook unless the feedback asks for a new one. A new hook needs a fresh web search and a real source URL.
   Never invent details; if the feedback asks for something not in their resume or profile, say so in `notes`.
3. Write a one-item list to `data/handoff/outreach.json` (same format) and save it with the feedback, so the old
   version is kept in the history:
   ```
   .venv/Scripts/python -m outreach save drafts data/handoff/outreach.json --feedback "<the feedback>"
   ```
   If the contact was already approved but not sent, this puts it back to "Draft ready" so they approve the new version.
4. Show the new email in full and one line on what changed.
