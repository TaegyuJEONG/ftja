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
   can own its own set: scrape settings (location, hours_old, results_wanted,
   exact phrase), tier1/exclude keywords, rubric, and an optional profile
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

### Open questions

- Is a full profile variant per search needed, or is a shared profile plus a
  per-search emphasis note enough?
- How the Pipeline tab presents multiple sets without overwhelming
  first-time users (default to a single set; reveal more only when added).
- Cost: running Stage 1/2 once per matching search multiplies LLM calls for
  overlapping searches; measure overlap on real runs before deciding.
