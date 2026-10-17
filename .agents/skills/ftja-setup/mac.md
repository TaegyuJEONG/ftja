# FTJA on macOS

Read this before any command when the session runs on macOS. The commands
in the skills are already the macOS forms.

## Rules

- **Every check of the local server has a time limit**:
  `curl -s --max-time 3 ...`.
- **Never cut an FTJA command's output short** with `| head`: read the
  whole output; if it is long, write it to a file and read the part you
  need.

## Start the viewer

In Codex, start it as a long-running command session, as the setup skill's
step 4 says (not detached: the sandbox stops detached children on
Windows).

## Git and Python

FTJA needs Git and Python 3.10 or newer. The Python that comes with macOS
(`/usr/bin/python3`, 3.9) is too old: FTJA stops at its first command.

1. Check `git --version`. If macOS answers by offering to install the
   command line developer tools, tell the user to click Install in that
   window and wait until it finishes.
2. Look for Python 3.10 or newer, in this order, and use the first one that
   prints one: `python3.13`, `python3.12`, `python3.11`, `python3.10`
   (each with `--version`), then `/opt/homebrew/bin/python3`,
   `/usr/local/bin/python3`, then `python3`.
3. If none is 3.10 or newer, ask once, then install it: with
   `brew install python@3.12` when `brew --version` works, otherwise ask
   the user to install Python 3.12 from https://www.python.org/downloads/
   and tell you when it is done.
4. Create the environment with the Python you found:
   `<python> -m venv venv`, then
   `venv/bin/pip install -r requirements.txt`. From then on every command
   uses `venv/bin/python`.
