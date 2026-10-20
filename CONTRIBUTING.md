# Contributing to FTJA

Thanks for your interest in FTJA. Ideas, bug reports, and pull requests are
all welcome.

## How pull requests are handled

FTJA runs on users' own machines and includes agent skills that an AI coding
agent follows as instructions. Because of that, pull requests are treated as
**proposals**:

- The maintainer reads a pull request to understand what it is trying to do.
- Accepted changes may be **re-implemented by the maintainer** instead of
  being merged as submitted. Your pull request may then be closed with a link
  to the commit that implements the idea.
- When your idea is used, you are credited with a `Co-authored-by` trailer
  in the commit or a credit in the pull request.

This is not a judgment of your code. It keeps the code that reaches users'
machines written and reviewed in one place.

## What makes a proposal easy to accept

- **Explain the problem first.** Describe what you ran into and why it matters,
  before the solution.
- **Keep it small.** One idea per pull request.
- **Call out sensitive changes.** Say so explicitly if your change touches any
  of these:
  - skill files under `.claude/skills/` or `.agents/skills/`;
  - dependencies (`requirements.txt`);
  - GitHub workflows under `.github/workflows/`;
  - anything that sends data to a network address or runs downloaded code.
- **No personal data.** Never include your own `criteria.json`, `rubric.md`,
  `profile/`, `seen.db`, digests, or logs. These are git-ignored on purpose.

## Testing setup without redoing it by hand

`venv/bin/python -m pytest` runs everything, including
`tests/test_e2e_onboarding.py`: it copies the working tree into an empty
temporary folder, starts the viewer there on a free port, and plays both
sides of setup with the answers in `tests/fixtures/e2e/`: the agent runs
the `ftja.onboarding` commands and a `wait-for-action` for each decision,
and the user makes that decision on the page. Your own folder and the
viewer on 8765 are left alone. To see each step with its timing, or to also
check a fresh install:

```bash
venv/bin/python scripts/e2e_onboarding.py --fresh-venv
```

`--keep` leaves the temporary folder in place for a look.

Under pytest the user sends the page's requests directly. With Playwright
the user clicks the page's own buttons in headless Chromium instead. It
needs the development requirements, once:

```bash
venv/bin/pip install -r requirements-dev.txt
venv/bin/python -m playwright install chromium
```

Then `--browser on --artifacts e2e-out` runs it and saves the report and a
screenshot of each card. On every push, the `onboarding-e2e` job in
`.github/workflows/tests.yml` does all of this from scratch on macOS,
Windows and Linux with Python 3.11 to 3.13. Each run's page shows a table
of steps and timings, and the report is kept as an artifact.

## Setup run by a real agent

`scripts/agent_e2e.py` runs `/ftja-setup` with Claude Code itself
(`claude -p`, Sonnet by default) in an empty copy of the folder, while a
headless browser answers each card on the web view. It tests the skill as
a user's agent reads it, from creating the venv to writing the files, and
reports what the agent cost, its tokens, time and turns, the permission
prompts it hit, and how long the user waited for each card.

```bash
venv/bin/python scripts/agent_e2e.py
```

The agent gets no MCP servers, only this project's settings, its own
viewer port (`FTJA_PORT`) and an `open` that only logs, so your viewer on
8765 and your browser are left alone. Reports, the agent's transcript and a
screenshot of every card go to `agent-e2e-out/`. `--model` and `--runs N`
(a pass rate) are there for comparing.

It needs `claude` logged in, or `CLAUDE_CODE_OAUTH_TOKEN` from
`claude setup-token`; runs count against that subscription. On GitHub,
Actions -> Agent setup E2E -> Run workflow does the same on clean macOS,
Windows and Linux runners, once the `CLAUDE_CODE_OAUTH_TOKEN` repository
secret is set. It never runs on push.

## Reporting bugs and ideas

Open an issue with what you expected, what happened, and the steps to
reproduce. For ideas, check `BACKLOG.md` first. It lists work that has
already been considered and deliberately deferred.

## Security

If you find a security problem, do not open a public issue or pull request
with the details. Report it privately through the repository's **Security**
tab using **Report a vulnerability**.

## Releasing a version

Bump `VERSION`, add a section to `CHANGELOG.md` that starts with "What
changes for you after updating", merge to `main`, then tag it
(`git tag v0.2.0 && git push --tags`) and publish a GitHub release with the
same notes. Existing installs learn about the update from `VERSION` on
`main`.
