# jobhunt

A job and internship search tool for students (high school, college, community college, graduate students and recent
grads) that runs **on your own computer**. It finds postings and small companies, flags requirements you may not meet,
helps you find the right person to email, and keeps track of applications. Claude Code (on **your own** Claude
subscription) writes match scores, cover letters and cold emails, and you approve everything before it's used.

Nothing here is shared with anyone else: your keys, your Claude account, your resume and your database stay in this
folder on your computer. The site only listens on `http://127.0.0.1:8000`.

## What you need

| | Cost | Needed for |
|---|---|---|
| Python 3.11 or newer ([python.org](https://www.python.org/downloads/)) | free | everything |
| [Claude Code](https://docs.claude.com/en/docs/claude-code) signed in with **your** Claude account | your Claude plan | AI scores, cover letters, emails (the site works without it) |
| Gmail App Password (your Gmail) | free | sending outreach emails you approve |
| Apify account (free plan, **your** token) | free credit | optional: LinkedIn job listings |
| GitHub token (no scopes) | free | optional: faster people search |

**Never use an Anthropic API key with this project.** The autopilot refuses to run if `ANTHROPIC_API_KEY` is set,
so it can't run up an API bill. AI work only goes through Claude Code on your subscription.

## Set up (about 10 minutes)

**Windows**
1. Unzip the folder somewhere you'll keep it (e.g. `Documents\jobhunt`).
2. Double-click **`setup.bat`**. It creates `.venv`, installs everything, and makes your own `.env` and empty database.
3. Double-click **`Start jobhunt.bat`**. Your browser opens the site.

**Mac**
1. Unzip the folder (e.g. into `Documents`).
2. Open Terminal, `cd` into the folder and run `chmod +x setup.command "Start jobhunt.command" scripts/*.sh` once, then
   `./setup.command`. (Or right-click `setup.command` → Open; macOS asks once because the file is downloaded.)
3. Double-click **`Start jobhunt.command`** (or run `./"Start jobhunt.command"`). Your browser opens the site.

**Then, in the site**
1. **Settings → Profile:** your name, school, **what you are** (high school, college, community college, graduate,
   recent grad), graduation year, major or planned field, and your **home area** (a metro preset or your state and
   cities). This decides which postings count as "eligible for you" and how letters and emails introduce you.
2. **Settings → Resumes:** upload your resume (PDF or DOCX).
3. **Settings → Keys & usage:** shows which keys are set. Keys are never typed into the site: you add them to the
   `.env` file in this folder (the page shows its exact path and the steps for Gmail and Apify).
4. Install Claude Code and sign in with your own account (`setup` prints the install command). Open a terminal in
   this folder and run `claude`. The slash commands (`/score-jobs`, `/draft-applications`, `/draft-outreach`, ...)
   are already included.

## How it fits together

- **Jobs:** saved searches over public Greenhouse / Lever / Ashby job feeds, Simplify's internship list, Hacker News
  "Who is hiring", and optionally LinkedIn public listings through Apify. Filters for title, location, work mode, pay,
  company size and **who can apply** (judged for your level). Postings that rule you out are tagged with the exact
  sentence; small companies with a posting you don't quite meet become **pitch candidates**.
- **Finder:** finds people and email patterns at a company from its own website and public GitHub, politely
  (robots.txt respected).
- **Applications:** cover letters (plain and requirements-table versions), answers, PDF/DOCX export, status board.
- **Outreach:** pick people, Claude drafts the email and two follow-ups, you approve, it sends from your Gmail under
  strict limits (10/day to start, weekdays 8-5, random gaps, bounce auto-pause, max 2 follow-ups, do-not-contact list).
  **Find companies** lists small companies (default 10-60 people) to cold-pitch with no posting at all.
- **Review:** the home page. What the autopilot prepared, with one-click "Looks good".
- **Autopilot (optional):** a daily run (searches, discovery, scores, drafts) through `claude -p` on your subscription,
  with daily caps. Settings → Autopilot shows the exact steps to schedule it on Windows (Task Scheduler) or Mac
  (`./scripts/schedule_mac.sh`, launchd). It never sends or submits anything.

## Student levels

| Level | "Eligible for you" hides postings that require... | Extras |
|---|---|---|
| High school | current college enrollment, a degree program or declared major, a college grad year, a required degree | "Open to high school students" tag, +15 score, "High school OK" filter; pitch/cold emails must say you're in high school |
| College / community college | a required master's, PhD or MBA; a grad year more than a year from yours | class year used in letters ("a junior studying...") |
| Graduate student | a required PhD; a far-off grad year | |
| Recent graduate | "must be currently enrolled"; a required PhD or MBA; a far-off grad year | emails can ask about a role, not only an internship |

Nothing is ever hidden unless you turn the filter on; flagged postings just get a yellow tag.

## Rules this project keeps

See `CLAUDE.md` (Claude Code reads it too). In short: no paid services, no API keys for Claude, no LinkedIn login or
automation, free public sources only, polite crawling, and nothing sends or submits without your approval.

## Updating your info later

Everything about you lives in Settings and in `.env` / `data/` in this folder. To move to a new version, copy your
`.env` and `data/` folder into the new one.

## Tests

`.venv\Scripts\python -m pytest -q` (Windows) or `.venv/bin/python -m pytest -q` (Mac). Tests use fixtures and fakes,
never live sites.
