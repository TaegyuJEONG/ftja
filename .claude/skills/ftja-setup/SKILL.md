---
name: ftja-setup
description: One-time (but re-invocable) onboarding for FTJA — interviews the user and creates local rubric.md + criteria.json. Invoke when rubric.md/criteria.json don't exist yet, or when the user asks to "set up again" / "reset rubric" / "redo onboarding".
---

# FTJA setup

Interview the user conversationally (not a rigid form — follow up, don't
just fire a checklist) to produce two files at the project root. Do not
guess at answers; if the user is vague, ask a follow-up.


## File-driven onboarding contract

The local web onboarding is a viewer and decision surface, not a wizard. Never
advance it with a `Next` button and never ask the user to repeat structured
answers in chat. The setup chat is the driver; it writes the local files and
`onboarding-state.json`, while the browser polls that state and shows only the
current stage.

Use the repository helper for every transition:

```bash
venv/bin/python -m ftja.onboarding . init
venv/bin/python -m ftja.onboarding . message agent "..."
venv/bin/python -m ftja.onboarding . set-state --stage profile --phase profile_summary --status waiting --json '{"profile":{"summary":"...","sources":[...]}}'
```

The web sends decisions to `POST /api/onboarding-action`. The server is the
onboarding state machine: it validates the current stage and revision, rejects
stale or out-of-order actions, and writes the next `onboarding-state.json`
state atomically. The action types are `set_profile_sources`,
`set_profile_summary`, `confirm_profile`, `confirm_stage0`, `confirm_stage1`,
and `confirm_stage2`. The agent/LLM may produce drafts, but it must not choose
the next stage or promote unconfirmed data into active configuration.

The only onboarding order is:

1. **Profile** — wait for resume and portfolio files, write a concise
   `profile/summary.md`, then propose editable job titles. Propose location from
   the user's country as a starting point, but use **Europe** in the public
   example; propose non-remote and postings from the past 24 hours as defaults.
   Do not save these defaults until the user confirms them in the web card.
2. **Rubric** — propose keywords, readable languages, and exclude words for
   Stage 0; after confirmation, ask about dealbreakers and preferences using the
   profile summary, then show Stage 1 and Stage 2 cards. Use plain model labels:
   `rough model` for the evidence-only pass and `middle model` for the full-job
   judgment. Do not expose vendor names in onboarding UI.
3. **Run** — after the rubric is confirmed, tell the user to run `/ftja-run` in
   the agent chat. The existing Results/Pipeline surfaces remain the source of
   truth and update from local files.
4. **Learn** — record apply/skip decisions and reasons in Results. On a later
   interactive run, propose rubric changes for approval; never silently apply
   them.

A stage is complete only after the corresponding local action has been read and
its next state has been written. The browser must be safe to reload: it should
resume from `onboarding-state.json`, not localStorage or a guessed step.

## What you need to learn

**For `criteria.json` (Rubric stage, after Profile — Stage 0 deterministic, used by `ftja/scrape.py` and
`ftja/filter_stage0.py`):**
- `search_terms`: list of LinkedIn search strings (e.g. `["Product Manager", "Founding PM"]`)
- `location`, `is_remote`, `hours_old`, `results_wanted` — this is the ONLY
  place location is controlled. If the candidate wants roles in more than
  one region (e.g. "Europe, or Singapore/Dubai if visa-sponsored"), that
  needs multiple scrape locations (a loop over `location` values in
  `/ftja-run`, or a `search_terms` x `locations` cross product) — say so
  explicitly if the candidate mentions more than one region, don't silently
  scope to just the first one they said.
- `exclude_keywords`: a blunt bare-word exclusion list — if any of these
  words appear ANYWHERE in the JD (title or body), the job is dropped, no
  phrase-matching ("fluent in X", "X required") involved. Ask what to put
  here — it doesn't have to be languages; a tool, a company, an industry,
  anything the candidate wants to hard-exclude works. Ask: "is there anything
  — a language or otherwise — that should get this posting dropped outright
  if it appears?"
- `languages`: ISO 639-1 codes (e.g. `["en","fr","ko"]`) of languages the
  candidate can actually work in. A JD whose own text isn't detected as one
  of these gets dropped at Stage 0 (via `langdetect`) even if it never
  explicitly states a language *requirement* — ask "what languages can you
  actually read a posting in?"
- `keywords.tier1`: the keyword phrases that must appear in a JD sentence
  for it to even reach Stage 1. Two useful sources, both matter — ask about
  each separately, don't let one crowd out the other:
  - **Concrete AI-builder tool names**: Cursor, Replit, v0.dev, Lovable,
    Bolt.new, Windsurf, Antigravity, Base44, Supabase, Firebase, Vercel,
    Claude Code, Gemini CLI, Codex, OpenAI API, "vibe coding" — a JD naming
    a specific tool is a much stronger, higher-precision signal than a
    vague topic word, and is easy to under-cover if you only ask "what
    words describe the role you want" (a real gap found in FTJA's first
    keyword list: it leaned entirely on phrase/concept terms like "MVP" /
    "prototyping" and missed almost all of these).
  - **Role/phrase concepts specific to the candidate's target roles**: e.g.
    "0 to 1", "solo founder", "entrepreneur in residence", "venture
    builder" — ask for concrete phrases, not vague topics ("prototyping",
    not just "AI").

**For `rubric.md` (Rubric stage, after Profile — read by the judgment workers):**
- What makes a role a clear yes for them (be specific — role type, seniority,
  what they'd actually be doing day to day)
- Dealbreakers (things that auto-fail regardless of everything else)
- Preferences that matter but aren't dealbreakers (comp range, remote policy,
  company stage, etc.)
- Ask where their resume/portfolio files live — copy or symlink them into
  `profile/` (already gitignored) rather than leaving paths to reference
  elsewhere.

## Write the files

`criteria.json` — structured, machine-read:
```json
{
  "search_terms": ["..."],
  "location": "...",
  "is_remote": true,
  "hours_old": 72,
  "results_wanted": 100,
  "exact_phrase_search": true,
  "exclude_keywords": ["..."],
  "languages": ["en", "fr", "ko"],
  "keywords": {"tier1": ["...", "..."]}
}
```

`exact_phrase_search` (default `true`): whether each `search_terms` entry is
sent to LinkedIn as an exact quoted phrase (e.g. `"Founder in Residence"` —
narrower, won't match a JD where those words appear scattered apart) or
as-is (broader). Don't ask about this unless the candidate brings up
search-result volume/coverage — `true` is the sane default.

`rubric.md` — free-form markdown, written in the user's own words as much as
possible (this is what the Stage 2 subagent reads verbatim — write it as
instructions TO that subagent, e.g. "Pass if..." / "Fail if..." sections),
covering: clear-yes signals, dealbreakers, preferences.

`profile/summary.md` — a condensed distillation of the resume/portfolio you
just read (a few hundred words: role history highlights, key skills/tools,
notable achievements — whatever a judge would need to assess fit). **Not
optional**: Stage 2 reads this file on every job, every day, instead of the
raw resume/portfolio — the first real run measured ~44-86k tokens/call
reading the full files directly, and this is the fix. Write it yourself
from what you already read in `profile/`, don't just copy-paste the raw
files. If the candidate's background changes materially later, this file
should be regenerated (mention that if `/ftja-tune` ever touches profile
info).

After writing all three, do not git-add or commit the user's criteria, rubric, profile, or run data. These files are intentionally gitignored in the public FTJA workspace. If the user wants version history, they can opt into a separate private repository.

Start `venv/bin/python -m ftja.server` from the FTJA folder when it is not already running, then open
`http://127.0.0.1:8765` automatically if possible (any browser — Chrome, Edge, or Safari) — the
Pipeline tab there shows the scraping criteria, keywords, Stage0 filter
rules, and the Stage1/Stage2 prompts in plain English (reads `criteria.json`/
`rubric.md` live, so it's never stale). Have them look at that before
running anything.

Tell the user both `rubric.md`/`criteria.json` are ready to review/edit by
hand if they want, and that `/ftja-run` is the next step (recommend a
manual run first — M1 in SPEC.md — before setting up the launchd schedule).
Once a run produces passed/failed jobs, the same server's Results tab is
where they record apply/skip decisions — see `/ftja-review` for how those
decisions feed back into criteria.json/rubric.md.
