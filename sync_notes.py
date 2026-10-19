"""Sync Simplenote notes into notes.db using the live Simperium sync API.

Usage:
    python sync_notes.py            # incremental sync
    python sync_notes.py --full     # ignore cache, refetch everything
    python sync_notes.py --limit 50 # only first 50 index entries (testing)

Token resolution order: env SIMPLENOTE_TOKEN, .token file, automatic extraction
via .venv (get_token.py) which reads the desktop app's Local Storage.

Outputs:
    notes.db              SQLite: one row per note, latest version only
    pending_changes.json  notes added/updated/deleted since the previous sync
"""
import argparse
import concurrent.futures
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

APP_ID = "chalk-bump-f49"
BASE = f"https://api.simperium.com/1/{APP_ID}"
HERE = pathlib.Path(__file__).parent
DB_PATH = HERE / "notes.db"
PENDING_PATH = HERE / "pending_changes.json"
WORKERS = 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    version INTEGER,
    content TEXT,
    creation_ts REAL,
    modification_ts REAL,
    deleted INTEGER,
    tags TEXT,
    system_tags TEXT,
    synced_at TEXT
);
CREATE TABLE IF NOT EXISTS sync_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def get_token() -> str:
    token = os.environ.get("SIMPLENOTE_TOKEN", "").strip()
    if token:
        return token
    token_file = HERE / ".token"
    if token_file.exists() and token_file.read_text(encoding="utf-8").strip():
        return token_file.read_text(encoding="utf-8").strip()
    venv_py = HERE / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        subprocess.run([str(venv_py), str(HERE / "get_token.py")], check=True)
        if token_file.exists():
            return token_file.read_text(encoding="utf-8").strip()
    sys.exit("no token found: run get_token.py or set SIMPLENOTE_TOKEN")


def api_get(path: str, token: str, retries: int = 3):
    url = f"{BASE}{path}"
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"X-Simperium-Token": token, "User-Agent": "Simplenote/2.27.1"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as err:  # noqa: BLE001 - network retry loop
            last_err = err
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last_err}")


def fetch_index(token: str, limit: int | None = None) -> list[dict]:
    entries, mark, pages = [], None, 0
    while True:
        path = "/note/index" + (f"?mark={mark}" if mark else "")
        page = api_get(path, token)
        entries.extend(page.get("index", []))
        mark = page.get("mark")
        pages += 1
        if limit and len(entries) >= limit:
            return entries[:limit]
        if not mark or not page.get("index"):
            return entries


def fetch_note(note_id: str, token: str) -> dict:
    return api_get(f"/note/i/{note_id}", token)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    token = get_token()
    started = time.time()
    print("fetching index ...", file=sys.stderr)
    index = fetch_index(token, limit=args.limit)
    print(f"index: {len(index)} notes", file=sys.stderr)

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    cached = dict(conn.execute("SELECT id, version FROM notes").fetchall())

    to_fetch = [
        entry["id"] for entry in index
        if args.full or cached.get(entry["id"]) != entry.get("v")
    ]
    print(f"to fetch: {len(to_fetch)} (unchanged: {len(index) - len(to_fetch)})", file=sys.stderr)

    results, failures = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fetch_note, nid, token): nid for nid in to_fetch}
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            nid = futures[fut]
            done += 1
            try:
                results.append((nid, fut.result()))
            except Exception as err:  # noqa: BLE001
                failures.append((nid, str(err)))
            if done % 200 == 0:
                print(f"  fetched {done}/{len(to_fetch)}", file=sys.stderr)

    version_by_id = {entry["id"]: entry.get("v") for entry in index}
    now = datetime.now(timezone.utc).isoformat()
    added, updated = [], []
    for nid, note in results:
        existed = nid in cached
        conn.execute(
            """INSERT INTO notes (id, version, content, creation_ts, modification_ts,
                                  deleted, tags, system_tags, synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 version=excluded.version, content=excluded.content,
                 creation_ts=excluded.creation_ts, modification_ts=excluded.modification_ts,
                 deleted=excluded.deleted, tags=excluded.tags,
                 system_tags=excluded.system_tags, synced_at=excluded.synced_at""",
            (
                nid,
                version_by_id.get(nid),
                note.get("content", ""),
                note.get("creationDate"),
                note.get("modificationDate"),
                1 if note.get("deleted") else 0,
                json.dumps(note.get("tags", [])),
                json.dumps(note.get("systemTags", [])),
                now,
            ),
        )
        title = (note.get("content", "").splitlines() or [""])[0][:100]
        (updated if existed else added).append({"id": nid, "title": title})

    index_ids = {entry["id"] for entry in index}
    deleted = [nid for nid in cached if nid not in index_ids]

    conn.commit()
    totals = conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN deleted=0 THEN 1 ELSE 0 END) FROM notes"
    ).fetchone()
    conn.close()

    PENDING_PATH.write_text(
        json.dumps(
            {"synced_at": now, "added": added, "updated": updated, "deleted": deleted},
            indent=1,
        ),
        encoding="utf-8",
    )
    print(
        f"done in {time.time() - started:.1f}s | db notes: {totals[0]} (live {totals[1]}) | "
        f"added {len(added)}, updated {len(updated)}, deleted {len(deleted)}, failures {len(failures)}"
    )
    for nid, err in failures[:5]:
        print(f"  FAILED {nid}: {err}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
