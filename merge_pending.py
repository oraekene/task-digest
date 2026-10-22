"""Merge tasks_new.json (written by the extraction agent) into tasks.json.

Same rules as merge_out.py: drop exact duplicate titles, keep tasks.json as the
primary record of completion state. Writes nothing to Simplenote.

Usage: python merge_pending.py
"""
import json
import os
import pathlib
import re

import categories

HERE = pathlib.Path(__file__).parent
TASKS = HERE / "tasks.json"
NEW = HERE / "tasks_new.json"
FIELDS = {"title", "quote", "source_note", "source_date", "tier", "impact", "status", "caveat", "why"}


def norm(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


STOP = set("the and for with that this into from all any are was were will add new get got use using my me i to do".split())


def words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3 and w not in STOP}


def is_dupe(title: str, existing: list) -> bool:
    """Same task in other words. Word overlap catches what exact match misses."""
    key = norm(title)
    mine = words(title)
    if not mine:
        return False
    for t in existing:
        if norm(t["title"]) == key:
            return True
    for t in existing:
        theirs = words(t["title"])
        if not theirs:
            continue
        shared = len(mine & theirs) / len(mine | theirs)
        if shared >= 0.6:
            return True
    return False


def main() -> int:
    if not NEW.exists():
        print("no tasks_new.json, nothing to merge")
        return 0

    data = json.loads(TASKS.read_text(encoding="utf-8"))
    tasks = data["tasks"]
    conn_titles = {}
    import sqlite3

    conn = sqlite3.connect(HERE / "notes.db")
    for nid, first in conn.execute("SELECT id, content FROM notes"):
        conn_titles[nid] = (first.strip().splitlines()[0][:70] if first.strip() else "")
    conn.close()

    seen = {norm(t["title"]) for t in tasks}
    settings = json.loads((HERE / "settings.json").read_text(encoding="utf-8")) if (HERE / "settings.json").exists() else {}
    use_deadlines = bool(settings.get("use_deadlines", False))
    fresh = json.loads(NEW.read_text(encoding="utf-8"))
    jev = {}
    need_cat = [t for t in fresh if FIELDS.issubset(t) and (t.get("category") or "none") == "none"]
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not key and (HERE / ".jev_key").exists():
        key = (HERE / ".jev_key").read_text(encoding="utf-8").strip()
    if need_cat and key:
        try:
            import jev_categorize

            for i, t in enumerate(need_cat):
                t["id"] = f"tmp{i:03d}"
            jev = jev_categorize.classify(need_cat, key, jev_categorize.load_cache())
        except Exception as e:  # noqa: BLE001 - Jev must never block the merge
            print(f"jev skipped: {type(e).__name__}: {e}")
    added = dupes = 0
    n = sum(1 for t in tasks if t["id"].startswith("b"))
    for t in fresh:
        if not FIELDS.issubset(t):
            missing = ", ".join(sorted(FIELDS - set(t)))
            print(f"skipping malformed entry ({missing}): {str(t)[:60]}")
            continue
        key = norm(t["title"])
        if not key or key in seen or is_dupe(t["title"], tasks):
            dupes += 1
            continue
        seen.add(key)
        status = t.get("status") or "open"
        caveat = t.get("caveat") or ""
        category = jev.get(t.get("id"), {}).get("category") or t.get("category") or "none"
        task = {
            "id": f"b{n + 1:04d}",
            "title": t["title"],
            "source_note": t["source_note"],
            "source_title": conn_titles.get(t["source_note"], ""),
            "quote": t["quote"],
            "status": status,
            "tier": int(t["tier"]),
            "impact": float(t["impact"]),
            "deadline": t.get("deadline") or None,
            "first_seen": t["source_date"],
            "completed": t["source_date"] if status == "done" else None,
            "category": category,
            "why": (t.get("why", "") + (f" | caveat: {caveat}" if caveat else "")).strip(" |"),
        }
        effort = str(t.get("effort") or "").strip().upper()
        if effort in ("S", "M", "L"):
            task["effort"] = effort
        categories.apply(task, use_deadlines=use_deadlines)
        tasks.append(task)
        n += 1
        added += 1

    TASKS.write_text(json.dumps({"version": 1, "tasks": tasks}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"merged {added} tasks, {dupes} duplicates dropped, total {len(tasks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())