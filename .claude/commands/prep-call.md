---
description: One-page brief for a 15-minute call with a contact - who they are, your 2-sentence story, 3 questions, answers to likely objections.
argument-hint: "<contact_id>"
---

Prepare the user for a 15-minute call. Work from this project's folder (the one with `run.py`) and use the project's Python: `.venv/Scripts/python` on Windows, `.venv/bin/python` on Mac/Linux (commands below show the Windows form).

1. `.venv/Scripts/python -m outreach show $ARGUMENTS`. It has the person, the role, the hook and its source, the emails
   sent and their reply. If there's no contact id, ask for it and stop.
2. Look them up with a web search (their role, team, recent work) so the brief is current. Use only what you find,
   and cite the URLs in the brief. Never invent facts about them.
3. Write the brief in plain markdown, **under 600 words**, with these sections:
   - **Who they are:** role, team, what they work on (with source links).
   - **Your story in 2 sentences:** who they are (level, school, field) plus the one experience most relevant to them (real facts only).
   - **Why this company:** 2-3 honest, specific reasons tied to their work.
   - **3 questions to ask them:** specific to their team or product, not generic.
   - **If they say...** short, honest answers to:
     - "We don't usually take interns at your level" (high schoolers, first-years...).
     - "Intern hiring is done for this cycle."
     - "We can't train interns right now."
   - **The ask to end on:** the next step (a referral, a name to contact, applying to a specific role).
4. Save it as `[{"contact_id": <id>, "call_prep": "<the markdown>"}]` in `data/handoff/prep.json`, then:
   ```
   .venv/Scripts/python -m outreach save prep data/handoff/prep.json
   ```
5. Show the brief and remind them it's on the contact's card under "Call prep".
