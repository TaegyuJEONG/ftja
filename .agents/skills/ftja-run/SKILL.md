---
name: ftja-run
description: Run the daily FTJA job-search pipeline (scrape LinkedIn -> deterministic filter -> cheap-model sentence-block judgment -> mid-model resume/portfolio judgment -> digest + notification). Invoke when the user says "run job search", "run FTJA", "check today's postings", or on the scheduled launchd trigger.
---

# FTJA run

You are executing one pass of the FTJA pipeline defined in `SPEC.md`. Follow
these steps in order. Every python call below uses `venv/bin/python` — if
`venv/` doesn't exist yet, run `python3 -m venv venv && venv/bin/pip install -r requirements.txt` first.

## 0a. Reattach the existing workspace and viewer

When `/ftja-run` is invoked in a new Claude Code session, reconnect the existing
FTJA repository before running anything:

1. Run `pwd` and `git rev-parse --show-toplevel`. Continue only if the resolved
   repository is the user's existing FTJA workspace. Do not clone a new copy or
   choose a similarly named folder.
2. Call `mcp__ccd_directory__change_directory` with that exact absolute repo
   path and require the `Folder access granted` result. A shell `cd` alone is
   not enough. If the repo is not the active workspace, stop and tell the user
   to open/connect the existing FTJA folder; never run against a scratch folder.
3. Check `http://127.0.0.1:8765/`. If the server is not responding, start it
   from the connected repo, verify it with `curl`, then call
   `mcp__Claude_Browser__preview_start` followed by
   `mcp__Claude_Browser__get_page_text`. On macOS also run
   `open http://127.0.0.1:8765` so the viewer is visible without an extra
   `Open` click.

Do not claim the Claude header is connected unless it visibly shows the folder.
If the directory tool confirms access but the header remains `No folder`, report
that client UI limitation separately and continue only with the confirmed repo
path.

## 0. Preflight

- Check `rubric.md` and `criteria.json` exist in the project root. If either
  is missing, STOP and tell the user to run `/ftja-setup` first — do not
  improvise a rubric yourself.
- Check the lock: `venv/bin/python -m ftja.lock check .ftja.lock`. If it
  prints `locked` (exit code 1), append one line to `run.log` ("skipped —
  already running") and STOP. Do not proceed.
- Acquire the lock: `venv/bin/python -m ftja.lock acquire .ftja.lock`.
- **Always release the lock before finishing** (`venv/bin/python -m ftja.lock release .ftja.lock`),
  including on any error path. Treat this like a try/finally.
- Note the wall-clock start time both as an epoch (for `duration_seconds`)
  and as an ISO8601 UTC string (`ts_start`, for the stats file in step 5),
  and start a running total of tokens spent (add each Stage1/Stage2 Agent
  call's `usage.subagent_tokens`, visible in that call's completion
  notification, as it arrives).
- Snapshot `criteria_snapshot` from `criteria.json` right now: `{search_terms,
  location, is_remote, hours_old, results_wanted}`. This goes into the stats
  file as-is — criteria.json can change later via /ftja-tune, and a run
  record should show what was actually used for THAT run, not whatever
  criteria.json holds when someone opens the review server afterward.
- **Check for pending review decisions**: `venv/bin/python -m ftja.state
  pending-review`. This is what replaces a live watcher — review "just
  happens" as part of the normal run cadence instead of needing a
  background process. If it's empty, continue straight to step 1. If this
  is an **unattended invocation** (triggered by the scheduled launchd
  trigger, no one to ask) and the list is non-empty, also continue straight
  to step 1 — pending items just wait for the next interactive run, don't
  block the scrape over them. If this is an **interactive invocation** (the
  user typed something to trigger this) and the list is non-empty, pause
  here and follow `/ftja-review`'s steps 2-3 (propose concrete
  criteria.json/rubric.md changes for each pending reason, confirm before
  applying) before moving on to step 1 — this is the only place that skill's
  logic runs unless the user explicitly calls `/ftja-review` on its own.

## 1. Scrape (Stage 0a)

Read `criteria.json` for `search_terms` (list), `location`, `is_remote`,
`hours_old`, `results_wanted`, `exact_phrase_search` (bool — whether each
search term is sent to LinkedIn as an exact quoted phrase; default `true`
if the key is missing, for backward compatibility with criteria.json files
written before this option existed). For each search term, run:

```
venv/bin/python -m ftja.scrape --search-term "<term>" --location "<location>" \
  --results-wanted <results_wanted> --hours-old <hours_old> \
  --exact-phrase / --no-exact-phrase (per exact_phrase_search) \
  --out /tmp/ftja-scraped-<n>.json
```

Merge all the output files into one list (dedup by `job_url` across terms).
Note the merged count — this is `scraped_count` for the stats file.

## 2. Deterministic filter (Stage 0b)

```
venv/bin/python -m ftja.filter_stage0 --jobs <merged_scraped.json> --criteria criteria.json --out /tmp/ftja-stage0.json
```

Read the printed `Stage0: N -> M (dropped: {...})` line — `M` is your
coverage number for this run (compare it, over time, against what
LinkedIn's own search UI shows for the same term — that's the Acceptance
Criteria #2 check, not something to automate here). The `dropped` dict
(language/no_tier1_hit/exclude_keyword counts) goes into the stats file as
`stage0_dropped`. Location is not part of Stage 0 — it's already
deterministic at scrape time via `criteria.json`'s `location`/`is_remote`.

The language check (langdetect against `criteria.json`'s `languages` list)
already ran inside `filter_stage0` — a job whose JD isn't written in a
language the candidate reads never reaches Stage 1, even if it doesn't
explicitly state a language *requirement*.

`filter_stage0`'s output is already deduped by content — the same JD
reposted under multiple LinkedIn URLs (companies/agencies do this to look
"freshly posted") collapses into one job carrying `_duplicate_job_urls`
(the other URLs). Don't judge those separately; the representative's
verdict applies to all of them — pass `duplicate_job_urls` through
unchanged into that job's entry in the Stage 5 results list, so `finalize`
marks every alias URL as seen too, and the digest/rejected files show one
entry (noting the repost count) instead of N identical ones. `dropped
duplicate_content` (in the stats file) counts how many were merged — the
`merged_duplicates` figure — expect this to be small (~2% historically),
not a major lever.

Also carry forward, per job, the `matched=True` entries from `_t1_blocks`:
`t1_matched_keywords` (their `matched_keywords`, deduped) and
`t1_matched_sentences` (their `sentence` text) into that job's Stage 5
results entry. This is the audit trail for keyword-matching quality — it
persists in `seen.db`, not a separate report file, so it's queryable any
time (`sqlite3 seen.db "SELECT title, t1_matched_keywords,
t1_matched_sentences FROM seen_jobs WHERE status='passed'"`) instead of
only existing for the duration of one run.

Before continuing, drop any job whose `job_url` is already in `seen.db`
(query it with `sqlite3 seen.db "SELECT job_url_hash FROM seen_jobs"` and
compare against `ftja.state.hash_url(job_url)` — or just let jobs pass
through to Stage 1/2 and skip already-verdicted ones there; either is fine,
but do not re-spend LLM calls on a job already recorded as
passed/stage1_fail/stage2_fail). Count how many were dropped this way —
that's `already_seen_skipped` for the stats file.

## 3. Stage 1 — cheap-model sentence-block judgment

Prompt template: `stage1_prompt.md`. Each job in the Stage 0 output carries
a `_t1_blocks` field: a list of `{index, s_id, sentence, matched}` — render
each as `<s_id>[T1 or ctx] <sentence>` and fill the template's
`{title}`/`{company}`/`{blocks}`/`{rubric_pass_fail_excerpt}` placeholders
(the last one is `rubric.md`'s "Pass"/"Fail" sections only, not the whole
file — Stage 2 reads the whole file).

Call the `Agent` tool with `model: "haiku"` for each job (batch independent
jobs into one message so they run in parallel — see the Agent tool's own
guidance on parallel calls). The subagent returns ONLY:

```json
{"verdict": "pass" | "fail", "evidence_sids": ["S3", "S4"]}
```

No hallucinated quotes — evidence is by S-id only (this mirrors
`job_evaluator.py:968-1010`'s design, ported here without the Supabase
plumbing). Collect the `pass` jobs as the Stage 1 output list. Add each
call's `usage.subagent_tokens` to the running token total.

## 4. Stage 2 — mid-model resume/portfolio judgment

Prompt template: `stage2_prompt.md`. For each Stage 1 `pass` job, call the
`Agent` tool with `model: "sonnet"`, filling
`{title}`/`{company}`/`{location}`/`{full_description}` — the subagent
reads `rubric.md` (full file) and `profile/summary.md` itself via the Read
tool (don't paste their content into the prompt; the template only carries
the JD text and identifiers). If `profile/summary.md` doesn't exist yet,
STOP and tell the user to run `/ftja-setup` (or re-run it) — don't fall
back to reading the raw CV/portfolio, that reintroduces the token cost this
file exists to avoid. If a description is somehow empty, skip Stage 2 for
that job and record it as `stage1_fail` with a note, don't fabricate content.

The subagent returns:

```json
{"verdict": "pass" | "fail", "evidence_sentences": ["<verbatim quote>", ...], "reasoning": "<one paragraph>"}
```

Add each call's `usage.subagent_tokens` to the running token total.

## 5. Finalize

Assemble a single JSON list, one entry per job touched this run (every job
that reached Stage 0 output, regardless of where it stopped):

```json
[
  {"job_url": "...", "title": "...", "company": "...", "location": "...",
   "status": "stage1_fail" | "stage2_fail" | "passed",
   "stage_reached": 1 | 2,
   "verdict": "...", "evidence_sentences": [...], "reasoning": "...",
   "duplicate_job_urls": ["...", "..."],
   "t1_matched_keywords": ["...", "..."], "t1_matched_sentences": ["...", "..."]}
]
```

`duplicate_job_urls`, `t1_matched_keywords`, `t1_matched_sentences` are all
optional — carry them straight through from that job's Stage 0 fields when
present (see step 2). Omit entirely rather than writing an empty list.

Also write a small stats file:

```json
{"ts_start": "<ISO8601 UTC from step 0>", "duration_seconds": <now - start_time>,
 "total_tokens": <running total>, "scraped_count": <merged count from step 1>,
 "already_seen_skipped": <count from step 2>, "stage0_dropped": <dropped dict from step 2>,
 "criteria_snapshot": <snapshot from step 0>}
```

Write both to temp files and run:

```
venv/bin/python -m ftja.finalize --results /tmp/ftja-results.json --stats /tmp/ftja-stats.json --db seen.db --out-dir .
```

This records everything in `seen.db` (including title/company/location —
the full JD text is never persisted, only these identifying fields), writes
`digest-YYYY-MM-DD.md` (passed) and `rejected-YYYY-MM-DD.md` (Stage1/2
fails, with their reasoning — Stage1/2 verdicts are free-text LLM judgment,
not a fixed category the way Stage0's dropped-reason counts are, so this is
where "why did X fail" stays readable after the run ends instead of only
existing in the moment), appends `run.log` and `runs.jsonl`, and fires the
macOS notification — do this even if 0 jobs passed (silent failure is
exactly what we're avoiding).

## 6. Release the lock and report

```
venv/bin/python -m ftja.lock release .ftja.lock
```

If this was invoked interactively (not from launchd), tell the user the
digest path, the pass count, and that this run now shows up in the review
server's Results tab (`venv/bin/python -m ftja.server` if it's not already
running) — nothing to regenerate, it reads `runs.jsonl`/`seen.db` live.
