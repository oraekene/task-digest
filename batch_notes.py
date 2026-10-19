"""Split unmined notes (modified since 2026-03-01) into batch files for extraction.

Writes batches/batch_NN.json, each note capped at CAP chars, batches aim at
TARGET bytes. Already-examined notes (the 30 most recent, plus notes with open
checkboxes in the window) are excluded.
"""
import datetime
import json
import pathlib
import re
import sqlite3

HERE = pathlib.Path(__file__).parent
OUT = HERE / "batches"
OUT.mkdir(exist_ok=True)
CAP = 12000
TARGET = 200_000
CUTOFF = datetime.datetime(2026, 3, 1).timestamp()

conn = sqlite3.connect(HERE / "notes.db")
excluded = {
    r[0]
    for r in conn.execute(
        "SELECT id FROM notes WHERE deleted=0 ORDER BY modification_ts DESC LIMIT 30"
    )
}
for nid, content, ts in conn.execute(
    "SELECT id, content, modification_ts FROM notes WHERE deleted=0"
):
    if ts >= CUTOFF and re.search(r"^\s*[-*] \[ \]", content, re.M):
        excluded.add(nid)

rows = [
    (nid, ts, content)
    for nid, ts, content in conn.execute(
        "SELECT id, modification_ts, content FROM notes WHERE deleted=0 ORDER BY modification_ts ASC"
    )
    if ts >= CUTOFF and nid not in excluded
]

for old in OUT.glob("batch_*.json"):
    old.unlink()

batches = []
current = []
size = 0
truncated_total = 0
for nid, ts, content in rows:
    truncated = len(content) > CAP
    truncated_total += truncated
    entry = {
        "id": nid,
        "date": datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d"),
        "truncated": truncated,
        "content": content[:CAP],
    }
    blob = json.dumps(entry)
    if current and size + len(blob) > TARGET:
        batches.append(current)
        current = []
        size = 0
    current.append(entry)
    size += len(blob)
if current:
    batches.append(current)

for i, batch in enumerate(batches, 1):
    (OUT / f"batch_{i:02d}.json").write_text(
        json.dumps(batch, ensure_ascii=False, indent=1), encoding="utf-8"
    )

lines = ["# Extraction batches\n"]
for i, batch in enumerate(batches, 1):
    dates = (batch[0]["date"], batch[-1]["date"])
    lines.append(f"- batch_{i:02d}.json: {len(batch)} notes, {dates[0]} to {dates[1]}")
(OUT / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

print(f"{len(batches)} batches, {len(rows)} notes, {truncated_total} truncated at {CAP}")
