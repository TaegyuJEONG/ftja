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
temporary folder, runs every onboarding step with the answers in
`tests/fixtures/e2e/`, starts the viewer on a free port and checks what it
serves. Your own folder and the viewer on 8765 are left alone. To see each
step with its timing, or to also check a fresh install:

```bash
venv/bin/python scripts/e2e_onboarding.py --fresh-venv
```

`--keep` leaves the temporary folder in place for a look.

The browser check opens the Pipeline tab in headless Chromium. It needs
the development requirements, once:

```bash
venv/bin/pip install -r requirements-dev.txt
venv/bin/python -m playwright install chromium
```

Then `--browser on --artifacts e2e-out` runs it and saves the report and a
screenshot. On every push, the `onboarding-e2e` job in
`.github/workflows/tests.yml` does all of this from scratch on macOS,
Windows and Linux with Python 3.11 to 3.13. Each run's page shows a table
of steps and timings, and the report is kept as an artifact.

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
