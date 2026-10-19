"""Dump the notes that changed in the last sync to pending_notes.json.

Usage: python dump_notes.py [--limit 40]
"""
import argparse
import json
import pathlib
import sqlite3

HERE = pathlib.Path(__file__).parent
CAP = 12000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()

    changes = json.loads((HERE / "pending_changes.json").read_text(encoding="utf-8"))
    ids = []
    for key in ("added", "updated"):
        for entry in changes.get(key) or []:
            nid = entry["id"] if isinstance(entry, dict) else entry
            ids.append(nid)
    ids = ids[: args.limit]

    conn = sqlite3.connect(HERE / "notes.db")
    notes = []
    for nid in ids:
        row = conn.execute(
            "SELECT modification_ts, content FROM notes WHERE id=? AND deleted=0", (nid,)
        ).fetchone()
        if not row:
            continue
        notes.append(
            {
                "id": nid,
                "date": __import__("datetime").datetime.fromtimestamp(row[0]).strftime("%Y-%m-%d"),
                "truncated": len(row[1]) > CAP,
                "content": row[1][:CAP],
            }
        )

    (HERE / "pending_notes.json").write_text(
        json.dumps(notes, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"pending_notes.json written: {len(notes)} of {len(ids)} changed notes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())