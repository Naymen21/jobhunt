---
description: Write both cover letter versions (requirements table + plain) and application answers for saved jobs without drafts. Default 5.
argument-hint: "[how many, default 5]"
---

You are drafting job applications for the student who uses this copy of jobhunt. Nothing is ever submitted: they
review, edit and send everything themselves. Work from this project's folder (the one with `run.py`) and use the
project's Python: `.venv/Scripts/python` on Windows, `.venv/bin/python` on Mac/Linux. The commands below show the
Windows form.

## 1. Read the pending work

Batch size: `$ARGUMENTS` if it's a number, otherwise 5. Never more than 10 in one run (it uses the user's own Claude
subscription).

```
.venv/Scripts/python -m applications pending drafts --limit <N>
```

`you` has their profile (name, school, `degree_program`, grad year), `student` (level, class year, home area), their
default resume text and their **answer bank**. `jobs` has each saved job with its description, flags (with the
triggering sentence), basic match and AI score. If `jobs` is empty, say so and stop. If there's no resume text, say
the letters need a resume (Settings > Resumes) and stop.

## 2. Before writing each one

1. Read the job description and what the company says it values (mission, product, team, "what we look for").
2. Pick the **2-3 experiences** from their resume and profile that match this role best. Don't list everything.
3. Check the flags against their level (`student.label`). If one makes them likely ineligible, still write the
   drafts, but say so plainly in `notes`. Never write around it or imply they meet it.
   - High school: college enrollment, a degree program, a declared major, a college grad year or a required degree.
     Dual-enrollment / PSEO courses don't count as college enrollment.
   - College / community college: a required master's/PhD/MBA, or a grad-year window that excludes theirs.
   - Graduate student / recent graduate: undergrad-only or current-enrollment wording, as it applies.
   If the posting explicitly welcomes their level (e.g. "open to high school students", "all class years"), say so in
   `notes`. It's the best sign.

## 3. Cover letter rules (follow all of them)

- About 250 words, never more than 300. 3-4 short paragraphs. Start with "Dear <Company> team," or the hiring
  manager's name if the posting gives one, and end with "Sincerely," then their full name (`you.name`) on the next line.
- **Lead with their strongest match** for this role: a specific thing they did that connects to a specific thing in
  the description. Not "I'm a student".
- Mention who they are **naturally, as context**, in one clause, not as an apology or the opening line: their
  `student.class_year` and level, the school, and `degree_program` (e.g. "a junior studying data science at
  <school>", "a high school senior planning to study computer science").
- Show real experience beyond class (internships, jobs, competitions, teaching, clubs, projects) woven into
  sentences, not as a list.
- Connect 1-2 specific things they've done to specific things in the job description, and say what they'd bring.
- Mention a skill only if the job asks for it or it clearly applies.
- If their "about" names a long-term interest that fits this company (a field, a kind of product), it's fine to
  mention it briefly.
- Plain, natural, specific language that sounds like a real person. No template openers ("I am writing to express
  my interest", "I am excited to apply"), no buzzword stacks ("results-driven", "team player", "fast-paced
  environment", "leverage my skills", "perfect fit"). The save step rejects these.
- **Only use real things from their resume and profile. Never invent experience, skills, numbers, project names,
  awards, dates or people.** If something would help but isn't there, leave it out (or mention it in `notes`).
- No placeholders like [Company] or [Hiring Manager].

## 3b. The requirements-table version (the default for PDF/DOCX)

Write this **as well as** the plain letter above. Same facts, same honesty rules.

- **Opening (2-3 sentences):** lead with their strongest match to this specific role, then who they are (level,
  school, field, and the real work they already do). Never "As a seasoned…", "I am writing to apply", or any stock
  opener.
- **Table, 3-5 rows:** `need` = one requirement taken from the posting's actual list (short, in the posting's words);
  `bring` = 1-2 plain sentences on how they meet it. Write these fresh in plain words. **Don't copy resume bullets.**
  Be honest about gaps, e.g. "Haven't used Salesforce yet, but I taught myself a new database to ship a live site."
  Pick the requirements that matter most for the role, not the easiest ones.
- **Closing (1-2 sentences):** a clear ask, e.g. a short call or an interview.
- No greeting or sign-off inside the table object: the export adds "Dear <Company> team," and "Sincerely, <name>".

## 4. Answers

Include these, in this order:

1. Every answer-bank question that has an answer: copy their answer **as written** and set `"source": "bank"`.
2. Answer-bank questions with an empty answer: if their profile clearly answers it (e.g. work authorization), write a
   short answer from those facts with `"source": "ai"`. Otherwise use `"answer": ""` and add a note telling them
   to fill it in under Settings → Answer bank.
3. Job-specific answers you write (`"source": "ai"`), each 60-120 words, same rules as the letter:
   - "Why do you want to work at <Company>?" (specific to the company, not generic praise)
   - "Why are you interested in this role?"
   - "Tell us about a relevant project or experience." (pick the single best one and give real detail)
   - Add one more only if the posting asks a specific question.

## 5. Save

Write a JSON list to `data/handoff/drafts.json`:

```json
[{"job_id": 123,
  "cover_letter": "Dear Riot Games team,\n\n...\n\nSincerely,\nAlex Rivera",
  "cover_table": {"opening": "...", "rows": [{"need": "Experience with Python", "bring": "..."}], "closing": "..."},
  "answers": [{"question": "When can you start?", "answer": "June 2027", "source": "bank"},
              {"question": "Why do you want to work at Riot Games?", "answer": "...", "source": "ai"}],
  "notes": "Posting says all class years can apply. Start date is June 15; check it fits your school calendar."}]
```

```
.venv/Scripts/python -m applications save drafts data/handoff/drafts.json
```

If it prints errors (too long, a stock phrase, a placeholder, a table with the wrong number of rows), fix those items
and save again.

## 6. Summary

For each job: company, title, word count, the 2-3 experiences you chose, the table's rows (need only), and any notes.
Then remind them to open Applications on the site to review, edit, approve and export. Show the shortest cover letter
in full so they can check the voice.
