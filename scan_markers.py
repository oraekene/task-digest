"""Find completion markers in notes and match them to tasks.

Your convention: DONE means finished, with optional caveats beside it such as
"DONE, NOT REVIEWED". WORKING means started, not finished.

This script is advisory only. It writes marker-report.md and marker-report.json
for review and never changes tasks.json, because pattern matching cannot tell
"when i am done" from "DONE". tasks.json stays the primary record of state.

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
REPORT_JSON = HERE / "marker-report.json"
STOP = set("the and for with that this into from all any are was were will add new get got use using my me i".split())
END = r"(?=\s*$|\s*[,(;:\-\u2014])"
PARTIAL_RE = re.compile(r"\b(HALF[- ]DONE|SEMI[- ]DONE|PARTIAL(?:LY)? DONE)\b" + END, re.I)
DONE_RE = re.compile(r"\b(DONE|FINISHED|CHECKED)\b" + END, re.I)
WORKING_RE = re.compile(r"\b(WORKING|WIP|IN PROGRESS|STARTED)\b" + END, re.I)
NEGATED_RE = re.compile(r"\bNOT\s+(DONE|FINISHED|CHECKED)\b", re.I)


def words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3 and w not in STOP}


def markers_in(line: str):
    """Yield (status, start, end, marker). Marker must sit at the end of a clause."""
    found = []
    for regex, status in ((PARTIAL_RE, "started"), (DONE_RE, "done"), (WORKING_RE, "started")):
        for match in regex.finditer(line):
            found.append((status, match.start(), match.end(), match.group(0).strip()))
    return found


def main() -> int:
    tasks = json.loads(TASKS.read_text(encoding="utf-8"))["tasks"]
    live = [t for t in tasks if t.get("status") in ("open", "started")]
    conn = sqlite3.connect(NOTES_DB)
    entries = []
    for t in live:
        row = conn.execute("SELECT content FROM notes WHERE id=?", (t["source_note"],)).fetchone()
        if not row:
            continue
        title_words = words(t["title"])
        if not title_words:
            continue
        for line in row[0].splitlines():
            if NEGATED_RE.search(line):
                continue
            best = None
            for status, start, end, marker in markers_in(line):
                window = line[max(0, start - 120) : end + 120]
                hit = len(title_words & words(window)) / len(title_words)
                if hit >= 0.6 and (best is None or hit > best[0]):
                    best = (hit, status, marker, line[end:].strip(" ,.;:-\u2014")[:100], line)
            if best is None:
                continue
            hit, status, marker, caveat, line = best
            entries.append(
                {
                    "task_id": t["id"],
                    "title": t["title"],
                    "current": t.get("status"),
                    "suggested": status,
                    "marker": marker,
                    "caveat": caveat,
                    "line": line.strip()[:200],
                    "hit": round(hit, 2),
                }
            )

    entries.sort(key=lambda e: (e["suggested"] != "done", -e["hit"], e["task_id"]))
    done_rows = [e for e in entries if e["suggested"] == "done"]
    started_rows = [e for e in entries if e["suggested"] == "started"]

    lines = [f"# Completion markers in notes ({len(done_rows)} done, {len(started_rows)} started)\n"]
    for title, items in (("Marked done", done_rows), ("Marked working", started_rows)):
        lines.append(f"\n## {title}\n")
        for e in items:
            tail = f" | caveat: {e['caveat']}" if e["caveat"] else ""
            lines.append(
                f"- `{e['task_id']}` [{e['hit']}] ({e['current']} -> {e['suggested']}) {e['title'][:70]}\n"
                f"  note says: {e['line']}{tail}"
            )
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    REPORT_JSON.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"marker-report.md written: {len(done_rows)} done, {len(started_rows)} started")
    print("advisory only: review the report, states in tasks.json are unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())