"""Find completion markers in notes and match them to tasks.

Your convention: DONE means finished, with optional caveats beside it such as
"DONE, NOT REVIEWED". WORKING means started, not finished.

This script is advisory only. It writes marker-report.md for review and never
changes tasks.json, because pattern matching cannot tell "when i am done" from
"DONE". tasks.json stays the primary record of state.

Usage: python scan_markers.py
"""
import json
import pathlib
import re
import sqlite3

HERE = pathlib.Path(__file__).parent
TASKS = HERE / "tasks.json"
NOTES_DB = HERE / "notes.db"
REPORT = HERE / "marker-report.md"
STOP = set("the and for with that this into from all any are was were will add new get got use using my me i".split())
DONE_RE = re.compile(r"\b(DONE|FINISHED|CHECKED)\b", re.I)
WORKING_RE = re.compile(r"\b(WORKING|WIP|IN PROGRESS|STARTED)\b", re.I)


def words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3 and w not in STOP}


def main() -> int:
    tasks = json.loads(TASKS.read_text(encoding="utf-8"))["tasks"]
    live = [t for t in tasks if t.get("status") in ("open", "started")]
    conn = sqlite3.connect(NOTES_DB)

    hits, done_rows, started_rows = [], [], []
    for t in live:
        row = conn.execute("SELECT content FROM notes WHERE id=?", (t["source_note"],)).fetchone()
        if not row:
            continue
        title_words = words(t["title"])
        if not title_words:
            continue
        for line in row[0].splitlines():
            is_done = DONE_RE.search(line)
            is_working = WORKING_RE.search(line)
            if not (is_done or is_working):
                continue
            hit = len(title_words & words(line)) / len(title_words)
            if hit < 0.6:
                continue
            caveat = ""
            if is_done:
                tail = line.split("DONE", 1)[-1] if "DONE" in line.upper() else ""
                caveat = tail.strip(" ,.;:-")[:80]
            entry = (t, line.strip()[:160], round(hit, 2), caveat)
            (done_rows if is_done else started_rows).append(entry)
            hits.append(entry)

    lines = [f"# Completion markers in notes ({len(done_rows)} done, {len(started_rows)} started)\n"]
    for title, items in (("Marked done", done_rows), ("Marked working", started_rows)):
        lines.append(f"\n## {title}\n")
        for t, line, hit, caveat in sorted(items, key=lambda e: -e[2]):
            tail = f" | caveat: {caveat}" if caveat else ""
            lines.append(f"- `{t['id']}` [{hit}] {t['title'][:70]}\n  note says: {line}{tail}")

    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"marker-report.md written: {len(done_rows)} done, {len(started_rows)} started")
    print("advisory only: review the report, states in tasks.json are unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())