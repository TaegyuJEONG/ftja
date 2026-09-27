#!/usr/bin/env python3
"""Assert the public FTJA landing entry points stay in sync."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRIES = (ROOT / "index.html", ROOT / "landing.html")


def main() -> None:
    pages = {path.name: path.read_text(encoding="utf-8") for path in ENTRIES}
    if pages["index.html"] != pages["landing.html"]:
        raise SystemExit("index.html and landing.html must be identical public entry points")

    page = pages["landing.html"]
    required = (
        'id="setup-toggle"',
        'id="setup-card"',
        "function toggleSetupCard()",
        "setup-toggle').addEventListener('click', toggleSetupCard",
    )
    missing = [marker for marker in required if marker not in page]
    if missing:
        raise SystemExit(f"landing CTA is incomplete: missing {', '.join(missing)}")
    if 'href="setup.html"' in page:
        raise SystemExit("landing CTA must expand inline, not link to setup.html")

    print("landing source contract: OK (index.html == landing.html; inline CTA)")


if __name__ == "__main__":
    main()
