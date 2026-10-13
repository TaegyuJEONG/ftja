import sys

# Windows prints in the system code page, not UTF-8, when output goes to a
# pipe, which is how a coding agent reads every `python -m ftja...` command.
# A posting or keyword outside that code page would fail to print at all.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")
