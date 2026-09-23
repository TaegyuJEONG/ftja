# FTJA — Fine Tune Job Agent

Local-first job-search skill for Claude Code or Codex. Full design in [SPEC.md](SPEC.md).

Scrapes LinkedIn (no login, via `jobspy`), filters deterministically, then
runs a 2-stage LLM judgment (cheap model on keyword-sentence blocks, mid
model on full JD + your resume/portfolio/rubric), and drops a daily digest
of only the jobs worth reading. Everything stays in your FTJA folder — no hosted database and no account required.

## Install and first-time setup

FTJA is installed as a normal workspace. Clone it, open the folder in Claude
Code or Codex, and start a new session there:

```bash
git clone https://github.com/<owner>/ftja.git ~/FTJA
cd ~/FTJA
```

Ask the agent to set up FTJA. The project-local `ftja-setup` skill creates the
Python environment when needed, opens the local web experience, and guides you
through creating your own `criteria.json`, `rubric.md`, and profile. You do not
need to choose an AI client or install a global skill.

If you prefer explicit commands, the setup prerequisites are:

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

Then in a new Claude Code or Codex session opened in the FTJA folder, run the
project's FTJA setup skill.

This interviews you and writes local `rubric.md` + `criteria.json` (gitignored —
edit them in the web experience or use `/ftja-tune` to update them
conversationally).

## Running it (M1 — manual, do this first)

```
/ftja-run
```

Do this by hand for a few days. Check `digest-YYYY-MM-DD.md` each time —
if Stage 1/Stage 2 verdicts don't match your own judgment, edit `rubric.md`
/ `criteria.json` (or run `/ftja-tune`) and re-run. Don't move to M2 until
you trust it.

## Automating it (M2 — once M1 is trusted)

```bash
cp com.ftja.run.plist ~/Library/LaunchAgents/com.ftja.run.plist
launchctl load ~/Library/LaunchAgents/com.ftja.run.plist
```

**Before you do this**, `claude --print "/ftja-run"` runs headlessly — no
one is there to click "allow" on a permission prompt at 9am. You need to
either:
- configure a permission allowlist for this project (`.claude/settings.json`)
  covering the Bash/Read/Write calls `/ftja-run` makes, or
- accept the risk of `--dangerously-skip-permissions` in the plist's
  `ProgramArguments` (only do this if you understand what it disables).

This wasn't decided for you on purpose — it's a real safety tradeoff, pick
it deliberately. Whichever you choose, check `launchd.err.log` after the
first scheduled run to confirm it actually executed instead of silently
failing on a permission prompt.

To stop the automation:
```bash
launchctl unload ~/Library/LaunchAgents/com.ftja.run.plist
```

## Layout

```
criteria.example.json, rubric.example.md  # public templates
criteria.json, rubric.md       # personal configuration — gitignored
profile/                   # your resume/portfolio — gitignored
seen.db                    # dedup state (SQLite) — gitignored
digest-*.md                # daily results — gitignored
run.log                    # append-only run history — gitignored
ftja/                      # the pipeline code
.claude/skills/ftja-*/     # the three skills: setup, run, tune
```

## Data and privacy

The public repository contains the FTJA code, prompts, and project-local skills.
Your criteria, rubric, resume/portfolio, run history, digests, and SQLite state
are local files and are gitignored by default. Review the files before making
the repository public; Git history is public too.

## Out of scope (see SPEC.md for why)

Public job board, LinkedIn login-based scraping, non-LinkedIn sources,
non-Claude-Code / mobile use, automatic (non-confirmed) rubric inference,
multi-rubric pivoting, pipeline visualization.
