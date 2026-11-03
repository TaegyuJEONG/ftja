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

## Sharing company job boards

FTJA reads a posting from the company's own job board (Greenhouse, Lever,
Ashby and others) when it knows the board, instead of LinkedIn's page. The
boards it knows ship in `ats_boards.json`; the ones your runs found are kept
in your folder's `ats_local.json`.

The simplest way to share them is the question the web view asks once: on a
yes, each finished run sends the boards a posting confirmed (company name,
board vendor and board name) with a random id made in your folder, kept in
`ats_share.json`. `venv/bin/python -m ftja.share status` shows what is
waiting and what was sent; `venv/bin/python -m ftja.share never` stops it.

To share them through a pull request instead:

1. `venv/bin/python -m ftja.ats export` writes `ats_contributions.json`:
   company name, board vendor and board name only — nothing about your
   searches, your criteria or the postings you looked at.
2. Open a pull request that adds that file as `contributions/ats/<any name>.json`.

A check reads every board in it and fails on any that lists no postings.
The maintainer then adds the passing ones with
`python -m ftja.ats merge contributions/ats/<file> --source "<your GitHub name>"`.

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
