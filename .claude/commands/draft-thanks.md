---
description: Short thank-you after a call that mentions one specific thing you discussed, plus anything they asked you to send.
argument-hint: "<contact_id> [what you talked about / what they asked for]"
---

Draft a thank-you email after the user's call. Work from this project's folder (the one with `run.py`) and use the project's Python: `.venv/Scripts/python` on Windows, `.venv/bin/python` on Mac/Linux (commands below show the Windows form).

`$ARGUMENTS`: the first word is the contact id; the rest is their notes from the call (what they talked about, anything
they asked them to send). If there are no notes, ask for one specific thing from the call before writing. The
email must mention something that actually happened, and you can't know that without their notes.

1. `.venv/Scripts/python -m outreach show <contact_id>` for the person, the role and the thread so far.
2. Write the thank-you:
   - "Hi <first name>," then 3-5 sentences, under 120 words, plain text, no links in the body.
   - Thank them for their time, mention ONE specific thing from their notes, and handle anything they asked for
     ("Here's the project I mentioned…" goes as a line describing it; links stay in the signature).
   - End with the agreed next step if there was one. No new asks beyond that.
   - Only real facts. Don't invent what was said.
3. Save `[{"contact_id": <id>, "thanks": "<text>"}]` to `data/handoff/thanks.json`, then:
   ```
   .venv/Scripts/python -m outreach save thanks data/handoff/thanks.json
   ```
4. Show it and remind them to copy it from the contact's card and send it as a reply in the same thread from Gmail.
