"""Merge tasks_new.json (written by the extraction agent) into tasks.json.

A newer mention of an open task refreshes its evidence and resets its check-in
clock. Other duplicates are dropped. tasks.json stays the primary record of
completion state. Writes nothing to Simplenote.

Usage: python merge_pending.py
"""
import json
import os
import pathlib
import re
from datetime import date

import categories
import checkins

HERE = pathlib.Path(__file__).parent
TASKS = HERE / "tasks.json"
NEW = HERE / "tasks_new.json"
FIELDS = {"title", "quote", "source_note", "source_date", "tier", "impact", "status", "caveat", "why"}


def norm(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


STOP = set("the and for with that this into from all any are was were will add new get got use using my me i to do".split())


def words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3 and w not in STOP}


def match_dupe(title: str, existing: list):
    """Return the task this title repeats, or None."""
    key = norm(title)
    mine = words(title)
    if not mine:
        return None
    exact = [t for t in existing if norm(t["title"]) == key]
    for t in exact:
        if t.get("status") in ("open", "started"):
            return t
    if exact:
        return exact[0]
    near = []
    for t in existing:
        theirs = words(t["title"])
        if not theirs:
            continue
        shared = len(mine & theirs) / len(mine | theirs)
        if shared >= 0.6:
            near.append(t)
    for t in near:
        if t.get("status") in ("open", "started"):
            return t
    return near[0] if near else None


def revive_task(task: dict, incoming: dict, source_title: str, today: str) -> bool:
    """Refresh an open task when a newer note mentions it again.

    A newer mention lifts a check-in demotion, updates the source evidence and
    moves the check-in base date. A repeat of the same note does nothing.
    """
    if task.get("status") not in ("open", "started"):
        return False
    seen = incoming.get("source_date") or ""
    if not seen or seen <= (task.get("last_seen") or task.get("first_seen") or ""):
        return False
    task["last_seen"] = seen
    task["mention_count"] = int(task.get("mention_count") or 1) + 1
    task["source_note"] = incoming.get("source_note") or task.get("source_note")
    task["source_title"] = source_title or task.get("source_title") or ""
    task["quote"] = incoming.get("quote") or task.get("quote") or ""
    if incoming.get("deadline"):
        task["deadline"] = incoming["deadline"]
    if incoming.get("soft_deadline"):
        task["soft_deadline"] = incoming["soft_deadline"]
    if task.get("checkin_state") in ("queued", "active", "demoted") or task.get("demoted_at"):
        checkins.restore_tier(task)
        checkins.clear_checkin(task)
    if incoming.get("status") == "done":
        task["status"] = "done"
        task["completed"] = incoming.get("source_date") or today
    elif incoming.get("status") == "started" and task.get("status") == "open":
        task["status"] = "started"
    return True


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
    added = dupes = revived = 0
    n = sum(1 for t in tasks if t["id"].startswith("b"))
    for t in fresh:
        if not FIELDS.issubset(t):
            missing = ", ".join(sorted(FIELDS - set(t)))
            print(f"skipping malformed entry ({missing}): {str(t)[:60]}")
            continue
        key = norm(t["title"])
        if not key:
            dupes += 1
            continue
        repeated = match_dupe(t["title"], tasks)
        if repeated:
            if revive_task(
                repeated, t, conn_titles.get(t["source_note"], ""),
                date.today().isoformat(),
            ):
                revived += 1
            else:
                dupes += 1
            continue
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
            "soft_deadline": t.get("soft_deadline") or None,
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
    print(
        f"merged {added} tasks, {revived} re-mentioned, "
        f"{dupes} duplicates dropped, total {len(tasks)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())