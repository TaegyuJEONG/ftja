---
name: ftja-setup
description: One-time (but re-invocable) onboarding for FTJA — interviews the user and writes rubric.md + criteria.json. Invoke when rubric.md/criteria.json don't exist yet, or when the user asks to "set up again" / "reset rubric" / "redo onboarding".
---

# FTJA setup

Interview the user conversationally (not a rigid form — follow up, don't
just fire a checklist) to produce two files at the project root. Do not
guess at answers; if the user is vague, ask a follow-up.

## What you need to learn

**For `criteria.json` (Stage 0, deterministic — used by `ftja/scrape.py` and
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
    Codex, Gemini CLI, Codex, OpenAI API, "vibe coding" — a JD naming
    a specific tool is a much stronger, higher-precision signal than a
    vague topic word, and is easy to under-cover if you only ask "what
    words describe the role you want" (a real gap found in FTJA's first
    keyword list: it leaned entirely on phrase/concept terms like "MVP" /
    "prototyping" and missed almost all of these).
  - **Role/phrase concepts specific to the candidate's target roles**: e.g.
    "0 to 1", "solo founder", "entrepreneur in residence", "venture
    builder" — ask for concrete phrases, not vague topics ("prototyping",
    not just "AI").

**For `rubric.md` (Stage 1/2 — read by the Agent subagents at judgment time):**
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

After writing all three:

```
git add rubric.md criteria.json
git commit -m "setup: initial rubric and criteria"
```

(`profile/summary.md` is gitignored along with the rest of `profile/` — not committed.)

Tell the user to run `venv/bin/python -m ftja.server` and open
`http://127.0.0.1:8765` (any browser — Chrome, Edge, or Safari) — the
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
