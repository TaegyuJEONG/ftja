"""Company job boards (ATS): read a posting from the company's own board
instead of LinkedIn's page, when the company is known to use one.

LinkedIn's search still finds the postings (company, title, URL). For each
one, the company is looked up in two lists of boards:

- ats_boards.json, shipped with FTJA: boards other people found, each
  checked to list postings before it was added (see `verify`);
- ats_local.json in the project folder: boards this folder found itself,
  and companies already looked for and not found.

A company in neither is looked for once on the public board APIs, by guessing
its board name from the company name (`probe`), and the answer is kept in
ats_local.json; a miss is looked for again after RECHECK_DAYS. One request
reads every posting on a board, with its full text, in about a second; a
LinkedIn posting page is one request per posting with gaps between them.

A posting is taken from the board only when the board has a posting with the
same title; otherwise LinkedIn's page is read as before. Only public,
unauthenticated board APIs are used. Workday, iCIMS and SuccessFactors are
left out: their boards are read one posting at a time, differently for each
company.

`python -m ftja.ats export` writes the boards this folder found that the
shipped list does not have yet (company, vendor, board name; nothing about
your searches) for a pull request; `python -m ftja.ats verify FILE` checks
every entry of a list still answers with postings.
"""
import argparse
import html
import json
import os
import re
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

SHARED_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ats_boards.json")
LOCAL_FILE = "ats_local.json"
JOBS_FILE = "ats_jobs.json"  # postings read from a board: which board, to check later that they are still on it
CLOSED_REASON = "Closed: no longer on the company's job board (checked automatically)."
EXPORT_FILE = "ats_contributions.json"
RECHECK_DAYS = 30
TIMEOUT = 20
WORKERS = 16
UA = {"User-Agent": "Mozilla/5.0 (FTJA; +https://github.com/TaegyuJEONG/ftja)", "Accept": "application/json"}

# vendor -> list URL for a board name; every one answers with all of a board's postings
LIST_URL = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{s}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{s}?mode=json",
    "lever_eu": "https://api.eu.lever.co/v0/postings/{s}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{s}",
    "workable": "https://apply.workable.com/api/v1/widget/accounts/{s}?details=true",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{s}/postings?limit=100",
    "recruitee": "https://{s}.recruitee.com/api/offers/",
    "teamtailor": "https://{s}/jobs.rss",
    "personio": "https://{s}/xml",
    "bamboohr": "https://{s}/careers/list",
}
# what `probe` tries, in order, for each guessed name (Personio and BambooHR names cannot be guessed reliably)
PROBED = ("greenhouse", "lever", "ashby", "workable", "smartrecruiters", "recruitee", "lever_eu", "teamtailor")
# the board name is part of a hostname or a URL path; anything else is not a board name
SLUG_RE = {"teamtailor": r"[a-z0-9-]+\.teamtailor\.com", "personio": r"[a-z0-9-]+\.jobs\.personio\.(?:de|com)",
           "bamboohr": r"[a-z0-9-]+\.bamboohr\.com"}
SLUG_DEFAULT = r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}"
# a careers URL -> (vendor, board name), for lists written as URLs
URL_RE = [
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)"),
    ("greenhouse", r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)"),
    ("lever_eu", r"jobs\.eu\.lever\.co/([A-Za-z0-9._-]+)"),
    ("lever", r"jobs\.lever\.co/([A-Za-z0-9._-]+)"),
    ("lever", r"api\.lever\.co/v0/postings/([A-Za-z0-9._-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([A-Za-z0-9._%-]+)"),
    ("ashby", r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9._-]+)"),
    ("workable", r"apply\.workable\.com/([A-Za-z0-9_-]+)"),
    ("smartrecruiters", r"(?:careers|jobs)\.smartrecruiters\.com/([A-Za-z0-9_-]+)"),
    ("recruitee", r"([a-z0-9-]+)\.recruitee\.com"),
    ("teamtailor", r"([a-z0-9-]+\.teamtailor\.com)"),
    ("personio", r"([a-z0-9-]+\.jobs\.personio\.(?:de|com))"),
    ("bamboohr", r"([a-z0-9-]+\.bamboohr\.com)"),
]
NOT_BOARDS = {"embed", "jobs", "v1", "api", "careers", "www", "app", "apply", "widget", "job_board"}


GENDER_RE = re.compile(r"\(\s*(?:all genders|[mwfdxhi]\s*(?:[/|,*]\s*[mwfdxhi]\s*)+)\)", re.I)


def norm(text) -> str:
    """A title as compared: case, punctuation and a gender note ("(m/f/d)") left out,
    which one site shows and the other does not."""
    return re.sub(r"[^a-z0-9]+", " ", GENDER_RE.sub(" ", str(text or "")).lower()).strip()


def company_key(name) -> str:
    """The same company under small differences of its name ("Acme GmbH", "ACME")."""
    return " ".join(re.sub(r"\b(gmbh|ag|se|inc|ltd|llc|limited|group|co|kgaa|plc|sa|sas|bv)\b", "", norm(name)).split())


def slugs(name) -> list[str]:
    """Board names a company is likely to use: "Acme Labs GmbH" -> acme-labs, acmelabs, acme, ..."""
    base = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    trimmed = re.sub(r"-(gmbh|ag|se|inc|ltd|llc|group|technologies|technology|labs|io|hq)$", "", base)
    return [s for s in dict.fromkeys([base, base.replace("-", ""), trimmed, trimmed.replace("-", "")]) if s]


def valid_board(vendor: str, slug: str) -> bool:
    return vendor in LIST_URL and bool(re.fullmatch(SLUG_RE.get(vendor, SLUG_DEFAULT), str(slug or "")))


def board_from_url(url: str):
    """(vendor, board name) from a careers URL, or None."""
    for vendor, pattern in URL_RE:
        m = re.search(pattern, str(url or ""))
        if m and m.group(1).lower() not in NOT_BOARDS and valid_board(vendor, m.group(1)):
            return vendor, m.group(1)
    return None


def plain(text) -> str:
    """HTML (sometimes escaped twice) -> text with line breaks."""
    return re.sub(r"\s+\n", "\n", re.sub(r"<[^>]+>", "\n", html.unescape(html.unescape(str(text or ""))))).strip()


def _fetch(url: str, as_json: bool = True):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=TIMEOUT) as response:
            body = response.read()
        return json.loads(body) if as_json else body
    except Exception:  # noqa: BLE001 - a board that does not answer is a board with nothing on it
        return None


# ---- reading a board ---------------------------------------------------------

def postings(vendor: str, slug: str) -> list[dict]:
    """[{title, description, url?, ref?}] for every posting on a board, from one
    request. SmartRecruiters and BambooHR lists carry no text: `text_of` reads
    it for the one posting that is needed. [] when the board cannot be read."""
    if not valid_board(vendor, slug):
        return []
    url = LIST_URL[vendor].format(s=slug)
    if vendor == "teamtailor":
        return _teamtailor(_fetch(url, as_json=False))
    if vendor == "personio":  # the English feed can carry text the default one lacks, and other titles
        return _personio(_fetch(url, as_json=False), _fetch(url + "?language=en", as_json=False))
    data = _fetch(url)
    if vendor == "greenhouse":
        return [{"title": j.get("title"), "description": plain(j.get("content")), "url": j.get("absolute_url")}
                for j in (data or {}).get("jobs") or [] if isinstance(data, dict)]
    if vendor in ("lever", "lever_eu"):
        return [{"title": j.get("text"), "url": j.get("hostedUrl"),
                 "description": "\n".join(filter(None, [j.get("descriptionPlain"), *(
                     f"{x.get('text')}\n{plain(x.get('content'))}" for x in j.get("lists") or []),
                     j.get("additionalPlain")]))}
                for j in (data if isinstance(data, list) else [])]
    if vendor == "ashby":
        return [{"title": j.get("title"), "url": j.get("jobUrl"),
                 "description": j.get("descriptionPlain") or plain(j.get("descriptionHtml"))}
                for j in (data or {}).get("jobs") or [] if isinstance(data, dict)]
    if vendor == "workable":
        return [{"title": j.get("title"), "description": plain(j.get("description")), "url": j.get("url")}
                for j in (data or {}).get("jobs") or [] if isinstance(data, dict)]
    if vendor == "recruitee":
        return [{"title": j.get("title"), "url": j.get("careers_url"),
                 "description": "\n".join(filter(None, [plain(j.get("description")), plain(j.get("requirements"))]))}
                for j in (data or {}).get("offers") or [] if isinstance(data, dict)]
    if vendor == "smartrecruiters":
        return [{"title": j.get("name"), "ref": j.get("ref"), "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}"}
                for j in (data or {}).get("content") or [] if isinstance(data, dict)]
    if vendor == "bamboohr":
        return [{"title": j.get("jobOpeningName"), "ref": f"https://{slug}/careers/{j.get('id')}/detail",
                 "url": f"https://{slug}/careers/{j.get('id')}"}
                for j in (data or {}).get("result") or [] if isinstance(data, dict) and str(j.get("id", "")).isdigit()]
    return []


def _teamtailor(body) -> list[dict]:
    try:
        root = ET.fromstring(body)
    except (ET.ParseError, TypeError):
        return []
    return [{"title": i.findtext("title"), "description": plain(i.findtext("description")), "url": i.findtext("link")}
            for i in root.iter("item")]


def _personio(*bodies) -> list[dict]:
    by_id: dict[str, dict] = {}
    for body in bodies:
        try:
            root = ET.fromstring(body)
        except (ET.ParseError, TypeError):
            continue
        for job in root.findall(".//position"):
            sections = ["\n".join(filter(None, [s.findtext("name"), plain(s.findtext("value"))]))
                        for s in job.findall("./jobDescriptions/jobDescription")]
            entry = by_id.setdefault(job.findtext("id") or job.findtext("name") or "", {"titles": [], "description": ""})
            if job.findtext("name") and job.findtext("name") not in entry["titles"]:
                entry["titles"].append(job.findtext("name"))
            entry["description"] = "\n".join(filter(None, sections)) or entry["description"]
    return [{"title": title, "description": entry["description"], "posting_id": posting_id}
            for posting_id, entry in by_id.items() for title in entry["titles"]]


def text_of(posting: dict) -> str:
    """The posting's text: already on the list, or one more request (SmartRecruiters, BambooHR)."""
    if (posting.get("description") or "").strip() or not posting.get("ref"):
        return (posting.get("description") or "").strip()
    data = _fetch(posting["ref"]) or {}
    if "jobAd" in data:  # SmartRecruiters
        sections = (data.get("jobAd") or {}).get("sections") or {}
        return "\n".join(plain((sections.get(k) or {}).get("text")) for k in sections).strip()
    opening = (data.get("result") or {}).get("jobOpening") or {}  # BambooHR
    return plain(opening.get("description")) if opening.get("jobOpeningStatus") == "Open" else ""


def same_title(board: list[dict], title) -> dict | None:
    """The board's posting with this title. Several different postings under one
    title (Personio requisitions) is no answer: which one LinkedIn showed is unknown."""
    matches = [p for p in board if norm(p.get("title")) == norm(title)]
    if len({(p.get("description") or p.get("ref") or "").strip() for p in matches}) > 1:
        return None
    return matches[0] if matches else None


def probe(company) -> dict | None:
    """{vendor, slug} of the first guessed board name that lists postings, or None."""
    for slug in slugs(company):
        for vendor in PROBED:
            if vendor == "teamtailor":
                slug_here = f"{slug}.teamtailor.com"
                if len(slug) > 63 or not valid_board(vendor, slug_here):
                    continue
            else:
                slug_here = slug
            if postings(vendor, slug_here):
                return {"vendor": vendor, "slug": slug_here}
    return None


# ---- the lists of boards -----------------------------------------------------

def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: str, value: dict):
    folder = os.path.dirname(os.path.abspath(path))
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=folder, suffix=".tmp", delete=False) as out:
        json.dump(value, out, ensure_ascii=False, indent=1, sort_keys=True)
        temporary = out.name
    os.replace(temporary, path)


def shared_boards(path: str = SHARED_PATH) -> dict:
    return _load(path).get("boards") or {}


class Boards:
    """One run's view of the boards: the shipped list, this folder's own, and
    every board read so far (each board is read once a run)."""

    def __init__(self, project_dir: str = ".", shared_path: str = SHARED_PATH):
        self.local_path = os.path.join(project_dir, LOCAL_FILE)
        self.shared = shared_boards(shared_path)
        self.local = _load(self.local_path)
        self.jobs_path = os.path.join(project_dir, JOBS_FILE)
        self.jobs = _load(self.jobs_path)
        self.read: dict[str, list[dict]] = {}
        self.stats = {"from_ats": 0, "found_now": 0}

    def board_of(self, company):
        key = company_key(company)
        entry = self.shared.get(key) or self.local.get(key) or {}
        return (entry["vendor"], entry["slug"]) if entry.get("vendor") and entry.get("slug") else None

    def discover(self, companies):
        """Look for the boards of companies in neither list (or missed more than
        RECHECK_DAYS ago), in parallel, and keep the answers in ats_local.json."""
        stale = (datetime.now(timezone.utc) - timedelta(days=RECHECK_DAYS)).isoformat()
        todo, seen = [], set()
        for name in companies:
            key = company_key(name)
            if not key or key in seen or key in self.shared:
                continue
            seen.add(key)
            entry = self.local.get(key)
            if entry is None or (not entry.get("vendor") and entry.get("checked_at", "") < stale):
                todo.append(name)
        if not todo:
            return
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            found = list(pool.map(probe, todo))
        now = datetime.now(timezone.utc).isoformat()
        for name, board in zip(todo, found):
            self.local[company_key(name)] = {"name": str(name), "checked_at": now, **(board or {})}
            self.stats["found_now"] += bool(board)
        _save(self.local_path, self.local)

    def board(self, vendor: str, slug: str) -> list[dict]:
        key = f"{vendor}/{slug}"
        if key not in self.read:
            self.read[key] = postings(vendor, slug)
        return self.read[key]

    def details(self, job: dict) -> dict:
        """What the company's board says about this posting ({description, _source,
        job_url_direct}), or {} when its board is unknown or has no posting by that title."""
        found = self.board_of(job.get("company"))
        if not found:
            return {}
        posting = same_title(self.board(*found), job.get("title"))
        text = text_of(posting) if posting else ""
        if not text:
            return {}
        self.stats["from_ats"] += 1
        out = {"description": text, "_source": f"ats:{found[0]}/{found[1]}"}
        if job.get("job_url"):
            self.jobs[job["job_url"]] = {"source": out["_source"], "title": job.get("title"),
                                         "read_at": datetime.now(timezone.utc).isoformat()}
        if posting.get("url"):
            out["job_url_direct"] = posting["url"]
        return out

    def save_jobs(self):
        _save(self.jobs_path, self.jobs)

    def still_open(self, source: str, title) -> bool | None:
        """Whether a posting first read from a board ("ats:vendor/slug") is still on
        it: True, False, or None when the board does not answer (it tells nothing)."""
        vendor, _, slug = str(source or "").removeprefix("ats:").partition("/")
        board = self.board(vendor, slug) if valid_board(vendor, slug) else []
        if not board:
            return None
        return any(norm(p.get("title")) == norm(title) for p in board)


def close_gone(project_dir: str = ".", db_path: str | None = None) -> dict:
    """Mark Expired the passed postings that were read from a company board and
    are no longer on it, unless the candidate already decided something. One
    request per board, so this is cheap enough to do at the start of every run.
    A board that does not answer closes nothing."""
    from ftja.state import connect, record_decision

    boards = Boards(project_dir)
    if not boards.jobs:
        return {"checked": 0, "closed": 0}
    db_path = db_path or os.path.join(project_dir, "seen.db")
    if not os.path.exists(db_path):
        return {"checked": 0, "closed": 0}
    checked = closed = 0
    with connect(db_path) as conn:
        rows = conn.execute("SELECT job_url, user_action FROM seen_jobs WHERE status IN ('passed', 'review')").fetchall()
        shown = {url: action for url, action in rows}
        for url, entry in list(boards.jobs.items()):
            if url not in shown:
                continue  # not passed (or not judged yet): nobody is waiting on it
            if shown[url]:
                del boards.jobs[url]  # decided already: never checked again
                continue
            checked += 1
            if boards.still_open(entry.get("source"), entry.get("title")) is False:
                record_decision(conn, url, "expired", CLOSED_REASON)
                del boards.jobs[url]
                closed += 1
    boards.save_jobs()
    return {"checked": checked, "closed": closed}


# ---- sharing -----------------------------------------------------------------

def export(project_dir: str = ".", shared_path: str = SHARED_PATH) -> dict:
    """The boards this folder found that the shipped list has not got, as a list
    to send in: company name, vendor and board name only."""
    shared = shared_boards(shared_path)
    found = {k: {"name": v.get("name") or k, "vendor": v["vendor"], "slug": v["slug"]}
             for k, v in _load(os.path.join(project_dir, LOCAL_FILE)).items()
             if v.get("vendor") and v.get("slug") and k not in shared}
    path = os.path.join(project_dir, EXPORT_FILE)
    _save(path, {"boards": found})
    return {"path": path, "boards": len(found)}


def verify(boards: dict) -> dict:
    """{key: reason} for every entry that is not a board listing postings."""
    def check(item):
        key, entry = item
        if not isinstance(entry, dict) or key != company_key(entry.get("name")):
            return key, "the key is not company_key(name)"
        if not valid_board(entry.get("vendor"), entry.get("slug")):
            return key, "not a known vendor and board name"
        return key, None if postings(entry["vendor"], entry["slug"]) else "the board lists no postings"
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return {k: why for k, why in pool.map(check, boards.items()) if why}


def merge(into_path: str, from_path: str, source: str) -> dict:
    """Add a contributed list's verified boards to the shipped list; entries already there stay as they are."""
    data = _load(into_path)
    boards = data.setdefault("boards", {})
    new = {k: v for k, v in (_load(from_path).get("boards") or {}).items() if k not in boards}
    bad = verify(new)
    for k, v in new.items():
        if k not in bad:
            boards[k] = {"name": v["name"], "vendor": v["vendor"], "slug": v["slug"], "source": source}
    _save(into_path, data)
    return {"added": len(new) - len(bad), "rejected": bad}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("export", help="write the boards this folder found for a pull request")
    check = sub.add_parser("verify", help="check every entry of a list answers with postings")
    check.add_argument("file")
    check.add_argument("--only-new-against", help="check only the entries this other list has not got")
    add = sub.add_parser("merge", help="add a contributed list's verified boards to ats_boards.json")
    add.add_argument("file")
    add.add_argument("--source", default="contributed")
    args = parser.parse_args()
    if args.command == "export":
        print(json.dumps(export(), indent=1))
    elif args.command == "merge":
        print(json.dumps(merge(SHARED_PATH, args.file, args.source), indent=1))
    else:
        boards = _load(args.file).get("boards") or {}
        if args.only_new_against:
            old = _load(args.only_new_against).get("boards") or {}
            boards = {k: v for k, v in boards.items() if old.get(k) != v}
        bad = verify(boards)
        print(json.dumps({"checked": len(boards), "failed": bad}, ensure_ascii=False, indent=1))
        sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
