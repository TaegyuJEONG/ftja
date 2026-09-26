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
stale or out-of-order actions, writes the next `onboarding-state.json` state
atomically, and records every web action in `onboarding-action.json`. The action
types are `set_profile_sources`, `confirm_profile_sources`,
`set_profile_summary`, `confirm_profile_summary`, `confirm_profile`,
`confirm_stage0`, `answer_rubric_question`, and `confirm_rubric`. The agent/LLM may produce drafts, but it must not choose
the next stage or promote unconfirmed data into active configuration.


The only onboarding order is:

1. **Profile** — wait for resume and portfolio files, write a concise
   `profile/summary.md`, get an explicit profile-summary confirmation, then
   propose editable job titles. Propose location from
   the user's country as a starting point, but use **Europe** in the public
   example; propose non-remote and postings from the past 24 hours as defaults.
   Do not save these defaults until the user confirms them in the web card.
2. **Rubric** — propose keywords, readable languages, and exclude words for
   Stage 0. After confirmation, the web asks exactly one judgment question at a
   time (clear yes, dealbreakers, then preferences). Wait for each answer before
   proceeding. The web then shows one generated rubric for a final confirmation;
   do not show separate Stage 1 or Stage 2 confirmation cards.
3. **Run** — after the rubric is confirmed, tell the user to run `/ftja-run` in
   the agent chat. The existing Results/Pipeline surfaces remain the source of
   truth and update from local files.
4. **Learn** — record apply/skip decisions and reasons in Results. On a later
   interactive run, propose rubric changes for approval; never silently apply
   them.

A stage is complete only after the corresponding local action has been read and
its next state has been written. The browser must be safe to reload: it should
resume from `onboarding-state.json`, not localStorage or a guessed step.

## Mandatory first-turn order

Before asking any onboarding question or reading profile material, follow this
order exactly. A shell `cd` changes one command's working directory; it does
**not** attach the Claude Code session to the workspace.

1. Resolve the absolute path of the cloned FTJA repository from the actual
   session context. Preserve the user's path and capitalization exactly; do
   not guess between similarly named folders.
2. Call `mcp__ccd_directory__change_directory` with that exact absolute path.
   Continue only when the result confirms `Folder access granted`. If the tool
   is unavailable or permission is denied, stop and report that the session
   folder is not connected; do not continue with a scratch workspace.
3. Initialize or resume `onboarding-state.json` from that connected folder.
4. Start the local server from that folder if `http://127.0.0.1:8765/` is not
   responding. Verify it with `curl` before proceeding.
5. Open the viewer immediately. Call
   `mcp__Claude_Browser__preview_start` with `http://127.0.0.1:8765`, then call
   `mcp__Claude_Browser__get_page_text` to verify the page. On macOS also run
   `open http://127.0.0.1:8765` so the user gets a visible browser without
   needing to click an `Open` card. Do not describe the viewer as open until a
   navigation/page-text check succeeds.
6. Only after the viewer is ready, tell the user to add source materials in
   the web view. Do not ask for a path in chat.

If the Claude client header still says `No folder` after a successful
`Folder access granted` result, report that the client label did not refresh;
the internal working directory and the visible client association are separate
states. Never claim the header says FTJA unless it actually does.

## Web-driven profile sources

The web view is the only source-material input surface. Never use
`AskUserQuestion` to ask where a resume or portfolio lives, and never ask
for an absolute source path in chat. Tell the user to add one or more files
and/or folders in the web view;
the web action records local paths, not file contents. After the action appears,
read every path from `onboarding-state.json`, then inspect/copy the selected
sources into the gitignored `profile/` folder as needed. This folder is a private
local cache and derived workspace for repeatable daily runs; it is not an upload,
public snapshot, or Git commit. Do not require a resume/portfolio label: the UI
uses the selected file or folder name.

Before asking the user to choose sources, send the instruction in the active
agent chat, then start the bounded file bridge in the same setup session. In
Claude Code, use `mcp__ccd_session_mgmt__send_message` when available so the
user sees the instruction before the blocking wait begins; do not put the
instruction only in a final response and then stop the session.

```bash
venv/bin/python -m ftja.onboarding . wait-for-action --type confirm_profile_sources --timeout 900
```

Do not continue the profile interview, write a summary, or ask another source
question until that command returns `status: confirmed`. When it returns, read
every path from `onboarding-state.json` and continue in this same setup session.
If it returns `status: timeout`, tell the user to type `I uploaded my background
files, continue the setup` in the FTJA setup chat and then resume from the state
file. If the user adds more sources before confirming, re-read the full source
list before writing `profile/summary.md`.

After writing `profile/summary.md`, the web must show the summary for an explicit
`Confirm profile summary` decision. Send a short review instruction, then wait:

```bash
venv/bin/python -m ftja.onboarding . wait-for-action --type confirm_profile_summary --timeout 900
```

Only after that action returns `status: confirmed` should you propose titles,
location, remote preference, and published-within settings. After
`confirm_profile` returns, derive Stage 0 defaults from the confirmed profile and
write them before asking for review:

```bash
venv/bin/python -m ftja.onboarding . set-state --stage rubric --phase stage0 --status waiting --json '{"profile":{"criteria":{"keywords":{"tier1":["..."]},"languages":["en"],"exclude_keywords":["..."]}},"cards":{"stage0":{"keywords":["..."],"languages":["en"],"exclude_keywords":["..."]}}}'
```

The `set-state` helper records this as a draft; it does not confirm Stage 0. The
same bridge applies to every later web decision. Before waiting for a card,
send the instruction in the active agent chat, then run the matching command:

```bash
venv/bin/python -m ftja.onboarding . wait-for-action --type confirm_profile --timeout 900
venv/bin/python -m ftja.onboarding . wait-for-action --type confirm_stage0 --timeout 900
venv/bin/python -m ftja.onboarding . wait-for-action --type answer_rubric_question --timeout 900
venv/bin/python -m ftja.onboarding . wait-for-action --type confirm_rubric --timeout 900
```

Use only the command for the card currently shown. There are three sequential
`answer_rubric_question` actions; after each one, re-read the state so the next
web question remains the source of truth. After `status: confirmed`,
read the returned `action` and the full `onboarding-state.json` before writing
the next draft or message. Never assume that a changed web card reached the
setup chat without the helper result. If a later wait times out, tell the user
the matching recovery phrase shown by the web card (for example, `I confirmed
my search settings, continue the setup`) and do not claim that the chat resumed.


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
- The web source picker is the only way to provide profile sources. It accepts
  one or more files or folders and records local paths without uploading
  contents. Read those paths from `onboarding-state.json`; do not ask for them
  in chat and do not require a resume/portfolio label.

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
