"""Task tracker for the Simplenote digest system.

tasks.json is the single reference list. This script never edits it; the agent
does. The script scores, ranks, and renders.

Commands:
    python tracker.py candidates [--n 30]   -> write candidates.md for extraction
    python tracker.py digest                -> write digest-YYYY-MM-DD.md + list.md
    python tracker.py rank                  -> print ranked open tasks to stdout
    python tracker.py check                 -> report bad or cyclic prerequisites
    python tracker.py shuffle [--n N]       -> randomize today's normal focus rows
    python tracker.py pick ID [ID ...]      -> choose today's focus by id
    python tracker.py auto                  -> focus back to the score order
    python tracker.py config [KEY] [VALUE]  -> show or change a setting

Scoring (from the Prioritization Engine spec, specs 01/03):
    tier_weight = 100 / 2**(tier-1)
    urgency     = 1/(days_until_deadline+1) if deadline or soft_deadline else min(age_days*0.02, 0.5)
    score       = tier_weight * (1 + urgency + impact)
    sort: score desc, then unblocks desc, effort S/M/L, first_seen ascending
"""
import argparse
import json
import pathlib
import random
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta

import categories

HERE = pathlib.Path(__file__).parent
TASKS = HERE / "tasks.json"
NOTES_DB = HERE / "notes.db"
SETTINGS = HERE / "settings.json"
TODAY_FILE = HERE / "today.json"
TODAY = date.today()

DEFAULTS = {
    "use_deadlines": False,
    "auto_extract": True,
    "notify_top": 3,
    "read_only": True,
    "focus_size": 7,
    "value_slots": 1,
    "band_late_days": 14,
    "band_ahead_days": 7,
    "refill_when_done": False,
    "routine_slots": 3,
    "lapse_limit_days": 14,
    "keep_days": 14,
    "lapse_alert_top": 3,
    "lapse_alert_days": 3,
    "checkin_days": 45,
    "checkin_active_days": 90,
    "checkin_answer_days": 14,
}
BOUNDS = {
    "notify_top": (1, 20),
    "focus_size": (1, 10),
    "value_slots": (0, 10),
    "band_late_days": (7, 14),
    "band_ahead_days": (7, 14),
    "routine_slots": (0, 5),
    "lapse_limit_days": (1, 365),
    "keep_days": (1, 365),
    "lapse_alert_top": (1, 10),
    "lapse_alert_days": (1, 30),
    "checkin_days": (1, 365),
    "checkin_active_days": (1, 365),
    "checkin_answer_days": (1, 90),
}


def clamp_settings(data: dict) -> dict:
    for key, (low, high) in BOUNDS.items():
        try:
            value = int(data.get(key, DEFAULTS[key]))
        except (TypeError, ValueError):
            value = DEFAULTS[key]
        data[key] = max(low, min(value, high))
    data["value_slots"] = min(int(data["value_slots"]), int(data["focus_size"]))
    return data


def load_settings() -> dict:
    data = dict(DEFAULTS)
    if SETTINGS.exists():
        data.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
    return clamp_settings(data)


def save_settings(data: dict) -> None:
    SETTINGS.write_text(json.dumps(clamp_settings(data), indent=2) + "\n", encoding="utf-8")


def cmd_config(args) -> int:
    data = load_settings()
    if args.key:
        current = data.get(args.key)
        if args.value is None:
            print(f"{args.key} = {current}")
            return 0
        if isinstance(current, bool):
            value = args.value.lower() in ("1", "true", "yes", "on")
        elif isinstance(current, int):
            try:
                value = int(args.value)
            except ValueError:
                print(f"{args.key} needs a number")
                return 1
        else:
            value = args.value
        data[args.key] = value
        save_settings(data)
        print(f"{args.key} = {load_settings()[args.key]}")
        return 0
    print("settings.json")
    for key, value in data.items():
        print(f"  {key} = {value}")
    print("\nchange one with: python tracker.py config use_deadlines true")
    return 0


def load_tasks() -> dict:
    if TASKS.exists():
        return json.loads(TASKS.read_text(encoding="utf-8"))
    return {"version": 1, "tasks": []}


def parse_date(value):
    if not value:
        return None
    return date.fromisoformat(value[:10])


def score(task: dict, settings: dict | None = None) -> float:
    settings = settings if settings is not None else load_settings()
    tier = int(task.get("tier") or 2)
    tier_weight = 100 / 2 ** (tier - 1)
    deadline = None
    if settings["use_deadlines"]:
        for key in ("deadline", "soft_deadline"):
            try:
                deadline = parse_date(task.get(key))
            except ValueError:
                deadline = None
            if deadline:
                break
    if deadline:
        urgency = 1 / (max((deadline - TODAY).days, 0) + 1)
    else:
        first = parse_date(task.get("first_seen")) or TODAY
        urgency = min((TODAY - first).days * 0.02, 0.5)
    impact = float(task.get("impact") or 0)
    return tier_weight * (1 + urgency + impact)


def is_lapsed(task: dict, settings: dict | None = None) -> bool:
    settings = settings if settings is not None else load_settings()
    if task.get("status") not in ("open", "started"):
        return False
    try:
        deadline = parse_date(task.get("deadline"))
        keep = parse_date(task.get("keep_until"))
    except ValueError:
        return False
    if not deadline:
        return False
    if keep and keep >= TODAY:
        return False
    return (TODAY - deadline).days > int(settings["lapse_limit_days"])


def lapsed_tasks(tasks: dict, settings: dict | None = None) -> list[dict]:
    settings = settings if settings is not None else load_settings()
    rows = []
    for task in tasks["tasks"]:
        if not is_lapsed(task, settings):
            continue
        row = dict(task)
        deadline = parse_date(task.get("deadline"))
        row["lapse_days"] = (TODAY - deadline).days if deadline else 0
        row["lapse_kind"] = categories.lapse_kind(task)
        rows.append(row)

    def order(task):
        kind = task["lapse_kind"]
        if kind == "compounding":
            return (0, -task["lapse_days"], -score(task, settings), task["id"])
        if kind == "expiring":
            return (1, task["lapse_days"], -score(task, settings), task["id"])
        return (2, -score(task, settings), -task["lapse_days"], task["id"])

    rows.sort(key=order)
    return rows


def note_edit_dates() -> dict:
    """Map note id to the day the note was last edited, when notes.db exists."""
    if not NOTES_DB.exists():
        return {}
    rows = []
    try:
        conn = sqlite3.connect(NOTES_DB)
        try:
            rows = conn.execute(
                "SELECT id, modification_ts FROM notes WHERE deleted=0"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    out = {}
    for note_id, stamp in rows:
        if not stamp:
            continue
        try:
            out[str(note_id)] = date.fromtimestamp(float(stamp))
        except (OverflowError, OSError, ValueError):
            pass
    return out


def checkin_base(task: dict, notes: dict) -> date:
    first = parse_date(task.get("first_seen")) or TODAY
    edited = notes.get(str(task.get("source_note") or ""))
    seen = parse_date(task.get("last_seen"))
    return max(first, edited or first, seen or first)


def checkin_due(
    task: dict, settings: dict | None = None, notes: dict | None = None
) -> date | None:
    """Return the day this task is due for a check-in, or None."""
    settings = settings if settings is not None else load_settings()
    notes = {} if notes is None else notes
    if task.get("status") not in ("open", "started"):
        return None
    if task.get("status") == "started":
        return None
    if task.get("deadline") or task.get("soft_deadline"):
        return None
    state = task.get("checkin_state")
    if state == "demoted":
        return None
    days = int(settings["checkin_days"])
    if state == "active":
        due = parse_date(task.get("checkin_until"))
        if not due:
            due = checkin_base(task, notes) + timedelta(days=days)
        return due if due <= TODAY else None
    if state == "queued":
        due = parse_date(task.get("checkin_due"))
        if not due:
            due = checkin_base(task, notes) + timedelta(days=days)
        return due if due <= TODAY else None
    if "demoted: stale" in (task.get("why") or "") and not task.get("demoted_at"):
        return None
    due = checkin_base(task, notes) + timedelta(days=days)
    return due if due <= TODAY else None


def checkin_queue(
    tasks: dict, settings: dict | None = None, notes: dict | None = None,
    include_unjudged: bool = False,
) -> list[dict]:
    """Check-ins waiting for your answer, oldest first, with no cap.

    A task that is due but has not been judged yet (no Jev answer, so nobody
    knows whether it is a project) is not a question for you. It is left out
    here, and checkins.py asks for those with include_unjudged=True, so a day
    without Jev cannot flood the list.
    """
    settings = settings if settings is not None else load_settings()
    if notes is None:
        notes = note_edit_dates()
    rows = []
    for task in tasks["tasks"]:
        due = checkin_due(task, settings, notes)
        if due is None:
            continue
        if not include_unjudged and task.get("checkin_state") != "queued":
            continue
        row = dict(task)
        first = parse_date(task.get("first_seen")) or TODAY
        row["checkin_due_date"] = due.isoformat()
        row["checkin_age_days"] = (TODAY - first).days
        row["checkin_wait_days"] = (TODAY - due).days
        rows.append(row)
    rows.sort(
        key=lambda t: (
            t["checkin_due_date"],
            int(t.get("tier") or 2),
            t.get("first_seen") or "",
            t["id"],
        )
    )
    return rows


def demoted_checkins(tasks: dict) -> list[dict]:
    """New demotions from the check-in flow, newest first. Old stale flags stay out."""
    rows = [
        dict(t) for t in tasks["tasks"]
        if t.get("checkin_state") == "demoted" and t.get("demoted_at")
    ]
    rows.sort(key=lambda t: (t.get("demoted_at") or "", t["id"]), reverse=True)
    return rows


def band_bucket(task: dict, settings: dict | None = None) -> int | None:
    settings = settings if settings is not None else load_settings()
    if not settings["use_deadlines"]:
        return None
    try:
        deadline = parse_date(task.get("deadline"))
    except ValueError:
        return None
    if not deadline:
        return None
    days = (deadline - TODAY).days
    if days < -int(settings["band_late_days"]):
        return None
    if days > int(settings["band_ahead_days"]):
        return None
    if days < 0:
        return 0
    if days <= 3:
        return 1
    if days <= 7:
        return 2
    return 3


def band_key(task: dict, settings: dict | None = None):
    settings = settings if settings is not None else load_settings()
    deadline = parse_date(task.get("deadline")) or TODAY
    first = parse_date(task.get("first_seen")) or TODAY
    return (band_bucket(task, settings), -score(task, settings), deadline, first, task["id"])


def due_label(task: dict) -> str:
    try:
        deadline = parse_date(task.get("deadline"))
    except ValueError:
        return ""
    if not deadline:
        return ""
    days = (deadline - TODAY).days
    if days < 0:
        return f" ({abs(days)}d late)"
    if days == 0:
        return " (due today)"
    return f" (in {days}d)"


def is_value(task: dict, settings: dict | None = None) -> bool:
    settings = settings if settings is not None else load_settings()
    return (
        band_bucket(task, settings) is None
        and int(task.get("tier") or 2) >= 2
        and float(task.get("impact") or 0) >= 0.3
    )


EFFORT_RANK = {"S": 0, "M": 1, "L": 2}


def effort_rank(task: dict) -> int:
    return EFFORT_RANK.get(str(task.get("effort") or "").upper(), 1)


def prereq_ids(task: dict) -> list[str]:
    raw = task.get("prerequisites") or []
    if isinstance(raw, str):
        return [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(i) for i in raw]


def open_prerequisites(task: dict, tasks: dict) -> list[dict]:
    by_id = {t["id"]: t for t in tasks["tasks"]}
    return [
        by_id[i] for i in prereq_ids(task)
        if i in by_id and by_id[i].get("status") not in ("done", "dropped")
    ]


def unblocks_counts(tasks: dict, settings: dict | None = None) -> dict:
    settings = settings if settings is not None else load_settings()
    counts = {}
    for task in tasks["tasks"]:
        if (
            task.get("status") not in ("open", "started")
            or task.get("routine_id")
            or is_lapsed(task, settings)
        ):
            continue
        for task_id in set(prereq_ids(task)):
            if task_id != task["id"]:
                counts[task_id] = counts.get(task_id, 0) + 1
    return counts


def completion_warning(task: dict, tasks: dict) -> str:
    pending = open_prerequisites(task, tasks)
    if not pending:
        return ""
    names = ", ".join((t.get("title") or t["id"])[:40] for t in pending[:3])
    if len(pending) > 3:
        names += f" and {len(pending) - 3} more"
    return f"This task still needs: {names}"


def dependency_issues(tasks: dict) -> list[str]:
    by_id = {t["id"]: t for t in tasks["tasks"]}
    issues = []
    graph = {task_id: [] for task_id in by_id}
    indegree = {task_id: 0 for task_id in by_id}
    for task in tasks["tasks"]:
        raw = task.get("prerequisites")
        if not raw:
            continue
        if isinstance(raw, str):
            issues.append(f"{task['id']} prerequisites must be a list")
            raw = [raw]
        if not isinstance(raw, (list, tuple)):
            issues.append(f"{task['id']} prerequisites must be a list")
            continue
        for pid in raw:
            pid = str(pid)
            if pid == task["id"]:
                issues.append(f"{task['id']} depends on itself")
            elif pid not in by_id:
                issues.append(f"{task['id']} has unknown prerequisite {pid}")
            else:
                graph[pid].append(task["id"])
                indegree[task["id"]] += 1

    queue = [task_id for task_id, count in indegree.items() if count == 0]
    seen = 0
    while queue:
        task_id = queue.pop()
        seen += 1
        for nxt in graph[task_id]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    if seen < len(by_id):
        left = sorted(task_id for task_id, count in indegree.items() if count > 0)
        issues.append("cycle among: " + ", ".join(left))
    return sorted(issues)


def ranked_open(tasks: dict, settings: dict | None = None) -> list[dict]:
    settings = settings if settings is not None else load_settings()
    open_tasks = [
        t for t in tasks["tasks"]
        if t.get("status") in ("open", "started")
        and not t.get("routine_id")
        and not is_lapsed(t, settings)
    ]
    blocks = unblocks_counts(tasks, settings)
    open_tasks.sort(
        key=lambda t: (
            -score(t, settings),
            -blocks.get(t["id"], 0),
            effort_rank(t),
            parse_date(t.get("first_seen")) or TODAY,
            t["id"],
        )
    )
    return open_tasks


def load_today() -> dict:
    if TODAY_FILE.exists():
        try:
            data = json.loads(TODAY_FILE.read_text(encoding="utf-8"))
            data.setdefault("ids", [])
            data.setdefault("mode", "auto")
            data.setdefault("band_ids", [])
            data.setdefault("value_ids", [])
            data.setdefault("routine_ids", [])
            data.setdefault("filler_ids", [])
            data.setdefault("auto_added", [])
            return data
        except ValueError:
            pass
    return {
        "date": TODAY.isoformat(),
        "mode": "auto",
        "ids": [],
        "band_ids": [],
        "value_ids": [],
        "routine_ids": [],
        "filler_ids": [],
        "auto_added": [],
    }


def save_today(data: dict) -> None:
    TODAY_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _picks_from_today(tasks: dict, choice: dict) -> list[dict]:
    all_by_id = {t["id"]: t for t in tasks["tasks"]}
    picks = []
    for task_id in choice.get("ids") or []:
        task = all_by_id.get(task_id)
        if not task:
            continue
        if task.get("status") in ("open", "started"):
            picks.append(task)
        elif task.get("status") == "done" and task.get("completed") == TODAY.isoformat():
            picks.append(task)
    return picks


def build_day(tasks: dict, settings: dict | None = None, shuffle_normal: bool = False) -> dict:
    settings = settings if settings is not None else load_settings()
    ranked = ranked_open(tasks, settings)
    focus = int(settings["focus_size"])
    band = [t for t in ranked if band_bucket(t, settings) is not None]
    band.sort(key=lambda t: band_key(t, settings))
    band = band[:focus]
    used = {t["id"] for t in band}
    value_room = min(int(settings["value_slots"]), focus - len(band))
    value = [t for t in ranked if t["id"] not in used and is_value(t, settings)][:value_room]
    used.update(t["id"] for t in value)
    normal_room = focus - len(band) - len(value)
    normal_pool = [t for t in ranked if t["id"] not in used]
    if shuffle_normal:
        normal = random.sample(normal_pool, min(normal_room, len(normal_pool)))
    else:
        normal = normal_pool[:normal_room]
    ordered = band + value + normal
    fillers = []
    if len(ordered) < focus:
        used = {t["id"] for t in ordered}
        fillers = [t for t in lapsed_tasks(tasks, settings) if t["id"] not in used]
        fillers = fillers[: focus - len(ordered)]
        ordered += fillers
    return {
        "band": band,
        "value": value,
        "normal": normal,
        "fillers": fillers,
        "ids": [t["id"] for t in ordered],
        "band_ids": [t["id"] for t in band],
        "value_ids": [t["id"] for t in value],
        "filler_ids": [t["id"] for t in fillers],
    }


def snapshot_auto(tasks: dict, settings: dict | None = None, force: bool = False) -> None:
    """Freeze the automatic top picks for today, so completed ones stay visible."""
    settings = settings if settings is not None else load_settings()
    choice = load_today()
    if not force and choice.get("date") == TODAY.isoformat() and choice.get("ids"):
        return
    day = build_day(tasks, settings)
    save_today({
        "date": TODAY.isoformat(),
        "mode": "auto",
        "ids": day["ids"],
        "band_ids": day["band_ids"],
        "value_ids": day["value_ids"],
        "routine_ids": [],
        "filler_ids": day["filler_ids"],
        "auto_added": [],
    })


def shuffle_day(tasks: dict, settings: dict | None = None) -> list[dict]:
    settings = settings if settings is not None else load_settings()
    day = build_day(tasks, settings, shuffle_normal=True)
    choice = load_today()
    done = [t for t in _picks_from_today(tasks, choice) if t.get("status") == "done"]
    done_ids = [t["id"] for t in done if t["id"] not in day["ids"]]
    ids = day["ids"] + done_ids
    save_today({
        "date": TODAY.isoformat(),
        "mode": "shuffle",
        "ids": ids,
        "band_ids": day["band_ids"],
        "value_ids": day["value_ids"],
        "routine_ids": [],
        "filler_ids": day["filler_ids"],
        "auto_added": [],
    })
    all_by_id = {t["id"]: t for t in tasks["tasks"]}
    return [all_by_id[i] for i in ids if i in all_by_id]


def _refill(tasks: dict, picks: list[dict], settings: dict, choice: dict) -> tuple[list[dict], dict]:
    focus = int(settings["focus_size"])
    open_picks = [t for t in picks if t.get("status") in ("open", "started")]
    auto_added = list(choice.get("auto_added") or [])

    if len(open_picks) > focus:
        remove = []
        for task_id in reversed(auto_added):
            if len(open_picks) <= focus:
                break
            task = next((t for t in picks if t["id"] == task_id), None)
            if task and task.get("status") in ("open", "started"):
                remove.append(task_id)
                open_picks.remove(task)
        if remove:
            drop = set(remove)
            ids = [t["id"] for t in picks if t["id"] not in drop]
            auto_added = [i for i in auto_added if i not in drop]
            choice["ids"] = ids
            choice["auto_added"] = auto_added
            choice["band_ids"] = [i for i in (choice.get("band_ids") or []) if i not in drop]
            choice["value_ids"] = [i for i in (choice.get("value_ids") or []) if i not in drop]
            save_today(choice)
            all_by_id = {t["id"]: t for t in tasks["tasks"]}
            picks = [all_by_id[i] for i in ids if i in all_by_id]

    need = focus - len(open_picks)
    if need <= 0:
        return picks, choice

    used = {t["id"] for t in picks}
    ranked = ranked_open(tasks, settings)
    band = [t for t in ranked if t["id"] not in used and band_bucket(t, settings) is not None]
    band.sort(key=lambda t: band_key(t, settings))
    value = [t for t in ranked if t["id"] not in used and is_value(t, settings)]
    seen = used | {t["id"] for t in band} | {t["id"] for t in value}
    normal = [t for t in ranked if t["id"] not in seen]
    pool = band + value + normal
    seen = set()
    ordered = []
    for task in pool:
        if task["id"] in seen:
            continue
        seen.add(task["id"])
        ordered.append(task)

    pool_iter = iter(ordered)
    new_ids = []
    added = []
    for task in picks:
        new_ids.append(task["id"])
        if task.get("status") == "done" and need > 0:
            nxt = next(pool_iter, None)
            if nxt:
                new_ids.append(nxt["id"])
                added.append(nxt["id"])
                need -= 1
    while need > 0:
        nxt = next(pool_iter, None)
        if not nxt:
            break
        new_ids.append(nxt["id"])
        added.append(nxt["id"])
        need -= 1

    if added:
        choice["ids"] = new_ids
        choice["auto_added"] = auto_added + added
        band_ids = set(choice.get("band_ids") or [])
        value_ids = set(choice.get("value_ids") or [])
        all_by_id = {t["id"]: t for t in tasks["tasks"]}
        for task_id in added:
            task = all_by_id.get(task_id)
            if task and band_bucket(task, settings) is not None:
                band_ids.add(task_id)
            elif task and is_value(task, settings):
                value_ids.add(task_id)
        choice["band_ids"] = sorted(band_ids)
        choice["value_ids"] = sorted(value_ids)
        save_today(choice)
        picks = [all_by_id[i] for i in new_ids if i in all_by_id]
    return picks, choice


def today_picks(tasks: dict, n: int | None = None, settings: dict | None = None) -> tuple[list[dict], str]:
    """Return the focus list and how it was chosen: auto, shuffle, or manual."""
    settings = settings if settings is not None else load_settings()
    if n:
        settings = dict(settings)
        settings["focus_size"] = max(1, min(int(n), 10))
        settings["value_slots"] = min(int(settings["value_slots"]), int(settings["focus_size"]))
    choice = load_today()
    if choice.get("date") == TODAY.isoformat() and choice.get("ids"):
        picks = _picks_from_today(tasks, choice)
        if picks:
            if settings["refill_when_done"] and (choice.get("mode") or "auto") in ("auto", "shuffle"):
                picks, choice = _refill(tasks, picks, settings, choice)
            return picks, choice.get("mode") or "auto"
        snapshot_auto(tasks, settings, force=True)
        choice = load_today()
        return _picks_from_today(tasks, choice), choice.get("mode") or "auto"
    snapshot_auto(tasks, settings)
    choice = load_today()
    return _picks_from_today(tasks, choice), choice.get("mode") or "auto"


def trim_auto_added() -> dict:
    choice = load_today()
    auto = list(choice.get("auto_added") or [])
    if not auto:
        return choice
    drop = set(auto)
    choice["ids"] = [i for i in (choice.get("ids") or []) if i not in drop]
    choice["band_ids"] = [i for i in (choice.get("band_ids") or []) if i not in drop]
    choice["value_ids"] = [i for i in (choice.get("value_ids") or []) if i not in drop]
    choice["auto_added"] = []
    save_today(choice)
    return choice


def cmd_candidates(args) -> int:
    conn = sqlite3.connect(NOTES_DB)
    seen = set()
    parts = ["# Candidate notes for task extraction\n"]

    def add(note_id, ts, content, label):
        if note_id in seen:
            return
        seen.add(note_id)
        when = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "?"
        parts.append(f"\n\n## [{label}] {when} | note {note_id}\n\n{content[:6000]}")

    for nid, ts, content in conn.execute(
        "SELECT id, modification_ts, content FROM notes WHERE deleted=0"
    ):
        if re.search(r"^\s*[-*] \[ \]", content, re.M):
            add(nid, ts, content, "checkboxes")

    for nid, ts, content in conn.execute(
        "SELECT id, modification_ts, content FROM notes "
        "WHERE deleted=0 ORDER BY modification_ts DESC LIMIT ?",
        (args.n,),
    ):
        add(nid, ts, content, "recent")

    (HERE / "candidates.md").write_text("".join(parts), encoding="utf-8")
    print(f"candidates.md written: {len(seen)} notes")
    return 0


def wip(task: dict) -> str:
    if task.get("status") == "started":
        return " [started]"
    if task.get("status") == "done":
        return " [done]"
    return ""


def label(task: dict) -> str:
    category = task.get("category") or "none"
    return "" if category == "none" else f" [{category}]"


def flags_for(task: dict, settings: dict, tasks: dict | None = None) -> str:
    flags = []
    if band_bucket(task, settings) is not None:
        flags.append("band")
    if is_value(task, settings):
        flags.append("value")
    if tasks is not None and open_prerequisites(task, tasks):
        flags.append("prereq")
    return (" [" + ",".join(flags) + "]") if flags else ""


def cmd_rank(args) -> int:
    tasks = load_tasks()
    settings = load_settings()
    for i, task in enumerate(ranked_open(tasks, settings), 1):
        mark = "P" + str(task.get("tier") or 2)
        dl = f" (due {task['deadline']})" if task.get("deadline") else ""
        print(
            f"{i:3d}. [{mark}]{label(task)}{wip(task)}{flags_for(task, settings, tasks)} "
            f"{task['title']}{dl}  ~{score(task, settings):.1f}"
        )
    return 0


def cmd_check(args) -> int:
    issues = dependency_issues(load_tasks())
    if not issues:
        print("no dependency issues")
        return 0
    for issue in issues:
        print(issue)
    return 1


def cmd_shuffle(args) -> int:
    tasks = load_tasks()
    settings = load_settings()
    if args.n:
        settings = dict(settings)
        settings["focus_size"] = max(1, min(int(args.n), 10))
        settings["value_slots"] = min(int(settings["value_slots"]), int(settings["focus_size"]))
    picks = shuffle_day(tasks, settings)
    if not picks:
        print("nothing to shuffle")
        return 1
    for task in picks:
        print(f"{task['id']}  {task['title'][:80]}")
    return 0


def cmd_pick(args) -> int:
    tasks = load_tasks()
    settings = load_settings()
    open_ids = {t["id"] for t in ranked_open(tasks, settings)}
    bad = [i for i in args.ids if i not in open_ids]
    if bad:
        print("not open: " + ", ".join(bad))
        return 1
    save_today({
        "date": TODAY.isoformat(),
        "mode": "manual",
        "ids": args.ids,
        "band_ids": [],
        "value_ids": [],
        "routine_ids": [],
        "filler_ids": [],
        "auto_added": [],
    })
    print(f"{len(args.ids)} picks saved for {TODAY.isoformat()}")
    return 0


def cmd_auto(args) -> int:
    save_today({
        "date": TODAY.isoformat(),
        "mode": "auto",
        "ids": [],
        "band_ids": [],
        "value_ids": [],
        "routine_ids": [],
        "filler_ids": [],
        "auto_added": [],
    })
    print("focus list back to automatic")
    return 0


def render_digest(tasks: dict, top_n: int | None = None) -> str:
    settings = load_settings()
    ranked = ranked_open(tasks, settings)
    picks, mode = today_picks(tasks, top_n, settings)
    picked = {t["id"] for t in picks}
    lapsed_rows = lapsed_tasks(tasks, settings)
    lapsed_by_id = {t["id"]: t for t in lapsed_rows}
    checkins = checkin_queue(tasks, settings)
    today = TODAY.isoformat()
    lines = [f"# Morning digest — {today}", ""]
    if picks:
        lines.append(f"## Today's focus ({len(picks)} of {settings['focus_size']})")
        lines.append("")
        if mode == "shuffle":
            lines.append("Shuffled the normal rows; band and value rows stayed.")
            lines.append("")
        elif mode == "manual":
            lines.append("Your own picks.")
            lines.append("")
        for i, task in enumerate(picks, 1):
            dl = f" — due {task['deadline']}{due_label(task)}" if task.get("deadline") else ""
            src = f" (from note: {task['source_title'][:60]})" if task.get("source_title") else ""
            mar = wip(task)
            filler = lapsed_by_id.get(task["id"])
            if filler:
                mar += f" [lapsed {filler['lapse_days']}d, filler]"
            else:
                mar += flags_for(task, settings, tasks)
            lines.append(f"{i}. {task['title']}{mar}{label(task)}{dl}{src}")
        lines.append("")
    if checkins:
        lines.append("## Check-ins — still real?")
        lines.append("")
        for task in checkins:
            lines.append(
                f"- {task['title']} ({task['checkin_age_days']}d old, "
                f"due {task['checkin_due_date']})"
            )
        lines.append("")
        lines.append(
            f"{len(checkins)} check-ins in the console Check-ins tab, oldest first."
        )
        lines.append("")
    if lapsed_rows:
        lines.append("## Lapsed — still real?")
        lines.append("")
        shown = lapsed_rows[: int(settings["lapse_alert_top"])]
        for task in shown:
            lines.append(
                f"- {task['title']} ({task['lapse_days']}d late, {task['lapse_kind']})"
            )
        if len(lapsed_rows) > len(shown):
            lines.append(f"- {len(lapsed_rows) - len(shown)} more in the console.")
        lines.append("")
    rest = [t for t in ranked if t["id"] not in picked][:25]
    open_picked = sum(
        1 for t in picks
        if t.get("status") in ("open", "started") and t["id"] not in lapsed_by_id
    )
    lines.append(f"## Next up ({len(rest)} of {len(ranked) - open_picked})")
    lines.append("")
    for i, task in enumerate(rest, len(picks) + 1):
        dl = f" - due {task['deadline']}{due_label(task)}" if task.get("deadline") else ""
        pre = " [prereq]" if open_prerequisites(task, tasks) else ""
        lines.append(f"{i}. {task['title']}{pre}{dl}")
    lines.append("")
    lines.append(f"The full list of {len(ranked)} open tasks is in list.md.")
    done_recent = [
        t for t in tasks["tasks"]
        if t.get("status") == "done" and (t.get("completed") or "") >= today[:8] + "01"
    ]
    if done_recent:
        lines.append("")
        lines.append("## Recently completed")
        lines.append("")
        for task in done_recent:
            lines.append(f"- {task['title']} ({task.get('completed')})")
    lines.append("")
    per_tier = {}
    for task in ranked:
        per_tier[task.get("tier") or 2] = per_tier.get(task.get("tier") or 2, 0) + 1
    counts = ", ".join(f"P{k}: {v}" for k, v in sorted(per_tier.items()))
    lines.append(f"Counts: {counts}, Lapsed: {len(lapsed_rows)}, Check-ins: {len(checkins)}")
    return "\n".join(lines)


def cmd_digest(args) -> int:
    tasks = load_tasks()
    settings = load_settings()
    for issue in dependency_issues(tasks):
        print(f"dependency warning: {issue}")
    digest = render_digest(tasks)
    digest_path = HERE / f"digest-{TODAY.isoformat()}.md"
    digest_path.write_text(digest, encoding="utf-8")
    ranked = ranked_open(tasks, settings)
    list_lines = [f"# Open tasks by importance — updated {datetime.now():%Y-%m-%d %H:%M}", ""]
    for i, task in enumerate(ranked, 1):
        dl = f" — due {task['deadline']}" if task.get("deadline") else ""
        list_lines.append(
            f"{i}. [P{task.get('tier') or 2}]{label(task)}{wip(task)}"
            f"{flags_for(task, settings, tasks)} {task['title']}{dl}"
        )
    (HERE / "list.md").write_text("\n".join(list_lines) + "\n", encoding="utf-8")
    print(f"wrote {digest_path.name} and list.md ({len(ranked)} open tasks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("candidates")
    p.add_argument("--n", type=int, default=30)
    p.set_defaults(fn=cmd_candidates)
    p = sub.add_parser("rank")
    p.set_defaults(fn=cmd_rank)
    p = sub.add_parser("check")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("config")
    p.add_argument("key", nargs="?", default=None, choices=list(DEFAULTS))
    p.add_argument("value", nargs="?", default=None)
    p.set_defaults(fn=cmd_config)
    p = sub.add_parser("digest")
    p.set_defaults(fn=cmd_digest)
    p = sub.add_parser("shuffle")
    p.add_argument("--n", type=int, default=None)
    p.set_defaults(fn=cmd_shuffle)
    p = sub.add_parser("pick")
    p.add_argument("ids", nargs="+")
    p.set_defaults(fn=cmd_pick)
    p = sub.add_parser("auto")
    p.set_defaults(fn=cmd_auto)
    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
