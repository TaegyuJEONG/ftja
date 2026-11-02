"""Build ats_boards.json, FTJA's shipped list of company job boards, from
open-source lists whose licence allows it (both MIT), at a fixed commit:

- outscal/OpenJobs data/companies_v2.json (gaming and tech companies)
- santifer/career-ops templates/portals.example.yml (tracked_companies)

Only boards with a public API FTJA reads (ftja.ats.LIST_URL) are kept, and
only those that list postings today (ftja.ats.verify). Entries already in
ats_boards.json are kept as they are; run it again to add what the sources
gained. Needs PyYAML (pip install pyyaml) for career-ops' file.

    python scripts/build_ats_boards.py
"""
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ftja import ats  # noqa: E402

SOURCES = {
    "outscal/OpenJobs@cdcc533 (MIT)":
        "https://raw.githubusercontent.com/outscal/OpenJobs/cdcc533521afb61f4e60657b3dbe06e484ccddcf/data/companies_v2.json",
    "santifer/career-ops@20b9fd3 (MIT)":
        "https://raw.githubusercontent.com/santifer/career-ops/20b9fd3a9c86d28c847e021f4dbbe93ce9a39a00/templates/portals.example.yml",
}


def _get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=ats.UA), timeout=60) as response:
        return response.read()


def candidates() -> dict:
    """{company_key: {name, vendor, slug, source}} from every source; the first source naming a company wins."""
    out = {}
    for source, url in SOURCES.items():
        body = _get(url)
        if url.endswith(".json"):
            rows = [(c.get("name"), c.get("ats_links", []) + c.get("list_urls", [])) for c in json.loads(body)]
        else:
            import yaml
            rows = [(c.get("name"), [c.get("careers_url"), c.get("api")])
                    for c in yaml.safe_load(body).get("tracked_companies") or []]
        for name, urls in rows:
            key = ats.company_key(name)
            board = next(filter(None, (ats.board_from_url(u) for u in urls if u)), None)
            if key and board and key not in out:
                out[key] = {"name": str(name).strip(), "vendor": board[0], "slug": board[1], "source": source}
    return out


def main():
    path = ats.SHARED_PATH
    data = ats._load(path)
    boards = data.setdefault("boards", {})
    new = {k: v for k, v in candidates().items() if k not in boards}
    bad = ats.verify(new)
    boards.update({k: v for k, v in new.items() if k not in bad})
    data["about"] = ("Company job boards FTJA reads postings from (see ftja/ats.py). Each entry was checked to list "
                     "postings when added; `source` says where it came from. Add yours: python -m ftja.ats export, "
                     "then a pull request with the file it writes.")
    ats._save(path, data)
    print(json.dumps({"candidates": len(new), "added": len(new) - len(bad), "rejected": len(bad),
                      "total": len(boards)}, indent=1))


if __name__ == "__main__":
    main()
