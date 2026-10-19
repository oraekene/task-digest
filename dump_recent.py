"""Test helper: put the N most recently changed notes into pending_notes.json.

Usage: python dump_recent.py 4
"""
import json
import pathlib
import sqlite3
import sys
import datetime

HERE = pathlib.Path(__file__).parent
n = int(sys.argv[1]) if len(sys.argv) > 1 else 4

conn = sqlite3.connect(HERE / "notes.db")
rows = conn.execute(
    "SELECT id, modification_ts, content FROM notes WHERE deleted=0 "
    "ORDER BY modification_ts DESC LIMIT ?",
    (n,),
).fetchall()
notes = [
    {
        "id": nid,
        "date": datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d"),
        "truncated": len(c) > 12000,
        "content": c[:12000],
    }
    for nid, ts, c in rows
]
(HERE / "pending_notes.json").write_text(
    json.dumps(notes, ensure_ascii=False, indent=1), encoding="utf-8"
)
print(f"pending_notes.json: {len(notes)} notes")
for note in notes:
    print(" ", note["date"], note["id"], note["content"][:70].replace("\n", " "))