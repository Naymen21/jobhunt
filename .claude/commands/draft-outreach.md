---
description: Write the cold email, both follow-ups and a LinkedIn note for contacts you've picked. Researches a real hook first. Default 5.
argument-hint: "[how many contacts, default 5]"
---

You are drafting cold outreach for the student who uses this copy of jobhunt, who is looking for an internship (or,
for a recent graduate, an entry-level role). Nothing is sent from here: they review and approve every email on the
Outreach page, and the LinkedIn note is for them to send **by hand** (this tool never touches LinkedIn). Work from
this project's folder (the one with `run.py`) and use the project's Python: `.venv/Scripts/python` on Windows,
`.venv/bin/python` on Mac/Linux. The commands below show the Windows form.

## 1. Read the pending contacts

Batch size: `$ARGUMENTS` if it's a number, otherwise 5. Never more than 10 in one run (it uses the user's own Claude
subscription).

```
.venv/Scripts/python -m outreach pending --limit <N>
```

`you` = their profile (name, school, `degree_program`, grad year), `student` (level, class year, home area), resume
text and the signature that gets added automatically (don't write a signature).
`contacts` = each person: name, title, department, where their address was found, the company, `company_prefs`,
the `mode` (normal / pitch / cold) and the role they're applying for (`applied`, job id, part of the description;
`null` for a cold pitch, see 3c). If the list is empty, say so and stop.

**Who they are, in one line** (used below as "who + proof"): build it from `student` and the profile, e.g.
"a high school senior at <school> planning to study computer science", "a sophomore studying economics at <school>",
"a master's student in data science at <school>", "a recent <field> graduate from <school>". Use `student.class_year`
when there is one. Never guess a detail that isn't there.

## 2. Research a hook (web search), for each contact

Search for something **specific and real** about this person, their team, or the company's recent work: a talk,
a post, a project they shipped, a launch, a blog post, a news item from the last year. Good searches:
`"<full name>" <company>`, `<company> <team> blog`, `<company> launch <this year>`, `<company> news`.

- Save the hook **with the URL you found it on** (`hook_source`) and say whether it's about the `person`, their
  `team`, or the `company` (`hook_level`).
- If nothing specific turns up for the person, use a team- or company-level hook. That's fine; say so in `notes`.
- **Never invent a detail** about them, and never claim to have read or watched something unless the hook is that thing.

## 3. Cold email principles (what gets replies, from hiring managers and recruiters)

1. **Be upfront.** Say in this first message that they're interested in an internship. No bait-and-switch ("can I
   learn about your job" and then asking for an internship). People dislike it, and every extra back-and-forth is
   another chance for them to stop replying.
2. **Make it about them.** Include one real, specific detail about the person, their team, or the company's recent work.
   If the email could be sent to anyone, it gets ignored.
3. **One proof point with a concrete number,** chosen to match their work. Never list the whole resume.
4. **Short:** 3 small paragraphs, under 130 words (not counting the signature).
5. **Direct, easy ask:** a 15-minute call, or pointing them to the right person.
6. **First name only.** "Hi Elena," and never Sir/Mr/Ms. That's stiff in US tech and can assume the wrong gender.
7. **No attachments.** The resume is a link in the signature (added automatically). **No links in the body.**
8. **Plain, natural language,** like a real person writing one email. Not a template.
9. **Never invent anything:** no fake details about them, and no fake feelings or experiences for the sender. Only
   facts from the resume and profile.

**Structure**
- **Subject:** short and specific to them ("Easy Coach + an AI intern this summer?", "Question about <team>'s
  <project>"). Never "Internship Request".
- **Paragraph 1 (hook):** one sentence about something specific to them or the company, and why it caught the
  sender's attention.
- **Paragraph 2 (who + proof):** who they are in one sentence (see "Who they are" above), then ONE project or
  experience with a number that connects to the recipient's work.
- **Paragraph 3 (ask):** internship interest stated plainly. If `role.applied` is true, mention the role title and job
  id ("I applied for the Technical Customer Support Internship, job 103"). Then the 15-minute call or right-person ask.
- Start with "Hi <first name>,". End the body after the ask: "Thanks," + signature are added automatically.

**Unpaid:** only mention working unpaid if `company_prefs.ok_to_offer_unpaid` is true (that means they marked the
company as a small startup and turned it on). Big companies generally can't legally take unpaid interns.

**Reference example** (a made-up student; match the style and length, don't copy it):

```
Subject: Easy Coach + an AI intern this summer?

Hi Elena,

I'm in the middle of college applications myself, so Easy Coach caught my attention right away.

I'm a high school senior at Lincoln High heading into computer science. I built StudyBuddy, a homework-help site my
school's tutoring club now uses, and it's reached about 600 students. I'd love to help on the AI side of Easy Coach as
an intern and see how a real product team works.

Would you have 15 minutes for a quick call next week?
```

## 3b. Pitch mode (when the contact's `mode` is "pitch")

The company is small (`company_size` about 100 or fewer) and the posting asks for something they don't quite meet at
their level (for a high schooler: college enrollment; for a college student: a later class year or a higher degree).
Small companies often make exceptions because the founder or CTO decides. The contact is the founder, CTO, or the
hiring manager for that team (never HR).

- **Angle:** "I saw your posting for <role>. I'm <who they are>, so I don't match <the requirement> exactly, but my
  work lines up with what you're building. Would you consider me?" Name the gap **in the first or second sentence**.
  Never hide it. For a **high school student** the save step refuses pitch emails that don't say "high school" there.
- **Hook:** their mission or product (`company_one_liner` is a start; research more), and why it connects to the
  sender personally or to their work. Only real connections from the resume/profile. Never invented.
- **Proof:** ONE project or experience that matches what they build, with a number.
- **Ask:** a 15-minute chat to see if an intern at their level could work for them.
- Same principles otherwise (first name, under 130 words, no links in the body, plain language).
- In `notes`, remind them: apply only if they say yes or ask them to.

## 3c. Cold mode (when the contact's `mode` is "cold")

There's **no posting**: they picked this small company (`company_size` about 5-60) because they like what it builds,
and `role` is null. The contact is a founder, CEO, CTO or engineering lead. This is the classic small-company cold
email: "I noticed what you're building, I like it, here's what I've done, could we talk about whether an intern
could help?"

- **Research first.** Start from `company_one_liner`, `company_about` and `company_website`, then search for something
  recent and specific: a launch, a blog post, a product update, a talk, a news item, or the founder's own post. The
  hook must be real and have its URL. If nothing recent turns up, use what the product does and say so in `notes`.
- **Paragraph 1 (hook):** the specific thing, and why it interests them. Only real connections from the
  resume/profile (e.g. they built a matching site, so a matching product genuinely connects). Never invent feelings
  or experiences.
- **Paragraph 2 (who + proof):** who they are, **in the first or second sentence of the email** (a high school
  student must say "high school" there; the save step checks). Then ONE project with a number that fits what they build.
- **Paragraph 3 (ask):** plainly ask whether they'd consider an intern, with a low-pressure ask: "Would you be open to a
  15-minute chat to see if there's a way I could help as an intern this summer?" Don't mention a role or job id
  (there isn't one), and don't claim they're hiring.
- **Subject:** about them, e.g. "<Product> + a student intern?", "Question about <Product>'s <feature>".
- Same principles otherwise: first name, under 130 words, no links in the body, no bait-and-switch, plain language.
- **Unpaid** only if `company_prefs.ok_to_offer_unpaid` is true, as always.
- **Follow-ups:** follow-up 1 adds one new thing (a relevant project detail or a thought about their product);
  follow-up 2 is a short, easy-to-say-no bump.

## 4. The rest of the sequence (all written now, sent only if there's no reply)

- **LinkedIn note** (under 200 characters, no links): the same hook and ask, for them to send by hand.
- **Follow-up 1** (sent 7 days later, same thread): 2-3 sentences that add ONE new thing: a recent project update
  from the resume or profile, or a relevant thought about their work. Not "just following up".
- **Follow-up 2** (14 days after that): 1-2 sentences, a last polite bump. Easy to say no to.

## 5. Save

Write `data/handoff/outreach.json`:

```json
[{"contact_id": 7,
  "hook": "Elena's team launched Easy Coach, an AI college-application coach, in October.",
  "hook_source": "https://example.com/blog/easy-coach-launch",
  "hook_level": "team",
  "subject": "Easy Coach + an AI intern this summer?",
  "email": "Hi Elena,\n\n...\n\nWould you have 15 minutes for a quick call next week?",
  "followup1": "Hi Elena, ...",
  "followup2": "Hi Elena, ...",
  "linkedin_note": "Hi Elena, ...",
  "notes": "No person-level detail found; used the team launch."}]
```

```
.venv/Scripts/python -m outreach save drafts data/handoff/outreach.json
```

The save step refuses: a missing source URL, links in the body, Sir/Mr/Ms, stock phrases, more than 130 words,
an email that doesn't say "internship", unpaid offers the company isn't marked for, and LinkedIn notes over 200
characters. Fix those and save again.

## 6. Summary

For each contact: name, title, company, the hook + its source, subject, word count. Show the first email in full for
the first contact, and remind them to review and **Approve** on the Outreach page (approving covers both follow-ups;
they only send if there's no reply).
