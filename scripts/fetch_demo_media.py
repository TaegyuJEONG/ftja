#!/usr/bin/env python3
"""Download the demo recording into a built landing site.

The recording (8.5 MB) is not in the repository, so that installing FTJA
does not download it. It is attached to the `media` release instead, and
the two landing builds (vercel.json and .github/workflows/pages.yml) call
this to put it where landing.html and the README expect it:
<site>/assets/demo/. To replace the recording, upload files with the same
names to that release (`gh release upload media <file> --clobber`) and
redeploy.

Usage: python3 scripts/fetch_demo_media.py _site
"""
import sys
import urllib.request
from pathlib import Path

RELEASE = "https://github.com/TaegyuJEONG/ftja/releases/download/media"
FILES = ("ftja-run.mp4", "ftja-run.gif")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.strip().splitlines()[-1])
    target = Path(sys.argv[1]) / "assets" / "demo"
    target.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        with urllib.request.urlopen(f"{RELEASE}/{name}", timeout=60) as response:
            data = response.read()
        if len(data) < 100_000:  # an error page, not a recording
            raise SystemExit(f"{name}: only {len(data)} bytes came back from the media release")
        (target / name).write_bytes(data)
        print(f"demo media: {name} ({len(data) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
