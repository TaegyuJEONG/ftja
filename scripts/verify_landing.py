#!/usr/bin/env python3
"""Assert the public FTJA landing entry points stay in sync."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
def main() -> None:
    page = (ROOT / "landing.html").read_text(encoding="utf-8")
    required = (
        'id="setup-toggle"',
        'id="setup-card"',
        "function toggleSetupCard()",
        "setup-toggle').addEventListener('click', toggleSetupCard",
    )
    missing = [marker for marker in required if marker not in page]
    if missing:
        raise SystemExit(f"landing CTA is incomplete: missing {', '.join(missing)}")
    print("landing source contract: OK (canonical landing.html; inline CTA)")


if __name__ == "__main__":
    main()
