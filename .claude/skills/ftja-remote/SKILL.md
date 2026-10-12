---
name: ftja-remote
description: Write (or rewrite) the Remote check's setup for the user — the remote-work keywords, the categories the model answers, and which answers pass or fail — from where they live, where they want to work, their time zone and their work permit. Invoke when the user says "/ftja-remote", "set up the remote check", "write the remote setup", or when `ftja.run_bridge wait` returns {"status": "remote"} (the Pipeline tab's button).
---

# FTJA remote check setup

The Remote check is an optional stage of `/ftja-run`, between the code
filter and Stage 1. A cheap model reads only the title and the sentences
around the user's remote keywords, and answers a few questions (the
"categories") by picking from a fixed list of answers. Code then applies
the rule the user set for each answer. Your job here is to write a good
first version of that setup for this user. They edit it afterwards on the
Pipeline tab, so write something sensible and complete, not something
cautious and empty.

Every python call uses `venv/bin/python`. Never edit `remote-check.json`,
`remote-request.json` or `criteria.json` by hand; the commands below write
them.

## 0. Be in the right folder

Run `pwd` and `git rev-parse --show-toplevel`. Continue only in the user's
existing FTJA folder (it has `criteria.json`). If the session is not
attached to it, attach it as `/ftja-run` step 0a describes. Do not start
or restart the viewer for this; if it is running it will show your
progress by itself.

## 1. Get the four answers

```
venv/bin/python -m ftja.remote request
```

- `status` is `requested`, `generating` or `failed`: the user filled the
  form on the Pipeline tab. Use `answers` (`residence`, `work_countries`,
  `time_zone`, `work_permit`) and do not ask again. An empty answer means
  they left it open.
- `status` is `none` or `done`: nobody asked from the page. Ask the user,
  in one message and in their language: where they live (required), which
  countries or regions they want to work in, their time zone, and what
  work permit they hold. Wait for the reply.

Then tell the viewer you have started, so the page shows that the setup is
being written:

```
venv/bin/python -m ftja.remote begin
```

From here on, if you cannot finish, run
`venv/bin/python -m ftja.remote fail --error "<one plain sentence>"` before
you stop, so the page does not wait for you.

## 2. Read what is there

- `criteria.json` → `languages`: the languages the user reads postings in
  (ISO codes; empty means any, then use English plus the main language of
  where they live). Keywords are written in **each** of these languages.
- `venv/bin/python -m ftja.remote show`: the current setup. If `saved` is
  true the user already has one: keep every category you do not recognise
  as one of the five below (they added it), and keep the `id` of every
  category and answer that still exists, even if you reword it.

## 3. Write the setup

Write it as JSON to `.ftja-run/remote-check.draft.json` (create `.ftja-run/`
if it is missing):

```json
{"drop_without_keywords": true,
 "guidance": "<two or three sentences for the model, see below>",
 "profile": {"residence": "...", "work_countries": "...", "time_zone": "...", "work_permit": "..."},
 "categories": [
   {"id": "remote_level", "label": "Remote level",
    "question": "How remote is the role itself?",
    "keywords": ["remote", "work from home", "hybrid", "..."],
    "options": [{"id": "fully_remote", "label": "Fully remote", "rule": "pass"},
                {"id": "hybrid", "label": "Hybrid: regular office days", "rule": "fail"},
                {"id": "on_site", "label": "On-site", "rule": "fail"}],
    "unknown": "pass"}]}
```

**Categories.** Write these five, with these ids, wording the labels for
this user:

1. `remote_level` — how remote the role is. Answers: fully remote (pass),
   hybrid with regular office days (fail), on-site (fail).
2. `work_location` — from where the person may work. Answers, named for
   this user: anywhere (pass); a region or list that includes where they
   live or want to work, e.g. "Includes France or the EU" (pass); only
   places that leave those out, e.g. "Only outside the EU" (fail).
3. `work_authorization` — the right to work. Answers: asks for a right to
   work the user holds (pass); asks for one they do not hold and offers no
   sponsorship (fail); offers visa sponsorship or relocation (pass). Leave
   this category out if the user gave no work permit.
4. `time_zone` — working hours. Answers: any hours (pass); hours that
   overlap with the user's time zone, named (pass); hours far from it,
   named, e.g. "US Pacific hours only" (fail). Leave it out if the user
   gave no time zone.
5. `onsite_duties` — presence asked for besides regular office days.
   Answers: none (pass); occasional — onboarding, team gatherings, some
   travel (pass); frequent — regular on-site weeks or heavy travel (pass:
   the user decides once they have seen real postings).

**The rule for "?"** (`unknown`): `pass` for every category. Most postings
say nothing about most of these; the user tightens a rule after reading
what the check found. Leave `drop_without_keywords` true.

**Answers.** Two to four per category, each a state a posting can plainly
be in, none overlapping. The label is what the user reads on every job
and what the model chooses from, so it must be understandable alone.

**Keywords.** They decide which sentences the model sees, and a posting
with none of them is dropped without a model call. So:

- Be broad. Include the direct words (remote, hybrid, on-site, work from
  home, home office) and the indirect ones that tell how remote a job
  really is: onboarding, offsite, retreat, team gathering, travel, in
  person, headquarters, time zone, working hours, overlap, work from
  anywhere, relocation, work permit, visa, sponsorship. A sentence like
  "you can work where you feel at home" has to be caught by something:
  add the phrases people actually write.
- Write each of them in every language the user reads (French:
  télétravail, à distance, présentiel, sur site, déplacements, fuseau
  horaire, ...).
- **No place names**: no countries, cities or regions (not France, Paris,
  Europe). Where a role may be done from is said in the sentence around a
  remote keyword, and a place name would match nearly every posting.
  Abbreviations of regions used as hiring scopes (EU, EMEA) are fine.
- Matching is by whole word and word stem, any case: `remote` also finds
  "remotely", `gathering` also finds "gather requirements". Prefer a
  phrase when a single word has an everyday meaning: `team gathering`,
  not `gathering`; `distributed team`, not `distributed`; never `global`,
  `country`, `location`, `office` or `work` alone. An all-capitals keyword
  (EU, WFH, CET) matches only in capitals.
- Put each keyword in the category it informs. A keyword of any category
  gets the posting read.

**Guidance.** Two or three sentences the model reads above the questions:
that it judges the role and not the company, what does not count as an
office day (travel, an onboarding week, occasional gatherings), and
anything the user's answers make important.

## 4. Measure before saving

```
venv/bin/python -m ftja.remote coverage --file .ftja-run/remote-check.draft.json
```

With no model call, it counts how many postings of the newest scrape in
this folder have at least one of your keywords (`with_a_keyword` of
`postings`), which keywords catch the most (`by_keyword`) and which never
matched. If there is no scrape yet (`postings` is 0), skip this step.

- A single everyday word near the top that hits a large share of all
  postings is probably matching something else. Read two or three of its
  hits if you are unsure, replace it with a phrase, and measure again.
- Do not chase a low number: catching most postings is acceptable, missing
  a remote one is not.

## 5. Save

```
venv/bin/python -m ftja.remote save --file .ftja-run/remote-check.draft.json
```

It checks the setup and writes `remote-check.json`. If it prints `Not
saved: ...`, fix what it names and run it again. Then delete the draft
file. The Pipeline tab shows the new setup by itself.

## 6. Report

Tell the user, in their language and briefly:

- the categories, and for each which answers fail;
- that "not stated" passes everywhere for now, and that a posting with no
  remote keyword at all is dropped (both are theirs to change);
- how many keywords, in which languages, and the coverage numbers if you
  measured;
- that everything is on the Pipeline tab under **Remote check**, where
  they can change any name, keyword, answer or rule;
- if `criteria.json`'s `stages.remote` is not true: that the check is
  still switched off, and the switch is on that same section. Do not
  switch it on yourself.

If you were called from `/ftja-run`'s wait (`{"status": "remote"}`), go
back to that wait afterwards.
