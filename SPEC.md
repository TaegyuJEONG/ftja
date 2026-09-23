---
spec_created_at: 2026-09-17
spec_source: brainstorm session (JobSpyProject → FTJA pivot)
status: approved-draft
---

# FTJA — personal job-search Skill: 3-stage judgment funnel + local rubric, local schedule automation

## Context

JobSpyProject today is a centralized pipeline for a public job board — a
shared Supabase DB, a global tier1 keyword list (tuned for a single user),
curated by an admin. It turned out that profiles vary too much to fit under
one "Product Builder" rubric, operating costs (egress etc.) are significant,
and curation itself is inherently something that has to differ per person.
The public board is being shut down.

The same problem was attempted once before (`fine-tune-job-agent`
prototype, ~2025 — Chrome extension + Pinecone vector matching +
Firebase/Cloud Run hosting). That architecture reproduced exactly the
operational burden being avoided here (hosting, vector DB, API costs), and
was abandoned after several rewrites of the semantic-parsing layer, leaving
behind only traces (`SEMANTIC_PARSING_IMPLEMENTATION_PLAN.md`,
`app.js.backup-before-semantic*`).

FTJA (Fine Tune Job Agent — name carried over) is a retry that incorporates
that lesson: no hosting, no vector DB, runs locally for one person, uses the
Claude Code agent itself as the judgment engine.

## Current State

- JobSpyProject's related logic (`job_evaluator.py`, `text_index.py`,
  `app_db.py`) is used **for reference only**. FTJA is a new repo with no
  code dependency/import from it.
- Design to reuse (pattern only, code rewritten from scratch):
  - `job_evaluator.py:968-1010` (`evaluate_phase1_llm_for_user`) — the
    structure of handing the model a tier1-keyword sentence ±1 block and
    getting back only an S-id evidence index (prevents hallucination,
    skips the LLM call entirely when there's no tier1 match)
  - `text_index.py` — the logic for extracting a keyword sentence plus its
    surrounding context block
  - `app_db.py`'s jobspy wrapper (login-free guest scraping) + its
    validated rate-limiting parameters
  - the `api_server_launchagent` pattern (per memory) — scheduling via a
    macOS LaunchAgent instead of a `daemon.py`, no custom process
    management needed

## Proposed Change

```
/ftja-setup (skill, one-time, re-invocable)
  → gathers keywords/location/language + resume/portfolio paths +
    dealbreakers/preferences through conversation
  → generates rubric.md (natural-language judgment criteria) +
    criteria.json (structured filters), git commit

/ftja-run (skill, manual or launchd-triggered)
  0. scrape LinkedIn via jobspy guest access (deterministic, no LLM)
  1. filter by keywords/location/language via criteria.json
     (deterministic, no LLM)                                     [N jobs]
  2. survivors → Agent(model:"haiku"): judge the keyword sentence
     ±1 block, S-id evidence                                     [M jobs]
  3. survivors → Agent(model:"sonnet"): judge full JD + rubric.md +
     resume/portfolio                                            [K jobs]
  4. record per-job_url_hash status in seen.db (SQLite) to prevent
     re-judging duplicates
  5. generate digest-YYYY-MM-DD.md + macOS notification
     ("K new postings")

/ftja-tune (skill, re-invocable anytime)
  → detects a new preference/criterion mentioned mid-conversation →
    asks "should I add this to the judgment criteria?"
  → asks back if it conflicts with the existing rubric → edits
    rubric.md → git commit
```

### Implementation Details

**Local file structure** (FTJA repo root):
```
rubric.md          # natural-language judgment criteria, git-tracked
criteria.json       # {keywords:{tier1:[...]}, location:[...], languages:[...]}, git-tracked
profile/            # references to resume/portfolio paths (original files or links), .gitignore
seen.db             # SQLite: seen_jobs(job_url_hash PK, job_url, status, stage_reached, verdict, last_seen_at) — .gitignore
digest-*.md         # daily results — .gitignore
run.log             # run log — .gitignore
.ftja.lock          # PID lock file to prevent concurrent runs
```
`.gitignore` excludes `seen.db`, `digest-*.md`, `run.log`, `profile/` —
only `rubric.md`/`criteria.json` are kept in git history (so personal
info/JD text never gets committed).

**Stage1/2 subagent contract**: both return only structured JSON,
`{verdict: pass|fail, evidence_sids: [...], reasoning: str}`. Stage2
includes the verbatim evidence sentences instead of `evidence_sids` (so
they can be surfaced directly in the digest).

**digest-YYYY-MM-DD.md format**: for each of the K postings that passed —
title/company/link + Stage2's **evidence sentences (verbatim quotes) + a
one-line reasoning**.

**Concurrency guard**: `/ftja-run` checks `.ftja.lock` on start — if the
PID is still alive, skip and just log it; otherwise create a lock with its
own PID and remove it on exit (trap).

**Scheduling**: `~/Library/LaunchAgents/com.ftja.run.plist`,
`StartCalendarInterval` at 09:00, headless call to
`claude --print "/ftja-run"`. Needs a non-interactive permission mode
configured in advance to avoid getting blocked on a permission prompt (to
be finalized during implementation). If the laptop is asleep, that day is
just skipped (no separate wake configuration — out of scope).

**Failure detection**: appends to `run.log` on every run, and fires a
macOS notification every single time regardless of success/failure/zero
results (`osascript -e 'display notification'`) — no silent failures.

## Acceptance Criteria

1. After running `/ftja-setup`, `rubric.md` (non-empty natural-language
   criteria) + `criteria.json` (valid JSON, at least one tier1 keyword)
   are generated and committed to git.
2. Running `/ftja-run` manually with saved search terms — the total
   result count jobspy returns from LinkedIn matches Stage0's input N
   exactly (no drops, verified with at least 3 search terms).
3. Manually reviewing a 20-job sample of Stage1 results (N→M), the
   Stage1 verdict matches the user's own judgment against criteria.json
   closely enough to be convincing.
4. Stage2 results (M→K) are generated into digest.md daily, including
   evidence sentences + reasoning.
5. The same job_url is not re-evaluated/re-surfaced on the next run
   (dedup via seen.db works).
6. Editing rubric.md via `/ftja-tune` produces a new git commit, and
   adding a conflicting criterion surfaces a confirmation question first.
7. **[M2]** launchd runs `/ftja-run` unattended at 09:00 for at least 2
   consecutive days, leaving a run.log entry + macOS notification every
   time (covering success/failure/zero-results alike).
8. `.gitignore` excludes `seen.db`/`digest-*.md`/`profile/`/`run.log` so
   no personal info or JD text ever lands in git history.

## Milestones

- **M1 (manual validation)**: one `/ftja-setup` run + repeated manual
  `/ftja-run` invocations to validate funnel reliability (over a few
  days). No LaunchAgent.
- **M2 (automation)**: once M1 proves reliable, install the LaunchAgent,
  verify unattended runs + notifications.

## Testing Plan

| Layer | What | Count |
|-------|------|-------|
| Smoke (manual) | One full-loop run with a small search term, check rubric/criteria/seen.db/digest shape | 1 |
| Component | jobspy wrapper returns title/company/url/description | 1 |
| Component | Stage0's deterministic filter correctly excludes location/language mismatches | 1 |
| Component | `.ftja.lock` blocks concurrent runs | 1 |
| Integration | launchd unattended for 2 consecutive days + logs/notifications fire | 1 |

## Rollback Plan

- rubric.md edited incorrectly → `git log rubric.md` → `git checkout <previous-commit> -- rubric.md`
- launchd misbehaving → `launchctl unload ~/Library/LaunchAgents/com.ftja.run.plist` then delete the plist,
  fall back to M1 (manual runs)
- seen.db state gets tangled → delete the file (it regenerates; rubric/criteria are unaffected — only dedup resets)

## Effort Estimate

| Component | Time |
|---|---|
| Repo scaffolding + porting the jobspy wrapper | 2h |
| Stage0 deterministic filter | 1h |
| Stage1 skill (S-id block judgment) | 2h |
| Stage2 skill (rubric + resume/portfolio judgment) | 2h |
| Onboarding skill (`/ftja-setup`) | 2h |
| Rubric-editing skill (`/ftja-tune`) | 2h |
| Local state (seen.db) + digest generation | 1h |
| LaunchAgent plist + lock + notifications | 1h |
| **Total (build)** | **~13h** |
| M1 manual validation period | usage-dependent, separate |

## Files Reference (new repo, proposed structure)

| File | Role |
|------|------|
| `ftja/scrape.py` | jobspy wrapper (references app_db.py, no dependency on it) |
| `ftja/filter_stage0.py` | deterministic keyword/location/language filter |
| `.claude/skills/ftja-setup/SKILL.md` | onboarding skill |
| `.claude/skills/ftja-run/SKILL.md` | main run skill (invokes Stage1/2 subagents) |
| `.claude/skills/ftja-tune/SKILL.md` | rubric-editing skill |
| `rubric.md`, `criteria.json` | judgment criteria (git-tracked) |
| `com.ftja.run.plist` | LaunchAgent definition |

## Out of Scope

- Public job board / publishing / insight curation (separate, untouched by this spec)
- Job boards other than LinkedIn, LinkedIn-login-based scraping
- General chat/mobile (non-Claude-Code) support, MCP multi-client — moving
  scraping/scheduling to a hosting layer is a separate problem, a separate
  issue

## Future Vision (deliberately deferred, with reasons)

- **Implicit/automatic rubric inference** (mining the whole conversation
  by hand and auto-applying it) — the `fine-tune-job-agent` prototype
  collapsed through several rewrites at exactly this point (automatic
  semantic-parsing inference). Revisit only after `/ftja-tune` (explicit
  confirmation gating) has proven stable.
- Managing multiple rubric profiles/pivots (PM→EIR→AI venture-building
  etc.), visualizing judgment evidence/the pipeline, a "soul tool list" —
  design these only after one static rubric has run stably for a few
  weeks.
- **Distributing to other users**: right now the skill is only recognized
  when the project folder itself is open (a tradeoff traded for local-file
  transparency). Extending this would need packaging via Claude Code's
  plugin/marketplace mechanism so the folder doesn't need to be opened
  directly (this repo's `gstack`/`product-management` skills already work
  that way) — but that would require redesigning where rubric.md/seen.db
  live (a fixed path vs. specified at install time). Setting up the
  jobspy/venv local runtime environment is a one-time-per-user step
  regardless of distribution method.

## Related

- Reference (not a dependency): `job_evaluator.py`, `text_index.py`,
  `app_db.py`, `daemon.py` (JobSpyProject, `/Users/taegyujeong/JobSpyProject`)
- Reference: `fine-tune-job-agent-main.zip` (2025 prototype, a failure
  case — code not reused; be careful when referencing it since it
  contains credentials like `the_real_key.p12` — never put this zip
  itself into the FTJA repo)
