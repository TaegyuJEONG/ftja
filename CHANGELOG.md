# Changelog

What changed in each version, newest first. If you cloned FTJA earlier, run
`/ftja-update` in your coding agent (or `git pull` in the folder) to get
these. Your own files — `criteria.json`, `rubric.md`, `profile/`, `seen.db`,
digests — are never touched by an update.

## 0.2.0 — 2026-10-07

### What changes for you after updating

- **Verdicts are now Pass / Review / Fail.** "Review" is a posting that is
  ambiguous on a point that decides the verdict; read those yourself. Jobs
  that used to pass weakly now land here, so expect fewer passes.
- **Your first run splits your rubric into single criteria.** `/ftja-run`
  writes `rubric-criteria.json` from your `rubric.md`. Read the list once
  in the viewer's Pipeline tab: it is what Stage 2 judges against from now
  on. The first time you save the list there, `rubric.md` is rewritten from
  it and your original is kept as `rubric.md.bak`.
- **Jobs judged before this version keep their old look** (a paragraph and
  quotes). Nothing is re-judged.
- **`seen.db` gains columns on first use**, automatically.
- Restart the viewer after updating (`/ftja-update` does it).

### Added

- Stage 2 answers every rubric criterion separately, each with a quote from
  the posting. Code checks that each quote is really in the posting and
  computes the verdict by a fixed rule, so the same posting gets the same
  verdict.
- Result cards: one line per criterion under Pass / Fail / Preferences, a
  one-line company description, what the employer asks for (must have /
  preferred) against your profile, and short notes.
- Disagree with a single judgment from its line; `/ftja-review` uses it to
  propose rubric wording.
- Edit the rubric one criterion at a time in the Pipeline tab.
- Watch a run while it happens: scrape counts per search term, each stage's
  numbers, and Stage 2 cards filling in as they are judged.
- Why a job stopped at Stage 1: a reason per job and per keyword sentence,
  from the Stage 1 line in Results.
- `python -m ftja.live replay` plays a finished run back through the live
  view.
- FTJA tells you when a newer version is published, and `/ftja-update`
  installs it.
- Each Stage 2 card also shows what Stage 1 read and why it let the job
  through.
- Details of jobs judged more than two months ago can be removed to save
  space. `/ftja-run` shows what would go and how much space comes back, and
  asks first. The job stays on record: it is never judged again, and it
  still counts in every statistic. Jobs you applied to are left whole.

### Changed

- Each run keeps its working files in `.ftja-run/<run id>/` (last 5 runs)
  instead of `/tmp`.
- Scrape settings are named for what they do: "Remote only", "Posted
  within", "Max results per search term".

## 0.1.0

First public version: scrape LinkedIn, filter with code (Stage 0), judge
keyword sentences with a cheap model (Stage 1), judge the full posting
against your rubric and profile (Stage 2), daily digest, review viewer,
onboarding.
