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
2. If `pwd` is already that repository, the session is attached: skip this
   step (asking to move a session into the folder it is already in only
   shows the user a pointless prompt). Otherwise call
   `mcp__ccd_directory__change_directory` with that exact absolute repo
   path and require the `Folder access granted` result. A shell `cd` alone is
   not enough. If the repo is not the active workspace, stop and tell the user
   to open/connect the existing FTJA folder; never run against a scratch folder.
3. Make sure the viewer on port 8765 is THIS folder's. Every FTJA clone
   uses the same port, so a viewer that answers may be showing another
   folder's data, and this run would never appear in it.
   `curl -s http://127.0.0.1:8765/api/version` returns `project_dir`.
   - It matches `pwd`: the viewer is fine, go on.
   - No answer: start it from this folder (`venv/bin/python -m ftja.server`,
     in the background) and verify with `curl`.
   - It names another folder, or has no `project_dir` (an older viewer):
     that viewer belongs to a different clone. Stop it (`lsof
     -tiTCP:8765 -sTCP:LISTEN | xargs kill`), start this folder's as above,
     and tell the user in your report that you switched the viewer from
     that folder to this one.
4. On an interactive run, put the viewer in front of the user every time,
   whether you started it or it was already running: on macOS run
   `open http://127.0.0.1:8765`, and if `mcp__Claude_Browser__preview_start`
   is available call it with that URL too. The run is watched there; a
   viewer that is running but not on screen shows the user nothing. (On an
   unattended run, open nothing.)

Do not claim the Claude header is connected unless it visibly shows the folder.
If the directory tool confirms access but the header remains `No folder`, report
that client UI limitation separately and continue only with the confirmed repo
path.

## Keep the user informed (interactive runs)

A run takes minutes, sometimes an hour. The user must never be left
looking at a silent chat wondering whether anything is happening. Say one
short, plain line in the chat at each of these moments, in the user's
language, with the numbers you have:

- **Before scraping**: that the run has started, how many search terms and
  up to how many results each, roughly how long that will take (LinkedIn
  yields about 70 postings a minute, so 4 terms × 1000 is up to an hour;
  4 × 100 is about 6 minutes), and that they can watch it at
  `http://127.0.0.1:8765` (Results tab). If the estimate is over 15
  minutes, add that lowering "Max results per search term" in the Pipeline
  tab makes it shorter.
- **After each search term**: how many it found and how many terms remain.
  Run the scrape one search term per command so you can say this; do not
  chain all the terms into one long command that stays silent until the
  end.
- **After Stage 0**: scraped, passed, and the biggest drop reasons.
- **When Stage 1 starts and ends**: how many jobs in how many batches, then
  how many passed.
- **When Stage 2 starts and ends**: how many jobs, then pass / review / fail.

Keep each to one line. This is on top of the final report in step 6, not
instead of it.

## 0. Preflight

- **Newer version?** Run `venv/bin/python -m ftja.version check` before
  anything else in this list. It prints something and exits 0 even when
  offline; it never stops a run.
  - `up to date`, or it could not check: go on.
  - `update available`, on an **interactive** run: ask the user now, before
    the lock is taken, whether to update first — name both versions, and
    say that it takes about a minute and their own files are not touched.
    - Yes: follow the `ftja-update` skill (`.agents/skills/ftja-update/SKILL.md`)
      from its step 2. If it stops (local changes, a diverged branch),
      report why and ask whether to run on the current version instead.
      When it succeeds, this file may have changed under you: read
      `.agents/skills/ftja-run/SKILL.md` again from disk and start over
      from step 0a with what it says now. Do not ask about updating again.
    - No: go on with the installed version and repeat the `update
      available` line in your final report.
  - `update available`, on an **unattended** run: never update and never
    ask. Go on, and append the line to `run.log` so it is seen later.
  Never update once the run has started.
- Check `rubric.md` and `criteria.json` exist in the project root. If either
  is missing, STOP and tell the user to run `/ftja-setup` first — do not
  improvise a rubric yourself.
- Check the lock: `venv/bin/python -m ftja.lock check .ftja.lock`. If it
  prints `locked` (exit code 1), append one line to `run.log` ("skipped —
  already running") and STOP. Do not proceed.
- Acquire the lock: `venv/bin/python -m ftja.lock acquire .ftja.lock`.
- **Always release the lock before finishing** (`venv/bin/python -m ftja.lock release .ftja.lock`),
  including on any error path. Treat this like a try/finally.
- Start a running total of tokens spent (add each Stage1/Stage2 Agent
  call's `usage.subagent_tokens`, visible in that call's completion
  notification, as it arrives).
- **Check the criteria list**: `venv/bin/python -m ftja.verdict status`.
  `rubric-criteria.json` is the rubric as a list of single criteria with
  stable ids; Stage 2 answers per criterion and `finalize` computes the
  verdict from those answers. The user edits this list one criterion at a
  time in the viewer's Pipeline tab, which rewrites `rubric.md` from it. If
  the command prints `ok`, continue. If it prints `missing`, `stale`
  (rubric.md was edited as text since) or `invalid: ...`, rewrite the file
  from `rubric.md` yourself before going on:

  ```json
  {"rubric_version": "<venv/bin/python -c 'from ftja.state import rubric_version; print(rubric_version())'>",
   "criteria": [
     {"id": "direct_build", "kind": "pass", "group": "builder", "label": "Builds the product personally", "text": "<the rule>"},
     {"id": "internship", "kind": "fail", "label": "Internship or trainee position", "text": "<the rule>"},
     {"id": "ownership", "kind": "preference", "label": "Hands-on, zero-to-one ownership", "text": "<the rule>"}],
   "notes": {"pass": "", "fail": "", "preference": ""}}
  ```

  - `kind` is one of the rubric's three categories. `pass`: a reason the
    role is a match. `fail`: a reason to reject it, wherever the rubric
    states it — including any "Fail when ..." line under Preferences.
    `preference`: everything else; shown to the user, never decides.
  - `group` (pass only, optional): give the same group name to pass
    criteria that only count together ("both must be evidenced"). A job
    passes on any one group, or on any one ungrouped pass criterion. Most
    rubrics need no groups.
  - `label` is what the user sees on every job, next to Met / Not met /
    Triggered. Write it so it reads on its own, in at most 6 words:
    "Requires a language the candidate doesn't speak", not "Language".
  - `text` is the rule the model applies. Carry the rubric's own wording
    over, with its examples and exceptions; do not shorten it into a
    paraphrase and do not add anything the rubric doesn't say.
  - `notes`: guidance in a section that belongs to no single criterion
    (how strict to be, a Stage 1 note). Leave out "fail if nothing passes";
    the rule already does that.
  - Keep the id of every criterion that still exists, even if its wording
    changed. The viewer stores the user's per-criterion feedback under
    these ids. New ids: short snake_case.
  - Do not rewrite `rubric.md` here. Run `ftja.verdict status` again; it
    must print `ok`.
- **Old details** (interactive runs only): `venv/bin/python -m ftja.state
  prune-preview`. It changes nothing. If `worth_asking` is false, say
  nothing and go on. If it is true, tell the user, before anything else
  runs, exactly what it reports and ask whether to remove it:
  - how many jobs, judged between which dates (`jobs`, `judged_from`,
    `judged_to`, older than `older_than_days` days), and how they break
    down (`by_status`);
  - what would be removed from each of them (`removes`) and what stays
    (`keeps`), and that jobs they marked as applied are not touched;
  - the database's size now and about how much comes back
    (`database_bytes`, `bytes_freed_estimate`, in MB).
  Only on a clear yes run `venv/bin/python -m ftja.state prune` and report
  the size before and after. It cannot be undone. On an unattended run
  never prune and never ask.
- **Check for pending review decisions**: `venv/bin/python -m ftja.state
  pending-review` and `venv/bin/python -m ftja.state pending-feedback` (the
  second lists single criteria the user disagreed with in the viewer;
  "empty" below means both are). This is what replaces a live watcher — review "just
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

## 1. Start the run and scrape (Stage 0a)

Do this only after the preflight above is settled (the criteria list is
`ok`, pending reviews are handled), because it copies `criteria.json`'s
search settings and `rubric-criteria.json` for this run:

```
venv/bin/python -m ftja.live start
```

It prints `{"run_id": ..., "run_dir": ".ftja-run/<id>", ...}`. Every later
command takes that `run_dir` as `--dir`; every file of this run lives in it
(nothing goes to /tmp). From this moment the run is visible in the viewer's
Results tab, and it advances there by itself as the files below appear —
you never report progress, and you never write these files by hand.

Then scrape each search term in `criteria.json`'s `search_terms`, by its
index (0, 1, 2, ...), one after another — a separate command per term,
with a line to the user between them (see "Keep the user informed"):

```
venv/bin/python -m ftja.live scrape --dir <run_dir> --term <index>
```

Each call reads the term, location, remote-only flag, time window, cap per
term and exact-phrase setting from the run's own snapshot, scrapes in
batches of 10, and updates that term's count after every batch.

## 2. Deterministic filter (Stage 0b)

```
venv/bin/python -m ftja.live stage0 --dir <run_dir>
```

Merges the scrapes (one entry per `job_url`), runs `ftja.filter_stage0`,
and prints `{"scraped": N, "passed": M, "dropped": {...}}`. `M` is your
coverage number for this run (compare it, over time, against what
LinkedIn's own search UI shows for the same term — that's the Acceptance
Criteria #2 check, not something to automate here). Location is not part
of Stage 0 — it's already deterministic at scrape time.

What the filter does, all in code with no LLM call: drops a JD not written
in a language the candidate reads (langdetect against `criteria.json`'s
`languages`); drops a JD that plainly requires a language outside that list
("Fluent in German and English"), keeping anything ambiguous ("German is a
plus") for Stage 2; requires a tier-1 keyword; applies exclude keywords;
collapses the same JD reposted under several URLs into one job; and, last,
drops jobs an earlier run already judged (they're in `seen.db`), so no LLM
call is spent on the same posting twice. The
language-requirement drops are listed in the rejected file with the
sentence that triggered each, and are not recorded as seen.

## 3. Stage 1 — cheap-model sentence-block judgment

```
venv/bin/python -m ftja.live prepare-stage1 --dir <run_dir>
```

Writes one filled prompt per Stage 0 survivor to `<run_dir>/s1/<n>.txt`
(template: `stage1_prompt.md`; the job's keyword sentences with their
neighbours, plus `rubric.md`'s Pass/Fail sections). It prints how many jobs
there are; they are numbered `0 .. jobs-1`.

Call the `Agent` tool with `model: "haiku"`, giving **each subagent a batch
of up to 10 job numbers**: it reads `<run_dir>/s1/<n>.txt` for each, judges
each job on its own, and writes each answer to `<run_dir>/s1/<n>.out.json`.
The answer is ONLY:

```json
{"verdict": "pass" | "fail", "evidence_sids": ["S3", "S4"], "reason": "<one sentence, at most 15 words>",
 "blocks": [{"sid": "S3", "why": "<12 words max>"}, ...]}
```

`reason` is shown to the user next to every job Stage 1 turned down, so it
must say what decided it, not "no pass criterion met". `blocks` has one
entry per [T1] sentence: what the model made of that sentence. The user
sees it under each matched sentence, next to the keyword that matched.

Tell the subagent to work in as few steps as it can: read all of its
prompt files in one step (the Read calls together), decide every job, then
write all of its answers in one step (the Write calls together). Do NOT
ask it to finish one job before reading the next: every extra step is a
round trip, and in a measured run that made a batch of 7 take 262 seconds
instead of 83 for the same verdicts. The viewer's Stage 1 count therefore
moves a batch at a time.

Batching is deliberate: every subagent carries a fixed overhead of roughly
45k tokens, so one job per subagent cost about 5× more for the same verdicts
in a measured run. Stage 1 inputs are short keyword excerpts, so a batch of
10 stays small.

Run the batches in parallel by putting several `Agent` calls in one message,
but **no more than 20 per message** — that is the concurrent subagent limit,
and calls beyond it fail. Send the next wave after the previous one returns.

Ask each subagent to also reply with one line per job (`N <json>`). Writes
can fail on transient permission-check errors; when an output file is
missing, write it yourself from the reply. If neither exists, rerun that
job. Evidence is by S-id only, so there are no quotes to hallucinate. Add
each call's `usage.subagent_tokens` to the running token total.

## 4. Stage 2 — mid-model resume/portfolio judgment

If `profile/summary.md` doesn't exist, STOP and tell the user to run
`/ftja-setup` — don't fall back to reading the raw CV/portfolio, that
reintroduces the token cost the summary exists to avoid.

```
venv/bin/python -m ftja.live prepare-stage2 --dir <run_dir>
```

Run it once Stage 1 is complete; Stage 2 starts only then, so the stages
run and show one after another. It refuses, naming the job numbers, while
any Stage 1 answer is missing or unreadable — fix those first. Otherwise
it writes one filled prompt per Stage 1 pass to `<run_dir>/s2/<n>.txt` (template: `stage2_prompt.md`, with
the run's criteria list and the full JD) and prints how many there are,
numbered `0 .. jobs-1`. A job whose description is empty is left out and
recorded as a Stage 1 fail with a note.

For each one, call the `Agent` tool with `model: "sonnet"` — **one job per
subagent; do not batch Stage 2.** In a measured comparison, borderline jobs
that failed in every single-job run passed when judged in a batch alongside
similar postings. Stage 2 is the final verdict, so it stays single-job. The
subagent reads `<run_dir>/s2/<n>.txt` and follows it (it reads `rubric.md`
and `profile/summary.md` itself), writes its JSON answer to
`<run_dir>/s2/<n>.out.json`, and returns the same JSON in its reply so a
failed write does not lose it.

The answer has one result per criterion and no overall verdict, plus what
the employer asks for, a one-line company description and notes:

```json
{"company_line": "...",
 "criteria": [{"id": "direct_build", "result": "met", "quote": "<verbatim from the JD>", "why": "<one short sentence>"}, ...],
 "employer_requirements": [{"kind": "must", "label": "...", "quote": "...", "candidate": "meets", "why": "..."}, ...],
 "notes": [{"text": "...", "quote": "..."}]}
```

Do not decide pass/fail from this yourself, and do not edit, drop or "fix"
any entry: copy a reply into the output file exactly as returned. Code
checks each quote against the JD and computes `passed` / `review` /
`stage2_fail` by a fixed rule (`ftja/verdict.py`): a result the model gave
without a quote that really appears in the JD is downgraded to `unclear`, a
criterion the model skipped counts as `unclear`, and an employer
requirement or note whose quote isn't in the JD is dropped. The viewer
shows each job's card the moment its file lands.

Run these calls in parallel waves of at most 20 per message, as in Stage 1.
Add each call's `usage.subagent_tokens` to the running token total.

## 5. Finalize

```
venv/bin/python -m ftja.live results --dir <run_dir>
```

It refuses, naming the job numbers, while any Stage 1 or Stage 2 answer is
missing or isn't valid JSON with a `criteria` list — rerun those jobs and
call it again. Otherwise it writes the run's `results.json` and
`stats.json`. Then:

```
venv/bin/python -m ftja.finalize --run-dir <run_dir> --total-tokens <running total>
```

This computes each Stage 2 job's verdict against the criteria list copied
at the start of the run, records everything in `seen.db` (title/company/
location and the Stage 1 reason for every job; for jobs that reached Stage
2 also the per-criterion results, the employer's requirements, notes and
the full JD text), writes `digest-YYYY-MM-DD.md` (passed, then the ones to
review) and `rejected-YYYY-MM-DD.md` (Stage1/2 fails, with their reasoning,
so "why did X fail" stays readable after the run ends), appends `run.log`
and `runs.jsonl`, fires the macOS notification, and marks the run finished
so the viewer swaps the live view for the finished run — do this even if 0
jobs passed (silent failure is exactly what we're avoiding).

## 6. Release the lock and report

```
venv/bin/python -m ftja.lock release .ftja.lock
```

If this was invoked interactively (not from launchd), tell the user the
digest path, the pass and review counts (`finalize` prints both; "review"
means the posting was ambiguous on a deciding point and the user should
read it), and that the run is in the viewer's Results tab.
