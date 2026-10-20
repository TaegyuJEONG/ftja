---
name: ftja-setup
description: One-time (but re-invocable) onboarding for FTJA. Interviews the user in the chat and writes criteria.json, rubric.md and profile/summary.md, then opens the local viewer for a final review. Invoke when rubric.md/criteria.json don't exist yet, or when the user asks to "set up again" / "reset rubric" / "redo onboarding".
---

# FTJA setup

The whole setup happens in this chat. The local viewer is opened only at
the end, once the files a run needs exist, so the user can review and edit
them there. Nothing in this skill waits for a click in the browser, and you
never ask the user to click anything in the viewer during setup.

Interview conversationally: one question at a time, follow up when an
answer is vague, and do not guess an answer the user has not given. Talk in
the language the user writes in. Keep each message short: what you
propose, and the one thing you need from them.

Every step is saved by `python -m ftja.onboarding`, which checks the input,
refuses to run a step before the one it builds on, and prints the result as
JSON (`{"error": ...}` and exit code 1 when something is wrong; fix it and
run the command again). Progress is kept in `onboarding-state.json`, so an
interrupted setup picks up where it stopped.

**Commands on Windows.** Every `venv/bin/python` below is
`venv\Scripts\python` in PowerShell. Pass JSON with `--json-file`: write the
JSON to a file under `.ftja-run/` with your file-writing tool first.
PowerShell strips the quotes out of JSON given on the command line, so do
the same on every system and never use `--json`.

## Turn 1: connect the folder

Do this without asking anything mid-way, then end the turn with exactly one
question.

1. Resolve the absolute path of the FTJA folder from the session context.
   Preserve the user's path and capitalization exactly; do not guess
   between similarly named folders. Clone it first if it isn't cloned yet.
   A fresh Windows PC often has neither Git nor Python (a `python` that
   only opens the Microsoft Store is not Python): ask once, for both
   together, before installing them with `winget install --id Git.Git -e`
   and `winget install --id Python.Python.3.12 -e`.
2. Create or merge `<folder>/.claude/settings.local.json`, keeping existing
   keys and `env` values, with `"CLAUDE_CODE_ENABLE_TODO_TOOLS": "1"`.
3. Call `mcp__ccd_directory__change_directory` with the exact absolute
   path. Continue only when the result confirms `Folder access granted`.
   If the tool is unavailable or permission is denied, stop and say the
   session folder is not connected; do not continue in a scratch folder.
   (A client without this tool, such as Codex started in the folder, is
   already in the right place: check with `pwd` and go on.)
4. Create the Python environment and install dependencies if they are not
   there yet: `python3 -m venv venv` and `venv/bin/pip install -r
   requirements.txt` (Windows: `python -m venv venv` and
   `venv\Scripts\pip install -r requirements.txt`).
5. Run `venv/bin/python -m ftja.onboarding . status`. If `next` is not
   `profile`, an earlier setup was interrupted: say what is already done
   and that you will continue from `next`, and ask the matching question
   below instead of the resume question.
6. End the turn by asking for the resume, for example: "Attach your resume
   here (PDF or Word), or tell me where it is on this computer."

The folder switch and the `env` setting take effect only once this turn
ends, which is why setup asks its first question here instead of going on.

## Turn 2 onward

### Checklist

If `TaskCreate` and `TaskUpdate` are available now, create these items in
this order and keep exactly one `in_progress`:

1. `Add your resume`
2. `Review your profile summary`
3. `Review your search settings`
4. `Review the code-based filter`
5. `Answer three questions about your ideal role`
6. `Review everything in the viewer`

Complete an item only once its command below has printed a result without
an error. If the task tools are not available, go on without a checklist;
do not imitate one in prose. Do not enter Plan Mode for this workflow.

### Step 1: the resume

The resume goes into the `profile/` folder inside the FTJA folder. That
folder is gitignored: a private local copy for daily runs, never committed.

- **Attached in the chat:** save the file into `profile/` under its own
  file name. If what you received is only the text of the file and not
  the file itself, write that text to `profile/resume.md` and say so in
  one line.
- **A path on this computer:** copy the file into `profile/` (`cp`;
  Windows `Copy-Item`). Do not move or change the original.

Take the resume only. Portfolios and folders of documents are switched off
during the beta, because the profile drafted from large folders was not
reliable yet; if the user offers one, say so and go on with the resume.

Read the resume and write a draft summary to `profile/summary.draft.md`: a
condensed distillation of a few hundred words, with role history
highlights, key skills and tools, notable achievements, and whatever a
judge would need to assess fit. Stage 2 reads this file on every posting,
every day, instead of the resume (reading the full files measured
44k to 86k tokens per call), so write it yourself from what you read; do
not paste the raw file.

Show the summary in the chat and ask whether it is right. Apply what the
user corrects and show it again, until they approve. Then save:

```bash
venv/bin/python -m ftja.onboarding . profile --source profile/<resume file> --summary-file profile/summary.draft.md
```

### Step 2: search settings

Propose these from the approved profile, as a short list the user can
correct in one reply. Do not save before they agree.

- **Job titles** (`titles`): the roles the profile targets. Only postings
  whose title contains a title you list are scraped, as a run of whole
  words in any word form and any case: "Product Manager" finds "Senior
  Product Manager", "AI Product Manager" and "Product Managers (m/f/d)",
  and does not find "Product Owner", "Product Marketing Manager", "Head of
  Product" or "Chef de produit". LinkedIn's own search matches by meaning
  and would return those too; FTJA leaves them out, so the user gets the
  titles they named and nothing else. Tell the user this rule in one
  sentence with an example from their own titles. Then propose the other
  names the same job goes by, for them to pick from: common variants
  ("Product Owner", "Head of Product") and the title in each language
  they read for the places they search ("Chef de produit" in France).
  Each one they keep is its own title. Do not add any they did not agree
  to.
- **Locations** (`locations`): start from the country the user is based
  in. Propose every place the profile says they want; if they mention more
  than one region, don't silently keep only the first. A run searches
  every title in every location (3 titles in 3 locations make 9
  searches), so say what that does to the length of a run. There is no
  remote-only setting: LinkedIn ignores its workplace filter for
  logged-out searches. A candidate who wants remote roles sets up the
  **Remote check** afterwards on the Pipeline tab (`/ftja-remote`); say
  so when they mention remote work.
- **Posted within** (`hours_old`): default 24 hours.

### Step 3: the code-based filter

Propose these from the profile too, again as one short list to correct:

- **Languages** (`languages`): ISO 639-1 codes (`["en", "fr"]`) of the
  languages the user can actually read a posting in. A posting whose text
  is in none of them is dropped, and so is one that plainly requires
  fluency in a language not on the list ("Fluent in German and English"),
  so a language requirement needs no exclude word (and a bare language
  name there would also drop "the German market").
- **Keywords** (`keywords`, with `keyword_language`): the phrases that must
  appear in a sentence of the posting for it to reach Stage 1. Stage 1
  reads only the sentences around them and judges those against the
  rubric, so every keyword has to be a word that shows up where a posting
  talks about what the rubric is about.
  - Write them **in every language in `languages`**: a posting written in
    French is caught only by French keywords. Do not translate word for
    word; give the terms postings in that language really use, and list
    the word forms yourself (singular and plural, masculine and feminine).
    Record each keyword's language in `keyword_language`
    (`{"prototypage": "fr"}`); a name that is the same in every language
    ("LLM", "Cursor") goes under the first language.
  - A keyword matches whole words and their forms ("remote" matches
    "remotely", not "premote"), so write full words, never a fragment. A
    keyword in capitals ("UTC") matches only those capitals.
  - Do not put remote-work words ("remote", "hybrid", "on-site",
    "télétravail") in the keywords: Stage 1 would then judge benefit and
    location sentences against a rubric that is about the role. That is
    the Remote check's job.
  - Two sources matter; ask about each separately:
    **concrete tool names** the target roles use (for AI-builder roles:
    Cursor, Replit, v0.dev, Lovable, Bolt.new, Windsurf, Supabase, Vercel,
    Claude Code, Codex, OpenAI API, "vibe coding"), which are a much
    stronger signal than topic words and easy to miss; and **role
    phrases** specific to the target roles ("0 to 1", "solo founder",
    "entrepreneur in residence", "venture builder"), concrete phrases
    rather than vague topics.
- **Exclude words** (`exclude_keywords`): any word here anywhere in the
  posting drops it, with no phrase matching. Ask: "is there anything, a
  language or otherwise, that should get a posting dropped outright if it
  appears?" A tool, a company, an industry all work. It may stay empty.

When the user agrees, write both steps to `.ftja-run/setup-search.json`
and save them in one command:

```json
{
  "titles": ["AI Product Manager", "Product Builder"],
  "locations": ["France"],
  "hours_old": 24,
  "filter": {
    "languages": ["en", "fr"],
    "keywords": ["AI product", "LLM", "produit IA"],
    "keyword_language": {"AI product": "en", "LLM": "en", "produit IA": "fr"},
    "exclude_keywords": []
  }
}
```

```bash
venv/bin/python -m ftja.onboarding . search --json-file .ftja-run/setup-search.json
```

### Step 4: three questions about the ideal role

Ask these one at a time, in this order, and wait for each answer before
asking the next. Follow up when an answer is too vague to judge a posting
by (role type, seniority, what they would do day to day).

1. **What makes a role a clear yes?** (`clear_yes`)
2. **What should rule a role out?** (`dealbreakers`): non-negotiables
   that need judgment beyond the code filter.
3. **What would make a good role even better?** (`preferences`): what
   should rank otherwise good roles.

Keep the user's own words. One criterion per line: a line break, a bullet
or a semicolon starts a new one. Each "clear yes" line stands on its own (a
posting passes on any one of them), so put things that must hold together
on the same line. Write the three answers to `.ftja-run/setup-answers.json`
(`{"clear_yes": "...", "dealbreakers": "...", "preferences": "..."}`) and
run:

```bash
venv/bin/python -m ftja.onboarding . rubric --json-file .ftja-run/setup-answers.json
```

The command prints the rubric list that code built from the answers. Show
it to the user as it is printed: do not rewrite, merge or reorder the
criteria yourself. If they want a change, change the answer it came from
and run the command again.

### Step 5: write the files and open the viewer

When the user is happy with the rubric list:

```bash
venv/bin/python -m ftja.onboarding . finish
```

This writes `criteria.json`, `rubric.md`, `rubric-criteria.json`,
`profile/summary.md` and `profile/sources.json`. Do not git-add or commit
any of them, or the files in `profile/`: they are gitignored on purpose. If
the user wants version history, they can opt into a separate private
repository.

Then start the viewer, unless `http://127.0.0.1:8765/` already answers
(check with `curl -s`; Windows `Invoke-WebRequest -UseBasicParsing`). If it
answers, make sure it is this folder's viewer: `/api/version` returns the
`project_dir` it shows. If another folder's FTJA viewer holds the port,
tell the user and ask before stopping it.

- In the Claude desktop app, create or merge `.claude/launch.json` with
  `{"name": "ftja", "runtimeExecutable": "venv/bin/python", "runtimeArgs":
  ["-m", "ftja.server"], "port": 8765}` (Windows: `"runtimeExecutable":
  "venv\\Scripts\\python.exe"`) and call
  `mcp__Claude_Browser__preview_start` with that `name`.
- Without that tool, start it in the background:
  `nohup venv/bin/python -m ftja.server > .ftja-run/server.log 2>&1 &`
  (Windows: `Start-Process venv\Scripts\python.exe -ArgumentList '-m',
  'ftja.server' -WindowStyle Hidden`).

Open `http://127.0.0.1:8765/#pipeline` (macOS `open`, Windows
`Start-Process`). Retry a failed start once with a different approach; do
not loop on the same command.

End with two or three sentences: setup is done; the Pipeline tab shows
every setting and anything there can be edited directly; once it looks
right, type `/ftja-run` in this chat to run the first search. Mention that
`/ftja-run` is best run by hand a first time before scheduling it, and that
the Results tab is where they record apply/skip decisions afterwards (see
`/ftja-review` for how those decisions feed back into the rubric).

## Setting up again

For "set up again" / "reset rubric" / "redo onboarding", run
`venv/bin/python -m ftja.onboarding . reset` and start from Step 1. The
existing `criteria.json` and `rubric.md` stay in place until `finish`
overwrites them; say so before starting.
