---
name: ftja-setup
description: One-time (but re-invocable) onboarding for FTJA — interviews the user and creates local rubric.md + criteria.json. Invoke when rubric.md/criteria.json don't exist yet, or when the user asks to "set up again" / "reset rubric" / "redo onboarding".
---

# FTJA setup

Interview the user conversationally (not a rigid form — follow up, don't
just fire a checklist) to produce two files at the project root. Do not
guess at answers; if the user is vague, ask a follow-up.

This runs in two turns. Turn 1 connects the workspace and opens the web view
on its own, then stops and asks one question. Turn 2 (triggered by the
user's reply) creates the native checklist and drives the actual interview.
Do not collapse these into one turn — see "Why two turns" below.

## Turn 1: connect and open the web view

Do this without asking the user anything mid-way, then end the turn with
exactly one question. Do not check for native task tools, create the
checklist, or start any `wait-for-action` call in this turn.

1. Resolve the absolute path of the cloned FTJA repository from the actual
   session context. Preserve the user's path and capitalization exactly; do
   not guess between similarly named folders. Clone it first if it isn't
   cloned yet.
2. Create or merge `<workspace>/.claude/settings.local.json`, preserving
   existing JSON keys and `env` values, with
   `"CLAUDE_CODE_ENABLE_TODO_TOOLS": "1"`. Do this now, in this turn, even
   though the native task tools it enables won't be checkable until turn 2.
3. Call `mcp__ccd_directory__change_directory` with the exact absolute path.
   Continue only when the result confirms `Folder access granted`. If the
   tool is unavailable or permission is denied, stop and report that the
   session folder is not connected; do not continue with a scratch
   workspace.
4. Create the repository's Python environment (`venv`) and install
   dependencies if they don't already exist, using the repository's own
   commands.
5. Initialize or resume `onboarding-state.json` from that connected folder.
6. Start the local server from that folder if `http://127.0.0.1:8765/` is
   not responding. Verify it with `curl` before proceeding. If another
   project's FTJA server already holds port 8765, tell the user and ask
   before stopping it — don't silently kill another workspace's server.
   Start it via `mcp__Claude_Browser__preview_start` with a `name`, not a
   background shell command: create or merge `.claude/launch.json` with a
   configuration such as `{"name": "ftja", "runtimeExecutable":
   "venv/bin/python", "runtimeArgs": ["-m", "ftja.server"], "port": 8765}`,
   then call `preview_start` with that `name`. This is a one-shot, purpose-
   built launch path; a background `Bash`/`nohup` command doing the same
   thing is more failure-prone (it needs its own PID tracking) and, live,
   has been the actual site of long, silent multi-minute stalls in this
   step — see "Move quickly through routine steps" for why that matters.
7. Open the viewer immediately, next to this chat. Call
   `mcp__Claude_Browser__preview_start` with `http://127.0.0.1:8765`, then
   call `mcp__Claude_Browser__get_page_text` to verify the page. On macOS
   also run `open http://127.0.0.1:8765` so the user gets a visible browser
   without needing to click an `Open` card. Do not describe the viewer as
   open until a navigation/page-text check succeeds.
8. End the turn by asking exactly one question, worded so it's unmistakable
   that the reply belongs in *this chat*, not the web page — the web page
   has no button to click at this stage and will just sit in a plain
   waiting state, which is expected, not a bug. Name your own client
   (Claude Code, Codex, or whatever you actually are) instead of leaving a
   placeholder, for example: "I've opened the FTJA web view next to this
   chat. To continue, type `Yes, ready to start onboarding` here in Claude
   Code." Never phrase this as something to answer on the web page. Say
   nothing about background files, checklists, or next steps yet; that all
   belongs to turn 2.

### Why two turns

`change_directory`'s folder switch, and the `env` setting it carries, only
take effect once this turn ends and a new one begins — so does the native
task tool availability that setting controls. Checking for `TaskCreate`/
`TaskUpdate` inside this same turn is expected to find nothing; that is not
evidence setup is broken, and it is not a reason to add a workaround (a
background `sleep` command, an immediate self-recheck) to dodge the turn
boundary. End the turn cleanly and let the user's reply start turn 2, where
the tools are actually available.

If the Claude client header still says `No folder` after a successful
`Folder access granted` result, report that the client label did not
refresh; the internal working directory and the visible client association
are separate states. Never claim the header says FTJA unless it actually
does.

### Move quickly through routine steps

A full onboarding pass is mostly the user reading a card, deciding, and
clicking — that time is theirs and isn't something to optimize. But a live
run of this skill has taken 20+ minutes on stretches with nothing for the
user to look at, and the cause was not the web's poll interval (already
sub-second and irrelevant to how many turns this skill takes) or large
inputs — it was this skill routing simple, one-shot steps through several
extra tool calls each:

- Retrying a failed tool call more than once or two before trying a
  genuinely different approach. If a call is refused or errors, don't loop
  on the identical call — the isolated fix above (`preview_start` +
  `launch.json` for the server) exists because that exact retry loop was
  observed live on a plain background server-start command.
- Re-verifying something in the browser (navigate, screenshot, a second
  `read_page`) right after writing it to `onboarding-state.json`. The web's
  poll-and-render loop is exercised by this project's own test suite; once
  you've confirmed the state file has what you meant to write, the browser
  will render it. Reserve an actual browser check for when the user reports
  something looks wrong, not as a routine step after every write.
- Multiple small exploratory commands (checking if a port is free, whether
  a process exists, whether a file exists) where one targeted command would
  do. Prefer the one-shot commands this skill already gives you over
  rediscovering them by hand.

## Turn 2 onward: the native checklist

Any affirmative reply to turn 1's question starts this section — do not
require exact wording.

### Client-native task list

- Verify `TaskCreate` and `TaskUpdate` are available in the current session
  now. A tracked `.claude/settings.json` arriving with a clone is not
  evidence the live session reloaded this feature — only an actual
  post-turn-boundary check is. If the native tools are still unavailable
  here, stop and report the limitation honestly; do not substitute prose
  and call it a native checklist.
- In Codex, use `update_plan` with `pending`, `in_progress`, and `completed`
  statuses. Its contract permits at most one `in_progress` item.
- Do not enter Plan Mode, `EnterPlanMode`, `/plan`, or a collaboration Plan
  Mode for this workflow. The native checklist is an execution-progress
  mechanism, not a planning mode.

Create these 8 items, in this order. Each is something the *user* does or
reviews — infrastructure from turn 1 (clone, connect, server, browser) is
already done and does not belong on this list:

1. `Add your background files`
2. `Review your profile summary`
3. `Review your search settings`
4. `Review the code-based filter`
5. `Answer three questions about your ideal role`
6. `Confirm your judgment rubric`
7. `Run your first job search`
8. `Review your first results`

Keep exactly one item `in_progress` at a time. A command invocation alone
cannot complete a checklist item; complete one only after its required
evidence is observed (see "Evidence and checklist advancement" below). The
checklist mirrors durable `onboarding-state.json` and observed client or
browser results; it never replaces that state machine or hides completed
work. Item 5 covers three sequential sub-answers (clear yes, dealbreakers,
preferences) — keep it `in_progress` across all three; it completes only
once the third is confirmed.

### Narrate long operations

Between two `wait-for-action` calls — writing `profile/summary.md`,
deriving titles/keywords, drafting the rubric — the web has nothing to show
and would otherwise look stuck or disconnected. Before starting one of
these, run:

```bash
venv/bin/python -m ftja.onboarding . status working "Writing your profile summary from your files"
```

The web shows this label with a spinner, and the matching checklist item
does the same, instead of a plain "connecting" notice. Write a short,
specific label for what you're actually doing right now (not a generic
"working..."). The next `wait-for-action` call automatically clears this
and marks the bridge listening again — no separate "stop working" call is
needed in the normal flow.

### Evidence and checklist advancement

- For `confirm_profile_sources`, `confirm_profile_summary`, `confirm_profile`,
  `confirm_stage0`, each ordered `answer_rubric_question` (`clear_yes`, then
  `dealbreakers`, then `preferences`), and `confirm_rubric`: advance only after
  `wait-for-action` returns `status: confirmed`, then read the returned action
  and the full durable state. The three rubric answers must remain strictly
  ordered. Build or mark drafts only after their preceding confirmation.
- Do not mark `Run your first job search` complete because `/ftja-run` was
  merely suggested — it needs a real completed run. Do not mark `Review your
  first results` complete until that run has an observable result surface or
  run artifact; a valid empty result must be verified and explained, never
  fabricated.

### Routine chat copy

The native checklist carries progress. Normal successful chat output is one or
two short sentences: state only what was just verified when useful and the
single next user action (or that no action is needed yet). Keep recovery and
timeout detail only in failure messages. Do not repeat the whole checklist or
web-card fields in ordinary prose.

## File-driven onboarding contract

The local web onboarding is a viewer and decision surface, not a wizard. Never
advance it with a `Next` button and never ask the user to repeat structured
answers in chat. The setup chat is the driver; it writes the local files and
`onboarding-state.json`, while the browser polls that state and shows only the
current stage.

This means every one of the decisions below is made on the web card, never in
this chat — do not call `AskUserQuestion`, and do not build an equivalent
multi-step Q&A/wizard out of plain chat messages, for any of them: which job
titles to search, location/remote/hours/max-results, the Stage 0 keywords/
languages/excludes, each of the three rubric questions, or the final rubric
text. A live run of this skill once ran the job-titles decision as a chat
wizard ("1/3 ... Skip / Next") in parallel with the web card that already
existed for it — that duplicates the interaction, contradicts this contract,
and is exactly what "not a wizard" rules out. If a decision needs the
candidate's input, write a proposal to `onboarding-state.json` and wait for
the matching web action; the only chat-side question this skill ever asks
directly is the single turn-1 handoff question in "Turn 1" above and the
occasional one-off follow-up when required evidence is genuinely missing (see
"What you need to learn").

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
`set_profile_summary`, `confirm_profile_summary`, `set_profile_draft`,
`confirm_profile`, `set_stage0_draft`, `confirm_stage0`,
`answer_rubric_question`, and `confirm_rubric`. The agent/LLM may produce drafts,
but it must not choose the next stage or promote unconfirmed data into active
configuration.


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

## Web-driven profile sources

The web view is the only source-material input surface. Never use
`AskUserQuestion` to ask where a resume or portfolio lives, and never ask
for an absolute source path in chat. In this same turn, write the
source-material instruction as your response text, then immediately run the
blocking `wait-for-action --type confirm_profile_sources` command below —
don't end the turn or ask the user to click before that command is running.
The web card's recovery phrase is a fallback for a delayed turn, not the
normal startup path. If a user message arrives saying they already acted,
run the same wait command immediately anyway; it recovers the most recent
valid action from the connected state instead of asking them to click again.

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

Read every path from `onboarding-state.json`, then inspect/copy the selected
sources into the gitignored `profile/` folder as needed. This folder is a
private local cache and derived workspace for repeatable daily runs; it is
not an upload, public snapshot, or Git commit. Do not require a
resume/portfolio label: the UI uses the selected file or folder name.

Mark bridge status `working` before reading and summarizing the files (see
"Narrate long operations"), then write `profile/summary.md`. The web must
then show the summary for an explicit `Confirm profile summary` decision.
Send a short review instruction as your response text, then wait:

```bash
venv/bin/python -m ftja.onboarding . wait-for-action --type confirm_profile_summary --timeout 900
```

Only after that action returns `status: confirmed`, read the confirmed profile
summary and create **one structured proposal covering both upcoming deterministic
screens**:

1. scraping/search settings: titles, location, remote-only, published-within,
   and results wanted;
2. Stage 0: matching keywords, readable languages, and hard-exclude words.

Do not show either screen until this combined proposal has been written to
`onboarding-state.json`. This is a required protocol step, not optional LLM
judgment. The LLM may derive candidate values from the profile, but the state
machine validates their JSON shape and rejects missing required fields. Never
write empty title, keyword, or language arrays, `...` placeholders, or generic
blank defaults.

Use profile evidence: target roles become search titles; the candidate's base
location becomes the initial location; target roles and product/AI work become
matching keywords; languages the candidate can actually read become ISO 639-1
language filters; explicit dealbreakers or clearly non-target work become
exclude words. Excludes may be empty only when the profile provides no defensible
negative signal. Do not invent unsupported values merely to satisfy validation;
ask one focused follow-up first when required evidence is absent.

Write the complete combined proposal in a single command **before** instructing
the user to review search settings:

```bash
venv/bin/python -m ftja.onboarding . set-state --stage profile --phase title_proposal --status waiting --json '{"profile":{"titles":["Product Builder","AI Product Manager","Founding Product Manager"],"criteria":{"search_terms":["Product Builder","AI Product Manager","Founding Product Manager"],"location":"France","is_remote":false,"hours_old":24,"results_wanted":100,"keywords":{"tier1":["AI product","LLM","Product Builder"]},"languages":["en","fr"],"exclude_keywords":["pure sales"]}},"cards":{"stage0":{"keywords":["AI product","LLM","Product Builder"],"languages":["en","fr"],"exclude_keywords":["pure sales"]}}}'
```

The `set-state` helper records drafts only. It must leave the state at
`profile/title_proposal`; it must never call `confirm_profile` or move to Stage 0.
Verify all of the following by reading `onboarding-state.json` back (do not
open the browser to check — the web polls and renders that same file, and a
separate navigate/screenshot here is a slow, redundant round trip, not extra
safety; see "Move quickly through routine steps"):

- state is still `profile/title_proposal`;
- the search-settings card contains the proposed titles and location;
- `cards.stage0` already contains non-empty keywords and languages.

Tell the user both upcoming screens were prefilled from the confirmed profile and
remain editable. Then wait for `confirm_profile`. When it returns, the state
machine must transition to `rubric/stage0` while preserving the already-populated
`cards.stage0`; confirm that in the returned state, not by reopening the browser.
Never insert a blank intermediate Stage 0 state.

Before each card, write the instruction as your response text, then run only
the matching command:

```bash
venv/bin/python -m ftja.onboarding . wait-for-action --type confirm_profile --timeout 900
venv/bin/python -m ftja.onboarding . wait-for-action --type confirm_stage0 --timeout 900
venv/bin/python -m ftja.onboarding . wait-for-action --type answer_rubric_question --timeout 900
venv/bin/python -m ftja.onboarding . wait-for-action --type confirm_rubric --timeout 900
```

Use only the command for the card currently shown. The helper watches the
connected project folder and also recovers an action that completed immediately
before the helper started, so a delayed chat turn does not lose a web decision.
There are three sequential `answer_rubric_question` actions; after each one,
re-read the state so the next web question remains the source of truth. After
`status: confirmed`, read the returned `action` and the full
`onboarding-state.json` before writing
the next draft or message. Never assume that a changed web card reached the
setup chat without the helper result. If a later wait times out, tell the user
the matching recovery phrase shown by the web card (for example, `I confirmed
my search settings, continue the setup`) and do not claim that the chat resumed.


## What you need to learn

**For `criteria.json` (Rubric stage, after Profile — Stage 0 deterministic, used by `ftja/scrape.py` and
`ftja/filter_stage0.py`):**
- `search_terms`: list of LinkedIn search strings (e.g. `["Product Manager", "Founding PM"]`)
- `location`, `is_remote`, `hours_old` — this is the ONLY place location is
  controlled. If the candidate wants roles in more than one region (e.g.
  "Europe, or Singapore/Dubai if visa-sponsored"), that needs multiple
  scrape locations (a loop over `location` values in `/ftja-run`, or a
  `search_terms` x `locations` cross product) — say so explicitly if the
  candidate mentions more than one region, don't silently scope to just the
  first one they said.
- `results_wanted`: the max results scraped per search title (the
  "Max results per title" field on the search-settings card, 1-1000,
  default 100). `/ftja-run` scrapes every title up to this cap, merges all
  of them into one list, and only then runs the code-based filter and LLM
  judgment once over the merged set — it does not finish one title through
  judgment before starting the next. Raising this widens per-title
  coverage; it does not change how many titles get searched.
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

Tell the user both `rubric.md`/`criteria.json` are ready to review/edit by
hand if they want, and that `/ftja-run` is the next step (recommend a
manual run first — M1 in SPEC.md — before setting up the launchd schedule).
Once a run produces passed/failed jobs, the same server's Results tab is
where they record apply/skip decisions — see `/ftja-review` for how those
decisions feed back into criteria.json/rubric.md.
