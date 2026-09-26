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
- a nine-page Adevinta AI House EiR case study; and
- a supporting workbook with 10,492 non-empty cells.

Observed findings:

- `portfolio_interview.md` listed six projects while the current public index
  listed eight, so a single portfolio summary can be stale.
- Trachemy reported 35 projects in the portfolio and 40+ pilot projects in the
  CV; the source material did not establish whether these were the same metric
  at different dates.
- TSF and PLACES appeared more complete in the CV than in the portfolio, so
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
