"""Sharing the company job boards a folder's runs found, when its user agreed.

The viewer asks once. On a yes, every finished run sends the boards a posting
confirmed (the same ones `ftja.ats export` writes) that the shipped list has
not got: company name, board vendor and board name, nothing about searches,
criteria or postings. Each folder is a random install id made here, so the
receiving side can count how many installs agreed without knowing who.

The answer and the id live in `ats_share.json` in the folder. Sending never
stops a run: any failure is reported and the boards go with the next run.

    venv/bin/python -m ftja.share status
    venv/bin/python -m ftja.share yes | no | never
    venv/bin/python -m ftja.share send
"""
import argparse
import json
import os
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from ftja import ats

SETTINGS_FILE = "ats_share.json"
CHOICES = ("yes", "no", "never")  # no: ask again later; never: do not ask again
# the public (publishable) key of FTJA's Supabase project: it can only call contribute_ats_boards()
ENDPOINT = "https://zkcdthveyelyzrazzxbs.supabase.co/rest/v1/rpc/contribute_ats_boards"
PUBLIC_KEY = "sb_publishable_EShL4Kclk2WjwKK_WRbxpw_6FyjPhFy"
ASK_AGAIN_DAYS = 14  # after "Not now"
TIMEOUT = 15
BATCH = 500


def _path(project_dir: str) -> str:
    return os.path.join(project_dir, SETTINGS_FILE)


def settings(project_dir: str = ".") -> dict:
    try:
        with open(_path(project_dir), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(project_dir: str, data: dict):
    with open(_path(project_dir), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def unsent(project_dir: str = ".", shared_path: str | None = None) -> list[dict]:
    """Confirmed boards the shipped list has not got and this folder has not sent yet."""
    sent = set(settings(project_dir).get("sent") or [])
    shared = ats.shared_boards(shared_path or ats.SHARED_PATH)
    out = []
    for key, v in ats._load(os.path.join(project_dir, ats.LOCAL_FILE)).items():
        if (v.get("vendor") and v.get("slug") and v.get("confirmed") and key not in shared
                and ats.valid_board(v["vendor"], v["slug"]) and f'{v["vendor"]}/{v["slug"]}' not in sent):
            out.append({"key": key, "name": v.get("name") or key, "vendor": v["vendor"], "slug": v["slug"]})
    return out


def status(project_dir: str = ".") -> dict:
    """What the viewer needs: the answer so far, and how many boards wait to be shared."""
    s = settings(project_dir)
    choice, waiting = s.get("choice") or "", len(unsent(project_dir))
    snoozed = choice == "no" and (s.get("decided_at") or "") > (
        datetime.now(timezone.utc) - timedelta(days=ASK_AGAIN_DAYS)).isoformat()
    return {"choice": choice, "waiting": waiting, "ask": bool(waiting) and choice in ("", "no") and not snoozed,
            "sent": len(s.get("sent") or []), "last_sent_at": s.get("last_sent_at") or ""}


def _call(install_id: str, boards: list[dict]) -> int:
    req = urllib.request.Request(
        ENDPOINT, method="POST",
        data=json.dumps({"p_install": install_id, "p_boards": boards}, ensure_ascii=False).encode(),
        headers={"apikey": PUBLIC_KEY, "Authorization": f"Bearer {PUBLIC_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return int(json.load(r) or 0)


def choose(project_dir: str, choice: str) -> dict:
    """Record the user's answer. A yes registers this folder's install id and sends what waits."""
    if choice not in CHOICES:
        raise ValueError(f"choice must be one of {CHOICES}")
    s = settings(project_dir)
    s.update(choice=choice, decided_at=datetime.now(timezone.utc).isoformat())
    if choice == "yes":
        s.setdefault("install_id", str(uuid.uuid4()))
    _save(project_dir, s)
    if choice != "yes":
        return {"choice": choice}
    try:
        _call(s["install_id"], [])  # counted as agreed even before a board is found
    except (urllib.error.URLError, OSError, ValueError) as err:
        return {"choice": choice, "error": f"saved; could not reach the server yet ({err})"}
    return {"choice": choice, **send(project_dir)}


def send(project_dir: str = ".") -> dict:
    """Send the boards that wait, if the user said yes. Never raises."""
    s = settings(project_dir)
    if s.get("choice") != "yes" or not s.get("install_id"):
        return {"sent": 0, "skipped": "not shared"}
    boards = unsent(project_dir)
    if not boards:
        return {"sent": 0}
    before = list(s.get("sent") or [])
    done = list(before)
    try:
        for i in range(0, len(boards), BATCH):
            part = boards[i:i + BATCH]
            _call(s["install_id"], part)
            done += [f'{b["vendor"]}/{b["slug"]}' for b in part]
    except (urllib.error.URLError, OSError, ValueError) as err:
        s["sent"] = done
        _save(project_dir, s)
        return {"sent": len(done) - len(before), "error": f"could not send, will retry next run ({err})"}
    s.update(sent=done, last_sent_at=datetime.now(timezone.utc).isoformat())
    _save(project_dir, s)
    return {"sent": len(boards)}


def main():
    parser = argparse.ArgumentParser(prog="python -m ftja.share")
    parser.add_argument("action", choices=("status", "send") + CHOICES)
    parser.add_argument("--dir", default=".")
    args = parser.parse_args()
    if args.action == "status":
        out = status(args.dir)
    elif args.action == "send":
        out = send(args.dir)
    else:
        out = choose(args.dir, args.action)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
