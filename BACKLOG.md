# FTJA Backlog

This document records deliberately deferred product work. `SPEC.md` remains the
authoritative scope for the current release. A backlog item should not be added
to v1 unless its evidence gate is met and the scope decision is revisited.

## Structured Candidate Evidence

### Status

Deferred until post-v1 validation.

### Decision

Launch v1 with:

- `profile/summary.md`
- `rubric.md`
- the full job description
- the existing Stage 0 → Stage 1 → Stage 2 judgment funnel

Do not add project cards, claim-level retrieval, a vector database, or a
candidate knowledge graph before launch.

### Why this is deferred

The earlier JobSpyProject pipeline already produced judgments close to the
founder's own decisions by combining:

1. deterministic keyword filtering;
2. low-cost LLM judgment of the matched sentence and surrounding context; and
3. mid-tier-model judgment of the full JD and candidate profile.

The immediate product risk is whether other users can complete onboarding,
run the funnel, and value its decisions. It is not yet proven that a richer
candidate evidence system is necessary.

### Minimum v1 safeguards

- The user reviews and can edit `profile/summary.md` before confirming it.
- The UI shows which profile sources were processed and which were skipped.
- A portfolio folder is not represented by its README alone.
- Stage 2 decisions retain reasoning and JD evidence.
- User feedback is captured as `Good match`, `Not for me`, or `Applied`, with a
  reason when practical.
- Duration and token usage are recorded per run when the runtime exposes them;
  unavailable values are omitted rather than invented.

### Evidence from the September 2026 spike

Representative sources were inspected without modifying them:

- a two-page CV;
- a portfolio repository exposing eight projects;
- a nine-page case study; and
- a supporting workbook with 10,492 non-empty cells.

Observed findings:

- An older portfolio summary listed six projects while the current public
  index listed eight, so a single portfolio summary can be stale.
- One project's count was reported differently in the portfolio and in the
  CV; the source material did not establish whether these were the same
  metric at different dates.
- Two projects appeared more complete in the CV than in the portfolio, so
  project-stage normalization would require user review.
- The case-study workbook mixed candidate research, third-party facts,
  proposals, and assumptions; these cannot safely be treated as equivalent
  achievements.
- Exhaustive analysis took approximately 8 minutes 29 seconds even with three
  parallel workers.
- The extracted source material was approximately 124K–165K tokens, mostly due
  to the workbook.

Conclusion: a structured evidence system is technically useful but too costly
and complex for the v1 onboarding path.

### Evidence gate for development

Start this work only if post-launch evidence repeatedly shows one or more of
the following:

- Good jobs are rejected because relevant project evidence was absent from
  `profile/summary.md`.
- Stage 2 attributes one project's result to another project.
- Proposals, assumptions, or roadmap items are treated as completed results.
- Users repeatedly edit the generated summary to restore missing context.
- Profile updates require expensive full regeneration.
- Stage 2 profile context becomes materially too large or slow.
- Users ask to see which parts of their background support a requirement.

Before implementation, classify the observed failures by cause: source
coverage, summary quality, rubric quality, Stage 1 filtering, or Stage 2
judgment. Do not assume retrieval is the answer to every disagreement.

### Phase 1 — Thin project cards

Add:

```text
profile/summary.md
profile/projects.md
profile/sources.json
```

Each project card should contain:

- role;
- product stage;
- industry and product context;
- three to five candidate actions;
- one to three reported results;
- evidence limitations; and
- source references.

Stage 2 should initially read all thin cards. Do not add retrieval while the
complete card set remains small enough to fit comfortably in one call.

### Phase 2 — Incremental profile updates

- Store a content hash per source.
- Reprocess only changed sources.
- Update only affected project cards.
- Preserve explicit user corrections.
- Report partial coverage instead of silently retaining stale cards.

### Phase 3 — Requirement-to-evidence retrieval

For each full JD:

1. extract the role thesis, hard requirements, preferences, and operating
   context;
2. retrieve relevant project claims;
3. construct an evidence packet containing support and limitations; and
4. reread the full JD when producing the final verdict.

Add this only after `projects.md` becomes materially too large or irrelevant
projects demonstrably reduce judgment quality.

### Phase 4 — Claim-level evidence

Possible claim types:

- completed action;
- measured result;
- self-reported result;
- research output;
- proposal;
- assumption;
- third-party fact;
- limitation; and
- unresolved conflict.

Each claim should retain an exact source location and must not be promoted from
proposal or assumption to achievement without new evidence.

### Phase 5 — Optional experience visualization

An experience map may be generated from the structured evidence for the user.
Mermaid can be an output format, but it must not be the canonical profile or
evidence format.

### Portfolio-only onboarding

A portfolio-only user can be supported, but FTJA must:

1. inspect the portfolio index or navigation;
2. discover project-detail pages;
3. extract visible project text rather than raw scripts and styling;
4. ignore dependencies, build output, translated duplicates, and migration
   utilities;
5. report how many projects were reviewed in detail; and
6. state that employment chronology, education, and formal titles are unknown
   unless explicitly provided.

Do not generate a candidate profile from README alone. If a portfolio is too
large to inspect fully, report the limit and ask the user to select projects
for deeper review.

### Note from 2026-10-13

Retrieval stays deferred. The amount of material may grow well past a resume
and a portfolio: one idea is to take in Builder's Diary, a searchable,
up-to-date portfolio built from a person's AI conversations. To keep that
possible without touching the pipeline later, each stage should read the
profile through one place in the code, so the profile's form can change
behind it.

### Explicitly out of scope for v1

- vector databases and embeddings;
- a candidate knowledge graph;
- a claim dependency graph;
- semantic indexing of every spreadsheet row;
- external verification of every metric;
- automatic resolution of conflicting claims; and
- Mermaid as the profile source of truth.

### Validation plan when this backlog item is activated

Compare the current summary-only Stage 2 flow against thin project cards on a
fixed set of real JDs. Record:

- agreement with the user's judgment;
- false-positive and false-negative reasons;
- input and output tokens;
- wall-clock duration;
- model and provider; and
- which project evidence changed the verdict.

Do not proceed to claim-level retrieval unless thin cards materially improve
judgment or reduce cost on that fixed set.

## Volume-Based Judgment Mode and Keyword Recall Audit

### Status

Deferred. Recorded 2026-10-01 from a measured experiment on one real run.

### Problem

The Stage 0 tier1 keyword gate is the recall ceiling: a JD with no tier1 hit
is auto-failed without an LLM call. This works well when the keywords have
been tuned over many runs for one role type. Other users may search for roles whose JDs use varied
vocabulary (e.g. cybersecurity consulting, business development, startup
support), where agent-generated keywords could silently drop real matches.
Roles defined by concrete skills (e.g. iOS: Swift, SwiftUI, Xcode) are lower
risk because those words almost always appear in the JD body.

### Measured evidence (one real run, ~1,000 scraped jobs)

Sample of 94 jobs judged on the FULL JD (`stage2_prompt.md`), compared with
the keyword pipeline:

| Stratum | haiku full-JD pass | sonnet full-JD pass |
|---|---|---|
| No tier1 hit (30 random) | 0 | 0 |
| Stage 1 fail (30 random) | 1 | 1 (different job) |
| Stage 1 pass (34) | 17 | 15 (same as pipeline) |

- The tuned keyword gate lost no candidates in this sample.
- Subagent tokens per job: haiku full JD, batches of 10 ≈ 8.4k; sonnet full
  JD, batches of 4 ≈ 19k; sonnet single ≈ 65k. The keyword pipeline cost about
  3.0M tokens for the whole run. Full-JD screening of the ~600
  language-passed jobs would cost about 5M (haiku) to 11M (sonnet).
- Batching side finding: sonnet in batches of 4 agreed with itself across
  two runs (12/12). Single runs agreed 10/12. Two borderline jobs passed in
  both batched runs and failed in all four single runs, and were co-batched
  both times. Final Stage 2 judgment should stay single-job until this is
  re-measured on a larger set.
- Plan usage: the whole experiment (~4.2M subagent tokens plus the
  orchestrating session) used about 45 percentage points of one 5-hour window
  (the app reported the account as Max; the plan tier is unconfirmed).
  Per-plan limits still need to be measured on a known Pro account.

### Proposed design

1. **Volume-based mode, chosen automatically after scrape.** Count jobs that
   pass the language filter. If the count is within the user's nightly budget
   (e.g. ≤ 100 jobs), skip the keyword gate and judge every full JD. Otherwise
   use the keyword gate. Report which mode ran and why. Let the user set the
   budget and force a mode.
2. **Keyword recall audit on every keyword-mode run.** Judge a random sample
   (10–20) of no-tier1-hit jobs on the full JD with a cheap model. If any pass,
   report them as missed matches and propose keywords drawn from those JDs
   (via the `/ftja-tune` confirmation flow). Track the audit miss rate over
   time so users can see how far to trust the gate.
3. Keyword generation at setup stays as a starting point only; the audit is
   what makes keywords trustworthy for a new role type.

### Explicitly out of scope

- Embeddings or semantic indexing as a separate stage. The two modes plus the
  audit sample cover the same need without new dependencies.

## Per-Search Pipeline Profiles

### Status

Deferred. Recorded 2026-10-01.

### Problem

Today every search term shares one configuration end to end: the same scrape
settings, tier1 keywords, `rubric.md`, and `profile/summary.md` drive Stage 0,
Stage 1, and Stage 2. A user hunting for several different role types (e.g.
"Product Builder" and "Venture Manager") needs different keywords, different
pass/fail criteria, and sometimes a different emphasis in their profile for
each. One merged rubric becomes a compromise that serves none of them well.
FTJA's goal is to let each user fine-tune their own job search, so this level
of freedom matters.

### Proposed design

1. **A pipeline profile per search.** Each search term (or group of terms)
   can own its own set: scrape settings (location, hours_old, results_wanted),
   tier1/exclude keywords, rubric, and an optional profile
   variant. Stages 0–2 for a job use the set of the search that found it.
2. **Duplicate and edit, not start from scratch.** Creating a new search
   offers "copy from an existing search" so the user only changes what
   differs.
3. **AI-drafted starting point.** Alternatively, the agent proposes a new set
   from existing data (current profile, existing rubrics, past review
   decisions) for the new search, and the user reviews and confirms before
   anything is written, as with `/ftja-tune`.
4. **Cross-search duplicates.** A job found by more than one search should be
   judged once per search set (verdicts may legitimately differ) but shown
   once in the digest, labeled with every search that matched it.

### Direction agreed 2026-10-13

Discussed while moving onboarding into the chat, then deferred again:
onboarding on Windows and macOS comes first.

- A track is a whole pipeline for one position: its own resume, search
  terms, locations, keywords, exclude words and rubric. One position can
  still have several search terms ("Product Manager", "AI Product Manager").
  Only facts about the person are shared (languages read, the Remote check's
  situation).
- The Pipeline tab gets a sub-tab per track and a + button.
- One flow serves three entry points (first setup, the + button, a resume
  uploaded again under a track): attach the resume, run the profile skill,
  confirm the proposed keywords, answer the pass / fail / preference
  questions. What the user confirms is saved to the track's files, and a run
  reads those files when it starts.
- Two tracks that use the same resume each keep a copy, so uploading a new
  one changes one track only.
- First setup proposes tracks from the resume and completes one; the others
  stay as drafts that go through the + flow.
- The profile skill records a hash of the sources it last processed, per
  track, so a run can say that a file in the track has not been processed.
- An existing installation becomes one track holding today's settings.
- Order of work: the track structure with a single track first (no change
  in behaviour), then several tracks and the sub-tabs.

### Open questions

- Does a scheduled run cover every track that is switched on, and can a
  manual run name one track?
- The number of searches is tracks x search terms x locations; measure the
  run time before allowing many tracks.
- How the Pipeline tab presents multiple sets without overwhelming
  first-time users (default to a single set; reveal more only when added).
- Cost: running Stage 1/2 once per matching search multiplies LLM calls for
  overlapping searches; measure overlap on real runs before deciding.

## Decision history on the Pipeline tab

### Status

Deferred. Recorded 2026-10-13. Wanted soon, and before several tracks: the
decisions will then pile up per track.

### Problem

The user's decisions on jobs (apply or skip, with a reason) are saved to
files and nothing shows them. The user cannot see whether the pipeline is
getting closer to their own judgment, or which reasons have already become
rubric lines.

### Proposed design

A read-only section on the Pipeline tab, beside the rubric (the decisions
are the material for changing the rubric, not the profile):

- how many jobs were applied to and skipped, with the reasons grouped;
- which reasons are already in the rubric and which `/ftja-review` has not
  looked at yet;
- how many postings passed each stage, per run.

The section shows the `/ftja-review` command to type in the chat; the page
does not reach the agent.

## Compare rubrics on one set of postings

### Status

Deferred. Recorded 2026-10-13. Not urgent.

### Problem

A user may want to try two rubrics on the same postings (for example, a set
of remote roles already collected) to see which one finds the roles that
suit them.

### Decided so far

- A run continued later uses the rubric as it is then, not a copy taken when
  the run started.
- Each judgment should record which rubric it was made with; without that
  two results cannot be compared.
- With tracks (see Per-Search Pipeline Profiles), two tracks that differ
  only in the rubric, run over the same postings, would give this. The track
  design should not rule that out.

## Stage 1 as a screen-out filter (keywords as dealbreaker signals)

### Problem

Stage 1 passes a job only on a sentence that shows a pass criterion. That
fits a search where few postings qualify and the pass criteria describe the
work ("builds prototypes"). It fits badly when the rubric is about
conditions a posting may simply not state (remote, location, visa, contract
type): in one remote-focused run, 106 of 244 Stage 1 fails cited no
sentence against the job, only "remote is not stated in these sentences".

### Proposed design

A `stage1_mode` in `criteria.json`, chosen during onboarding from the
rubric:

- `screen_in` (today's behaviour): keywords are signs of a role worth
  reading; a pass needs an evidence sentence.
- `screen_out`: keywords mark where a dealbreaker would be stated; a fail
  needs an evidence sentence, everything else goes to Stage 2, and a
  posting with no keyword sentence skips Stage 1.

A first version was built and removed before release (the Stage 0 switch,
a per-mode verdict rule in the Stage 1 prompt, and the evidence check
flipped per mode in `ftja.live.stage1_problems`).

### Why it is parked

- Cost: on that run it would have sent roughly 200 jobs to Stage 2 instead
  of 75 (an estimate; Stage 1 was not rerun).
- A posting that says nothing about a condition the candidate requires is
  not one they should apply to on trust, so passing silence is the wrong
  default for the candidate too.
- Choosing the mode belongs in onboarding, which makes this a larger change
  than Stage 1 alone.

## Run the tests on every pull request

### Problem

Nothing runs `pytest` in CI. A pull request's only checks are the Vercel
preview deployment; `pages.yml` deploys the landing page on a push to main
and does not run on pull requests. The 108 tests run only when someone
remembers to run them locally.

### Proposed design

1. A workflow that runs `pytest` on every pull request (one file, about 20
   lines; the suite takes about 14 seconds).
2. A Stage 0 regression test on a saved set of scraped jobs: keyword hit
   counts, repost groups and drop counts compared with stored numbers. No
   model call, so it can run in CI.
3. Leave the full run on real postings manual. Replaying one run of 559
   jobs cost 8.4M tokens, so it cannot run per pull request.

Automatic fixing of CI failures stays off: a failing test means either the
code or the test is wrong, and deciding which needs a person.

### Why it is parked

One developer, MVP stage, no other users yet. Revisit when someone else
contributes or when a change ships broken because the tests were not run.

## Remote check: sentences that name a place, for postings already found remote

### Problem

The Remote check reads only the sentences around remote keywords. Where a
role may be done from is usually said there ("remote within the EU"), but
not always: a posting can say "fully remote" in one place and "candidates
must be based in Spain" three paragraphs later, with no remote keyword
near it. The location category is then "not stated" although the posting
states it.

Place names were deliberately left out of the keywords (decided
2026-10-12): a country or city name matches nearly every posting, so as a
keyword it would send everything to the model and filter nothing.

### Proposed design

For postings the check has already found remote, and only those, have code
pull the sentences that name a place (the user's country and city, plus
region words) and show them to the model in a second, small call that
fills in the location category. Postings that fail the remote level never
get this call, so the cost is bounded by the number of remote passes.

### Why it is parked

An edge case at this stage. A run with Stage 2 on already covers it, since
Stage 2 reads the whole posting. Take it up when real runs show the
location as "not stated" for postings whose text does say it.

## Remote check: apply changed rules to a finished run

### Problem

The model's answers are stored per posting (`seen_jobs.remote`), and pass
or fail is computed from them by the user's rules. So after the user
changes a rule ("not stated" fails for the remote level), a finished run
could be re-sorted without any model call. Today it is not: a finished run
keeps the verdicts of the rules it ran with, and the new rules apply from
the next run on.

### Proposed design

A "Re-apply my rules" action on a run that stopped after the Remote check:
recompute `remote_pass` / `remote_fail` for its rows from the stored
answers and the current `remote-check.json`, and rewrite the run's counts.
Runs that went on to Stage 1 or 2 are harder (a posting that now passes
was never judged further) and would need a continue step for the newly
passing ones.

### Why it is parked

Not needed to ship the stage. The popup already shows every answer with
its rule, so the effect of a rule can be read off before changing it.

## Word forms in languages other than English

### Problem

A keyword matches whole words and their forms: "prototype" also finds
"prototyping" and "prototypes". The folding of forms is the English Porter
stemmer, applied to every keyword whatever its language. In French, German
or Korean it folds little or nothing useful: "basé" does not find "basée"
or "basés", and Korean particles attached to a word are not separated at
all.

### What is done instead

`/ftja-keywords`, `/ftja-remote` and onboarding are told to list the forms
themselves (singular and plural, masculine and feminine). That works for
the common forms and misses the rest.

### Proposed design

Pick the stemmer by the keyword's recorded language (`tier1_language`,
`keyword_language`), and stem each sentence with the stemmer of the
posting's detected language. Snowball covers the European languages;
Korean needs a morphological analyser, a much heavier dependency.

### Why it is parked

Decided 2026-10-12: listing the forms is enough to start with. Take it up
when a run shows postings in a second language being dropped for a missing
word form.

## Setup in the chat (dropped)

Decided 2026-10-20: not taken up. The core of FTJA is the interaction
between the agent and the web view, so setup stays viewer-driven. The
chat-only setup (`68ac5fb`) and the work built on it are kept, unmerged, on
the `archive/chat-only-onboarding` branch for reference.

## More job boards than LinkedIn

### Problem

FTJA searches LinkedIn only (`SITES = ["linkedin"]` in `ftja/scrape.py`).
jobspy also reads Indeed, Glassdoor, ZipRecruiter, Bayt, Naukri, BDJobs and,
from 1.3.0, HelloWork (France). Google Jobs is listed as unavailable.

### What is already in place

- jobspy is called from `ftja/scrape.py` and nowhere else.
- Stage 0, `seen.db` and the digests read `job_url`, `title`, `company`,
  `location` and `description`, which jobspy returns for every board.
- Reposts are grouped by company, title and description, not by URL, so a
  posting on two boards should be merged. Not tried: boards format the
  description differently.
- The tests never call a job board; they write `scrape/<n>.json` themselves.
  Nothing tests `ftja/scrape.py` against a live board.

### What it takes

- Move from jobspy 1.1.82 to 1.3.0 first. In October 2026 upstream fixed
  every board's scraper (remote flag, `hours_old`, paging). In 1.3.0
  `linkedin_fetch_description` is `fetch_description`, LinkedIn's `job_type`
  is ignored with a warning, and the private page fetch the title-first
  scrape uses is renamed from `_get_job_details` to `_fetch_details`.
- Indeed and Glassdoor need `country_indeed`. The search settings have
  locations only, so a country has to be derived from them or asked for.
- The batch of 10 with an offset loop, and the gap between search requests,
  were measured on LinkedIn. Each board needs its own loop: `results_wanted`
  is per board and page sizes and limits differ (Glassdoor: about 2 requests
  a second; ZipRecruiter: US and Canada only).
- Text written for LinkedIn: "Scraping LinkedIn" and the removal of
  `is_remote` in `ftja/app.html`, the title rule's explanation, the
  `/ftja-run` and `/ftja-setup` skills.
- A live check per board and country: result counts, description format,
  repost merging, blocking. This is where the time goes, not the code.

### Proposed first step

Indeed alone. Its search results carry the description, so there is no page
fetch per posting; upstream's README says it has no rate limiting; its
search accepts quotes and `-` (it also searches the description, so it
returns more unrelated postings).

### Why it is parked

Decided 2026-10-13: the title-first scrape came first. Take this up as its
own change, after the jobspy upgrade.


## Show the titles a search left out

### Problem

A search scrapes only postings whose title contains its term. A user who
searches "Product Manager" does not see that LinkedIn also returned 40
"Product Owner" postings, so they cannot tell which titles to add.

### What is already in place

Every search writes the titles it left out, with how many of each, to
`.ftja-run/<run>/scrape/<n>.skipped.json`. Nothing reads the file yet, and
it goes when the run folder is pruned.

### Proposed design

On the run's funnel, under each search term: "Also returned, not scraped:
Product Owner (42), Chef de produit (31), …", each with a button that adds
it as a search term. Group titles that differ only in seniority or case.
Keep the top titles in the run record so they outlive the run folder.

### Why it is parked

Decided 2026-10-13: the rule and its explanation shipped first, with the
titles recorded so this needs no new scraping.


## Ask before judging a large run

### Problem

Tokens are spent in the Remote check, Stage 1 and Stage 2, not in the
scrape. With no cap per search, a broad term over a long period can pass
hundreds of postings to the model without the user having chosen that.

### Proposed design

After Stage 0, when more postings than a threshold wait to be judged, stop
and say how many, as a run that stops after Stage 0 already does. The user
continues with `/ftja-run continue` or narrows the search.

### Why it is parked

Decided 2026-10-13: a daily run over 24 hours is small. Take it up when a
run's token count surprises someone.

## Scheduled runs on macOS and Windows

### Status

Not started. Scheduled runs have not been tested end to end on macOS either.

### Problem

A daily run is the product's promise, but today it exists only as
`com.ftja.run.example.plist`, a launchd file the user installs by hand
(`claude --print /ftja-run`). It has never been run end to end, it does not
exist on Windows, and the "run finished" notification (`ftja/notify.py`,
`osascript`) is macOS only.

### Options

- **Claude Code desktop app scheduled tasks** (preferred to try first): one
  mechanism on macOS and Windows, runs on the user's computer where the FTJA
  folder is, and the run stays visible in the app as a session. Needs the
  app open. Setup could register it for the user.
- **OS schedulers** (launchd, Windows Task Scheduler): run with the app
  closed, but headless `claude --print` needs every permission allowed in
  advance and fails silently otherwise; two implementations to keep.
- Cloud routines (`/schedule`) do not fit: the user's criteria, profile and
  `seen.db` are only on their computer.

### To do

1. Check on macOS and Windows how desktop scheduled tasks behave when the
   computer sleeps or the app is closed at the scheduled time.
2. Implement the chosen mechanism, and offer it at the end of setup.
3. A "run finished" notification on Windows, if the mechanism does not
   already show one.
4. Test a real scheduled run end to end on macOS and on Windows.
