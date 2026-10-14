# Changelog

What changed in each version, newest first. If you cloned FTJA earlier, run
`/ftja-update` in your coding agent (or `git pull` in the folder) to get
these. Your own files — `criteria.json`, `rubric.md`, `profile/`, `seen.db`,
digests — are never touched by an update.

## 0.3.2 — 2026-10-14

### What changes for you after updating

- **Setup finds a Python that works.** FTJA needs Python 3.10 or newer.
  On a Mac, the Python that comes with macOS (3.9) is too old, and setup
  now looks for a newer one or offers to install it. On Windows, setup
  finds a Python that is installed but not on the PATH instead of trying
  to install it again.
- **Setup no longer stalls on Windows.** Every check of the web view has a
  3-second limit, commands run in PowerShell only, and their output is
  never cut short (which ended them with an error).
- **Setup opens the web view before its first question again**, and starts
  the viewer from your FTJA folder. On Windows it used to start it through
  a settings file in a temporary folder.
- **The page says when its viewer has stopped.** If the viewer stops (the
  computer slept, the window that started it closed), the page now says so
  at the top, with how to start it again: ask the Claude Code session
  connected to your FTJA folder to start the FTJA viewer, or run the
  command it shows. The page comes back on its own once the viewer runs.
- **Locations are suggested in English, whatever your browser's language.**
  A French browser suggested "Royaume-Uni", which LinkedIn does not know:
  it searched the United States instead, without saying so. If your
  location is not in English, change it on the Pipeline tab.
- **FTJA now works on Windows.** Before, setup stopped at the first step:
  "Add resume" answered "supported on macOS only".
  - "Add resume" opens the Windows file chooser.
  - The folder name at the bottom of the page opens the folder in Explorer.
  - The page knows when your agent is waiting for your click. The check
    used before could interrupt the agent's wait on Windows, or always
    report that no agent was there, which keeps the buttons switched off.
  - Postings, keywords and résumés with letters outside your Windows
    language (Korean, French accents, a long dash) are read and written
    correctly. Every file is UTF-8 now, on every system.
  - Saving no longer fails when the page reads a file at the same moment
    your agent rewrites it.
  - Only one viewer can run on the port. Windows used to let a second
    one start alongside the first, and the page then got answers from both.
  - The setup, run and update instructions your agent follows name the
    Windows commands, and setup offers to install Git and Python when the
    PC has neither.
- **A decision sent twice is done once.** Clicking Confirm twice, or on a
  page that had just been redrawn, showed "stale onboarding revision"
  although the first click had worked. Now the second one is ignored.
- **A button is only live when your agent is really waiting.** If the
  agent's wait was stopped, the page kept offering Confirm and then
  answered "setup bridge is not listening". It now shows that it is
  waiting for the agent.
- **The page no longer stalls.** It asked the viewer something every
  second over a new connection each time. On Windows the closed ones piled
  up in the thousands, and then about one request in five could not
  connect at all: the page loaded slowly, stayed on its grey waiting lines,
  or lost a click. The viewer now keeps a few connections open and reuses
  them, and a tab you are not looking at asks nothing.
- **Still Mac only:** scheduled runs and the "run finished" notification.
- The Windows code is covered by tests but has had little use on real
  Windows PCs yet. If something stops, please report the message you see.

## 0.3.1 — 2026-10-13

### What changes for you after updating

- **Only postings whose title contains your search term are scraped.**
  "Product Manager" finds "Senior Product Manager" and "AI Product
  Manager"; it no longer brings back "Product Owner", "Strategy Lead" or
  "Chef de produit", which LinkedIn's search returns alongside. If you
  want those, add each as its own search term. This used to be a setting,
  off unless you ticked it: it is now how every search works, so check
  your search terms after updating.
- **"Max results per search" is gone.** A search scrapes every matching
  posting and ends when LinkedIn's results stop containing the term. Both
  old settings are ignored if your `criteria.json` still has them.
- **Runs are shorter.** The pages of postings that do not match are no
  longer read, and those were most of a scrape's time: the same search
  (Product Manager, France, 24 hours) took 17 minutes before and a little
  over 4 now.
- **A run shows how many postings it searched through.** Next to each
  search term: how many postings were scraped, and out of how many
  LinkedIn returned for that search ("37 of 298").
- **A title LinkedIn does not know well is found less often.** Its
  logged-out search puts matching titles first for a common title, and
  not for a rare one ("Venture Builder"): some postings that exist are
  never returned to FTJA. This was true before too; it is now visible.

### Under the hood

- The title is checked on the search results, before a posting's page is
  fetched. A search ends after 15 result pages in a row without a
  matching title, or at LinkedIn's limit of 1,000 results.
- Search requests are spaced 3 to 7 seconds apart; with less, LinkedIn
  answered 429.
- The titles a search left out are written to
  `.ftja-run/<run>/scrape/<n>.skipped.json`. Nothing shows them yet.
- Stage 0 no longer checks titles; the scrape has done it.

## 0.3.0 — 2026-10-12

### What changes for you after updating

- **Nothing changes until you switch something.** A run with the new
  Remote check off does exactly what it did before.
- **Each judging stage has an on/off switch** on the Pipeline tab: Remote
  check, Stage 1, Stage 2. A run does the ones that are on, in that order,
  each judging what the one before let through. The "Stop after Stage 1"
  box is gone; switching Stage 2 off does the same, and a setting you had
  saved is read as that.
- **A new optional stage, the Remote check**, sits before Stage 1. For
  each posting a cheap model reads only the title and the sentences around
  your remote keywords, and reports what the posting says per topic
  ("categories": how remote, from where, which hours, which on-site
  duties), each with the sentence it rests on, or "Not stated". You mark
  which answers rule a posting out; a posting that gives one fails.
  Otherwise the model judges the sentences against all of your answers and
  passes a posting only when one of them says the role is worked in a way
  that suits you, showing that sentence. A posting that says nothing about
  remote work does not pass.
- **Your agent writes the setup for you.** On the Pipeline tab, fill in
  where you live, where you want to work, your time zone and your work
  permit, and press the button; or type `/ftja-remote` in the chat. The
  agent writes the keywords (in the languages you read), the categories
  and the rules. Everything it writes can be changed, added to or removed
  on the page. If no agent session reacts within about ten seconds, the
  page shows the line to type.
- **A posting with none of your remote keywords is dropped by code**,
  before any model call: it says nothing for the check to read.
- **The Pipeline tab follows the order things happen in:** scraping, the
  code filter (languages and exclude words), Remote check, Stage 1,
  Stage 2. The "Include keywords" moved from the code filter to Stage 1,
  the stage that reads them. With Stage 1 off, they are not required.
- **Keywords are kept per language.** A posting written in French is only
  caught by French keywords, so Stage 1's keywords and the Remote check's
  now show one list for each language you read postings in. Onboarding
  proposes keywords in every one of your languages. When you add a
  language later, its list is empty and offers "Add French keywords with
  my agent" (or type `/ftja-keywords`): the agent writes them from the
  keywords you already have, and you confirm them on the page before they
  are added. Take a language off your readable languages and its keywords
  are switched off, not deleted; they come back with the language. Your
  existing keywords count for your first language and work as before.
- **Results:** a run with the Remote check shows a Remote line with
  "Passed N — see why" and "Failed N — see why". Each posting lists every
  category with its answer, the detail, whether that fails by your rule,
  and the sentence. Both lists download as a table (CSV), one row per
  posting.
- **Continue goes one stage at a time.** A run that stopped after the
  Remote check offers "Continue to Stage 1" and "Continue to Stage 2"; one
  that stopped after Stage 1 offers "Continue to Stage 2" as before. The
  button now gives up waiting for the agent after 10 seconds instead of 25
  and shows the line to type.

## 0.2.18 — 2026-10-12

### What changes for you after updating

- You can search several locations. Under Scraping criteria on the
  Pipeline tab, Locations takes a list, like Search terms. A run searches
  every search term in every location: 3 terms and 3 locations make 9
  searches, each up to your maximum results.
- More searches make a longer run. The Results tab shows a line per search
  (term and location) with its own count.
- A location can contain a comma ("Paris, France"): press Enter to add it.
- Your existing single location keeps working and shows as one entry.

## 0.2.17 — 2026-10-12

### What changes for you after updating

- The Scraping criteria on the Pipeline tab no longer carry "?" marks and
  hover text. The one-line descriptions stay.

## 0.2.16 — 2026-10-12

### What changes for you after updating

- The Scraping criteria on the Pipeline tab read shorter. The two
  checkboxes and the note at the top each say what they do in one line; the
  full explanation shows when you hover over the line or its "?" mark.

## 0.2.15 — 2026-10-12

### What changes for you after updating

- New option on the Pipeline tab, under Scraping criteria: **Title must
  contain the search term**. LinkedIn also returns postings it considers
  related, so a search for "Entrepreneur In Residence" brings back
  "Co-Founder" and "Strategy Lead". With the option on, Stage 0 keeps a
  posting only if its title contains the search term that found it, in any
  word form ("Product Manager" keeps "Senior Product Managers"). With
  several search terms, each is checked against its own results.
- It is off unless you turn it on. Turned on, differently named versions of
  the same job are dropped ("Founder in Residence" for "Entrepreneur In
  Residence"); add them as search terms to keep them.
- The Stage 0 line of a run shows how many postings the rule dropped.

## 0.2.14 — 2026-10-12

### What changes for you after updating

- The "Exact phrase" setting is gone. It put each search term in quotes,
  but LinkedIn's search for people who are not logged in matches by
  meaning either way: tested on 20 postings per search, "venture builder"
  in quotes returned none that contain that phrase, and "founder in
  residence" returned 3, next to titles like "Entrepreneur In Residence".
- Search terms are now sent as you type them. If yours were quoted by the
  old setting, the postings a search returns may shift a little. To keep
  the quotes, type them into the search term yourself.
- A phrase that has to be in the posting belongs in the Stage 0 keywords,
  which check the posting's text.
- A search term that contains quote marks is kept when you save the
  Pipeline tab. It used to be saved as an empty term.
- Nothing to change in your files: an `exact_phrase_search` line left in
  `criteria.json` is ignored.

## 0.2.13 — 2026-10-12

### What changes for you after updating

- The "Remote only" setting is gone, from setup and from the Pipeline tab.
  It never had an effect: LinkedIn ignores its Remote filter for searches
  that are not logged in, which is how FTJA searches. Tested on one search,
  the same postings came back with the filter set to remote, hybrid,
  on-site or left out. Runs that had it on were not restricted to remote
  postings.
- To find remote roles, put "remote" in a search term (it then matches the
  posting's text), and let FTJA judge remote from each posting: keywords
  such as "remote", "hybrid" and "on-site", and a rubric criterion.
- Nothing to change in your files: an `is_remote` line left in
  `criteria.json` is ignored.

## 0.2.12 — 2026-10-11

### What changes for you after updating

- A run can stop after Stage 1. Tick **Stop after Stage 1** under Scraping
  criteria on the Pipeline tab and the next `/ftja-run` scrapes, filters and
  reads the keyword sentences, then stops before Stage 2, the step that
  reads every remaining posting in full and costs most of a run. Use it
  while you tune keywords and the rubric.
- A stopped run shows what passed Stage 1 and a **Continue to Stage 2**
  button on its Stage 2 line. After such a run your agent keeps waiting for
  about nine minutes; press the button in that time and Stage 2 starts. If
  the agent is no longer waiting, the button shows the line to type in the
  chat instead (`/ftja-run continue <run id>`). The run picks up where it
  stopped, with the rubric it started with, and keeps its place in the list.
- Postings waiting for Stage 2 count as already judged, so a later run does
  not read them again. To have them judged from scratch, delete the run.

## 0.2.11 — 2026-10-11

### What changes for you after updating

- `/ftja-update` and `/ftja-run` see a new version as soon as it is
  published. They read the version from a file that GitHub serves through a
  cache, which kept answering with the previous version for several minutes
  after a release. They now ask the repository directly.

- Deleting from the trash asks a plain question in place ("Delete this run
  for good? Yes, delete / Cancel") and shows "Deleting…" while it works.
  The button used to turn into "Cannot be undone. Click again to delete",
  which read like a status message.

## 0.2.10 — 2026-10-11

### What changes for you after updating

- "Delete for good" and "Empty the trash" work. They asked for confirmation
  with a browser pop-up that some browsers suppress, and then nothing
  happened. The button now asks you to click it a second time instead.

## 0.2.9 — 2026-10-11

### What changes for you after updating

- You can delete a run from the viewer. Each run in the list on the Results
  tab has a **Delete** link at its top right. A deleted run goes to the
  trash, and its postings are judged again the next time a run finds them,
  which is what you want after a test run. Before you confirm, the viewer
  says how many application statuses, notes and disagreements you recorded
  on that run, and how many of them `/ftja-review` has not used yet.
- The size of FTJA's data is shown next to the folder path at the top of the
  viewer. Click it to see what takes the space, how old details are offered
  for removal (postings judged more than 60 days ago, at the start of a
  run), and the trash, where a deleted run can be restored or removed for
  good.
- Click the folder path at the top of the viewer to open that folder in
  Finder.
- Each run writes its own digest and rejected file, named after the run's
  start time (`digest-2026-10-11T07-25-59Z.md`). A second run on the same
  day used to overwrite the first one's files. Files from earlier runs keep
  their old names.

## 0.2.8 — 2026-10-11

### What changes for you after updating

- Keywords now match whole words and their forms instead of any run of
  letters. "UTC" no longer matches "outcomes", "work" no longer matches
  "framework", and "CET" no longer matches the French "cet". A keyword that
  was a fragment on purpose (such as "tele") stops matching: replace it with
  the full words you meant.
- Stage 1 passes a job when one sentence triggers a fail criterion but
  another shows the exception that criterion allows for, and leaves the
  weighing to Stage 2. It no longer skips location: only the posting's
  language is treated as already filtered.
- `/ftja-run` checks for a newer version at the start of every run. It used
  to reuse an answer up to 12 hours old, so an update published in between
  was not offered until later.
- The same posting listed for several cities is judged once and shown once,
  with every location on the card.
- Sentences in the Stage 1 popups no longer carry markdown leftovers
  (`\-`, `**`), and a line that was cut before a comma is put back together.
- A job that got through Stage 1 has a "Passed Stage 1 — see why" link that
  opens the sentences Stage 1 read, the keywords, and what it made of each.

## 0.2.7 — 2026-10-08

### What changes for you after updating

- During setup, while the chat is asking "ready to start onboarding?", the
  web view now says to reply `yes` in the chat. It used to show a spinner and
  a "Stuck? Type…" recovery phrase, which did not match what the chat asked.

## 0.2.6 — 2026-10-08

### What changes for you after updating

- Onboarding takes your resume only. Adding a portfolio or a folder is
  switched off during the beta, because the profile drafted from large
  folders was not reliable yet. The button stays visible and says so. Sources
  you already added keep working.
- FTJA is now described as a beta for Claude Code on macOS, which is what it
  has been tested on. The Codex skills are still in the folder, untested.
- The README is rewritten for people who are not developers: what FTJA does,
  how to start, how to use it day to day, what leaves your computer, and
  where it is going.
- Dependency versions are pinned, so a new install gets the versions FTJA was
  tested with.
- The landing page moved to https://ftja.vercel.app and shows a recording of
  a real run. It counts visits with Vercel Web Analytics (no cookies); the
  tool itself still has no analytics.

## 0.2.5 — 2026-10-08

### What changes for you after updating

- The viewer only answers its own page. While it was running, another web
  page open in your browser could have sent it requests, and so changed your
  rubric, search settings or profile summary, or read them. It now refuses
  any request that does not come from `127.0.0.1` itself. Restart the viewer
  after updating (`/ftja-update` does this for you).
- `/ftja-tune` and `/ftja-review` no longer try to `git commit` your
  `rubric.md` and `criteria.json`. Those files are ignored by git on purpose,
  so the step failed; your changes were saved either way.
- The README lists what leaves your computer and where it goes (your AI
  agent's provider, LinkedIn, OpenStreetMap, Google Fonts, GitHub).

## 0.2.4 — 2026-10-07

### What changes for you after updating

- `/ftja-run` opens the viewer every time you start a run, and tells you in
  the chat what it is doing at each step: what it is about to scrape and
  roughly how long that takes, each search term as it finishes, and each
  stage's numbers.

### Fixed

- When the viewer was already running, `/ftja-run` did not bring it up, so
  a run could be under way with nothing on screen.

## 0.2.3 — 2026-10-07

### What changes for you after updating

- The viewer opens on Results instead of Pipeline.

### Fixed

- If the viewer opened on Pipeline, clicking Results showed no earlier runs
  until the page was reloaded.
- Returning to Results keeps the run and tab you were on, and stays on a run
  in progress.

## 0.2.2 — 2026-10-07

### What changes for you after updating

- When a newer version is out, `/ftja-run` asks whether to update before it
  starts, and carries on with the run afterwards. You no longer need to
  know about `/ftja-update`. A scheduled run never updates on its own.

## 0.2.1 — 2026-10-07

### What changes for you after updating

- Nothing to do. If you keep more than one FTJA folder on the same
  computer, `/ftja-run` now makes sure the viewer is showing the folder it
  is running in.

### Fixed

- Two FTJA folders share the viewer's address. A run started in one folder
  used whatever viewer was already open, even if it belonged to the other
  folder, so the run never appeared. `/ftja-run` now checks which folder
  the viewer is showing and switches it, and tells you when it did.
- The viewer shows its version and the folder it is reading under the
  title.

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
