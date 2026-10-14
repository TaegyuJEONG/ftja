# FTJA on Windows

Read this before any command when the session runs on Windows. Every FTJA
skill follows it; where a skill shows a macOS command, use the form here.

## One shell

Run every command in PowerShell. Do not mix in Git Bash commands (`curl`,
`tail`, `head`, `ls`): the two shells quote and pipe differently, and a
command that mixes them can stall or be cut short.

## Commands

| macOS form in the skills | Windows form |
|---|---|
| `venv/bin/python`, `venv/bin/pip` | `venv\Scripts\python`, `venv\Scripts\pip` |
| `python3 -m venv venv` | `<python> -m venv venv` (see "Python" below) |
| `curl -s http://127.0.0.1:8765/...` | `Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 http://127.0.0.1:8765/...` |
| `open <url or folder>` | `Start-Process <url or folder>` |
| `lsof -tiTCP:8765 -sTCP:LISTEN` | `(Get-NetTCPConnection -LocalPort 8765 -State Listen).OwningProcess` |
| `launch.json` `runtimeExecutable` `venv/bin/python` | `venv\\Scripts\\python.exe` |

## Rules

- **JSON arguments.** PowerShell strips the quotes out of JSON given on the
  command line. Wherever a skill shows `--json '{...}'`, write that JSON to
  a file under `.ftja-run/` with your file-writing tool and pass
  `--json-file <that file>`. Write every file a skill passes with your
  file-writing tool, not `>` or `Set-Content`, which do not write plain
  UTF-8.
- **Never cut an FTJA command's output short.** `| Select-Object -First n`
  stops the command while it is still writing: it ends with exit code 255
  and you lose the rest of its answer. Read the whole output; if it is long,
  write it to a file and read the part you need.
- **Every check of the local server has a time limit**
  (`-TimeoutSec 3`). Without one, a check while no server is running can
  wait for minutes.

## Start the viewer

From the FTJA folder (it keeps running after the command returns):

```powershell
Start-Process -FilePath "<FTJA folder>\venv\Scripts\python.exe" -ArgumentList "-m","ftja.server" -WorkingDirectory "<FTJA folder>" -WindowStyle Hidden
```

## Git and Python

A fresh Windows PC often has neither. A `python` that answers "Python est
introuvable" / "Python was not found" or opens the Microsoft Store is not
Python.

1. Look for Python before calling it missing, in this order, and use the
   first one that prints Python 3.10 or newer: `py -3 --version`, then
   `$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe --version`
   (newest folder first). Python is often installed but not on the PATH.
2. If Git or Python is missing, ask once, for both together, then install
   with `winget install --id Git.Git -e` and
   `winget install --id Python.Python.3.12 -e`.
3. If `winget` answers that the package is already installed, do not try
   again: look in the places in step 1. After an install, look there too:
   a PATH changed by an install does not reach this shell.
4. Create the environment with the Python you found:
   `<python> -m venv venv`, then
   `venv\Scripts\pip install -r requirements.txt`. From then on every
   command uses `venv\Scripts\python`.
