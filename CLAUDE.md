# Job Hunt: project rules

Local-only job search tool for students (high school, college, graduate, recent grads). Each person runs
their own copy on their own computer with their own keys and their own Claude subscription; "the owner" below
means whoever runs this copy. These rules are permanent and apply in every phase.
If a request conflicts with them, stop and ask instead of working around them.

## Cost rules (never break these)
- Zero paid services. The only cost is the owner's Claude subscription.
- The website NEVER calls the Claude/Anthropic API. No `ANTHROPIC_API_KEY` anywhere in this project,
  `.env`, or the system environment. Do not add the `anthropic` package or any LLM SDK.
- All AI work (match scoring, cover letters, cold emails) happens through Claude Code slash commands in
  `.claude/commands/` that read pending items from SQLite and write results back. The website only
  displays and manages them.
- The website must work fully without AI; AI-dependent items just show as "waiting for drafts."
- Paid APIs (Brave, Serper, SMTP VPS, etc.) stay off. Free sources only: site crawling, GitHub API,
  Apify free tier, and public Greenhouse/Lever/Ashby job board feeds.
- Never add anything that could charge a card automatically.

## Account safety rules (never break these)

### Email sending (Outreach)
- Configurable daily send cap. Default ramp: 10/day, 20/day after 14 clean days (bounce rate under 2%), then 25/day
  after 14 more. Hard ceiling 30/day. An automatic bounce pause restarts the ramp.
- Random delays between sends (minimum several minutes), only during configurable hours (default weekdays 8am-5pm local).
- Only emails with "seen in the wild" or strong-pattern confidence can be auto-queued. Anything "unknown" needs manual approval.
- Track bounces. If bounce rate goes over 2% (or 2+ bounces in a day), automatically pause sending and alert the owner.
- Plain text only, no tracking pixels, no attachments on first contact. No links in the email body; at most 2 links in
  the signature (LinkedIn + resume).
- Max two follow-ups per person (7 days after the first email, then 14 days after that), sent in the same thread and
  only if there's been no reply. Permanent do-not-contact list for anyone who asks to stop. (Replaces the old
  one-follow-up rule, at the owner's request in Phase 5.)
- Nothing ever sends without the owner's approval. Approving a contact approves its email and both follow-ups.
- Gmail is used through an App Password (SMTP 587 to send, IMAP read-only to detect replies to outreach threads only).
  Credentials live in .env only, never in the database, the UI or logs.

### LinkedIn
- Never use or store the owner's LinkedIn login, password, or session cookies (`li_at`) anywhere in this project.
- Job scraping only goes through Apify actors that scrape public listings without the owner's account.
- The tool never performs actions on the owner's LinkedIn account: no auto-applying, connection requests, or messages.
- For external application sites: human pace (a few per day, each one approved by the owner), no headless or hidden automation.

## Engineering conventions
- Build in phases. Plan first, get approval, and don't build ahead into later sections.
- The only approved Apify actor is `curious_coder/linkedin-jobs-scraper` (public listings, no login fields).
  Every Apify run goes through the budget guard in `jobsearch/sources/apify.py` (usage check first, refuse
  above 80% of the monthly credit, a daily budget of (80% of the credit - usage so far) / days left in the month,
  hard `maxTotalChargeUsd` cap per run). Using a different actor needs the owner's approval.
- Automation (the daily autopilot) runs AI steps only through Claude Code (`claude -p`) on the owner's subscription.
  It stops cleanly at a usage limit and resumes after the reset; it never uses extra/paid usage, and it refuses to run
  if ANTHROPIC_API_KEY is present in the environment.
- Discovery sources must be free, public and login-free, respect robots.txt, and not forbid automated access in their
  terms (e.g. Wellfound is out).
- Logic lives in the `finder/` and `jobsearch/` engine packages (and later engine packages). The web app (`app/`) and CLI only
  call into it. No business logic in templates or routes.
- Persistence is shared through `storage/`. The engine talks to it only through protocols, never imports it.
- Python 3.11+ in the project venv: `.venv\Scripts\python` on Windows, `.venv/bin/python` on Mac/Linux.
  `setup.bat` / `setup.command` create it.
- Run: `Start jobhunt.bat` (Windows) or `Start jobhunt.command` (Mac), or the venv Python with `run.py`
  (http://127.0.0.1:8000, localhost only).
  Tests: `<venv python> -m pytest -q`. Tests use fixtures and fake servers, never live sites.
- Outbound port 25 is blocked on most home networks: `SMTP_TRANSPORT=disabled` by default. Confidence must come from evidence.
- Crawling: obey robots.txt, slow and polite, identifying User-Agent. Never scrape search engine results pages.
