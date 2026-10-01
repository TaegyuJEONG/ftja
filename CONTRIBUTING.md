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

## Reporting bugs and ideas

Open an issue with what you expected, what happened, and the steps to
reproduce. For ideas, check `BACKLOG.md` first. It lists work that has
already been considered and deliberately deferred.

## Security

If you find a security problem, do not open a public issue or pull request
with the details. Report it privately through the repository's **Security**
tab using **Report a vulnerability**.
