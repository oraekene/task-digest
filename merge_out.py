"""Merge batches/out_*.json into tasks.json.

Adds ids b001.., pulls the note title from notes.db, drops exact duplicate
titles, and reports near duplicates for manual review.

Usage: python merge_out.py [--dry]
"""
import difflib
import json
import pathlib
import re
import sqlite3
import sys

HERE = pathlib.Path(__file__).parent
TASKS = HERE / "tasks.json"
NOTES_DB = HERE / "notes.db"
BATCHES = HERE / "batches"
NEAR = 0.88


def norm(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def main() -> int:
    conn = sqlite3.connect(NOTES_DB)
    titles = {
        nid: (first.strip().splitlines()[0][:70] if first.strip() else "")
        for nid, first in conn.execute("SELECT id, content FROM notes")
    }

    tasks = json.loads(TASKS.read_text(encoding="utf-8"))["tasks"]
    seen = {norm(t["title"]) for t in tasks}
    added, exact_dupes, near_dupes = 0, 0, []

    for path in sorted(BATCHES.glob("out_*.json")):
        for t in json.loads(path.read_text(encoding="utf-8")):
            key = norm(t["title"])
            if not key or key in seen:
                exact_dupes += 1
                continue
            seen.add(key)
            note = t["source_note"]
            tasks.append(
                {
                    "id": f"b{added + 1:04d}",
                    "title": t["title"],
                    "source_note": note,
                    "source_title": titles.get(note, ""),
                    "quote": t["quote"],
                    "status": t.get("status", "open"),
                    "tier": int(t["tier"]),
                    "impact": float(t["impact"]),
                    "deadline": t.get("deadline") or None,
                    "soft_deadline": t.get("soft_deadline") or None,
                    "first_seen": t["source_date"],
                    "completed": t["source_date"] if t.get("status") == "done" else None,
                    "why": (t.get("why", "") + (f" | caveat: {t['caveat']}" if t.get("caveat") else "")).strip(" |"),
                }
            )
            added += 1

    keys = list(seen)
    words = {k: {w for w in k.split() if len(w) > 3} for k in keys}
    index = {}
    for i, k in enumerate(keys):
        for w in words[k]:
            index.setdefault(w, []).append(i)
    pairs = set()
    for members in index.values():
        if len(members) > 60:
            continue
        for i in members:
            for j in members:
                if i < j:
                    pairs.add((i, j))
    for i, j in pairs:
        a, b = keys[i], keys[j]
        if difflib.SequenceMatcher(None, a, b).ratio() > NEAR:
            near_dupes.append((a, b))

    print(f"added {added}, exact dupes dropped {exact_dupes}")
    print(f"near dupes {len(near_dupes)} (review these):")
    for a, b in near_dupes[:60]:
        print(f"  {a[:70]}\n  {b[:70]}\n")

    if "--dry" in sys.argv:
        return 0
    TASKS.write_text(
        json.dumps({"version": 1, "tasks": tasks}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"tasks.json now has {len(tasks)} tasks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())