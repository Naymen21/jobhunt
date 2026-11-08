---
description: Give unscored jobs an AI match score (0-100) with 2-3 sentences of reasoning. Default 15 jobs per run.
argument-hint: "[how many, default 15]"
---

You are scoring job postings for the student who uses this copy of jobhunt, using only what's in the database. The
website never calls AI; you do the judging here and save results through the project's CLI. Work from this project's
folder (the one with `run.py`) and use the project's Python: `.venv/Scripts/python` on Windows, `.venv/bin/python` on
Mac/Linux. The commands below show the Windows form.

## 1. Read the pending jobs

Batch size: `$ARGUMENTS` if it's a number, otherwise 15. Never more than 30 in one run (it uses the user's own Claude
subscription).

```
.venv/Scripts/python -m applications pending scores --limit <N>
```

The JSON has `you` (profile, default resume text, and `student`: their level, class year, grad year and home area) and
`jobs` (title, company, flags with the sentence that triggered each, `basic_match`, `eligible_for_you`, company size,
`company_local`, `pitch_candidate`, and the description). If `jobs` is empty, say so and stop. If the profile is mostly
empty, say the scores will be rough until they fill in Settings > Profile and upload a resume.

## 2. Score each job (0-100)

Judge how good a fit this job is for them **right now**, at their level (`you.student.label`, class of
`you.student.grad_year`). Only credit experience and interests actually in the resume and "about".

**Eligibility comes first, at their level. Be honest.** `eligible_for_you` is the site's own check; trust it unless the
description clearly says otherwise.

- **High school student** (`student.high_school` true):
  - "Degree program required", a declared major, a college grad year, or a required degree: very likely ineligible.
    Score 0-25 and say why.
  - "College enrollment required": also very likely ineligible (0-25), unless the posting also says incoming
    freshmen / first-years or high school students can apply. Dual-enrollment / PSEO courses do NOT make them a
    college student.
  - "Open to high school students" is the strongest plus: these are the jobs they can realistically get.
- **College or community college student:** current-enrollment wording is fine. A required master's/PhD/MBA, or a
  graduation-year window that clearly excludes theirs, rules them out (0-25). Class year matters: many internships
  want rising juniors/seniors; a freshman applying to those is a stretch, not impossible. Say so.
- **Graduate student:** a required PhD (if they're in a master's) or a far-off grad-year window rules them out.
  Undergrad-only programs ("must be pursuing a bachelor's") are a likely no.
- **Recent graduate:** "must be currently enrolled" or "returning to school" internships rule them out. New-grad and
  entry-level roles fit best.
- Senior roles or many years of experience required: low for everyone here.

**Weigh these strongly:**
- `pitch_candidate: true` (a company of about 100 people or fewer, and a posting they don't quite qualify for): NOT
  ineligible. Small companies make exceptions because the founder or CTO decides. Score on fit, and say
  "pitch candidate" in the reasoning.
- `company_size` around 30-100 (sweet spot ~50): easier to reach a decision-maker.
- `company_local: true` (their home area, `student.home_area`): in-person or hybrid is a real advantage. Score it up.

Use the full range. 80+ means "apply this week", 50-79 "worth a look", under 50 "probably not".

Reasoning: 2-3 plain sentences, 20-600 characters: what fits, what's missing, and any eligibility concern. No fluff.
Refer to the person as "you" or by role, never by a guessed pronoun.

## 3. Save

Write a JSON list to `data/handoff/scores.json`:

```json
[{"job_id": 123, "score": 72, "reasoning": "Python and SQL from your internship match the data work, and the posting is open to your class year. No sign of the dashboard tools they list."}]
```

Then run:

```
.venv/Scripts/python -m applications save scores data/handoff/scores.json
```

If it prints errors, fix those items and save again. Nothing is saved until every item passes.

## 4. Summary

End with a short table: score, company, title, and a few words of the reason, sorted by score. Add a line with how
many unscored jobs are still left (`pending_total` minus what you saved) and remind them to refresh the Jobs page.
