"""Split all notes into batches for the state audit.

Each note goes with the tasks already extracted from it, so the auditor can
compare the note text against the current state of each task.

Usage: python audit_dump.py [batch_size]
Writes audit_in_01.json ...
"""
import json
import pathlib
import sqlite3
import sys
from datetime import datetime

HERE = pathlib.Path(__file__).parent
BATCH = int(sys.argv[1]) if len(sys.argv) > 1 else 80


def main() -> int:
    tasks = json.loads((HERE / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    by_note = {}
    for t in tasks:
        by_note.setdefault(t["source_note"], []).append(
            {
                "id": t["id"],
                "title": t["title"],
                "status": t.get("status"),
                "tier": t.get("tier"),
                "category": t.get("category") or "none",
            }
        )

    conn = sqlite3.connect(HERE / "notes.db")
    notes = []
    for nid, ts, content in conn.execute(
        "SELECT id, modification_ts, content FROM notes WHERE deleted=0 ORDER BY modification_ts"
    ):
        notes.append(
            {
                "id": nid,
                "date": datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "?",
                "truncated": len(content) > 12000,
                "tasks": by_note.get(nid, []),
                "content": content[:12000],
            }
        )

    for old in HERE.glob("audit_in_*.json"):
        old.unlink()
    for i in range(0, len(notes), BATCH):
        path = HERE / f"audit_in_{i // BATCH + 1:02d}.json"
        path.write_text(
            json.dumps(notes[i : i + BATCH], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    with_tasks = sum(1 for n in notes if n["tasks"])
    print(f"{len(notes)} notes ({with_tasks} with tasks) in {-(-len(notes) // BATCH)} batches of {BATCH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())