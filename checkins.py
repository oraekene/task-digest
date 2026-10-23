"""45-day check-ins for long-running tasks.

A task with no date, not started, and no source-note edit for 45 days gets one
Jev question: is this a multi-week project or a single action? A likely project
waits for a human answer. Everything else drops one tier automatically, and the
drop is reversible.

Run:  python checkins.py run [--dry-run] [--no-jev]
      python checkins.py answer ID active|done|not|unsure|restore
"""
import json
import pathlib
import sys
from datetime import date, timedelta

import jev_categorize
import tracker

HERE = pathlib.Path(__file__).parent
CACHE = HERE / "checkin_cache.json"
THRESHOLD = 0.7
BATCH = 20


def load_cache() -> dict:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def save_cache(cache: dict) -> None:
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def save_tasks(data: dict) -> None:
    tracker.TASKS.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def signature(task: dict, notes: dict) -> str:
    edited = notes.get(str(task.get("source_note") or ""))
    return "|".join([
        str(task.get("first_seen") or ""),
        str(edited or ""),
        str(task.get("title") or ""),
        str(task.get("tier") or ""),
    ])


def restore_tier(task: dict) -> None:
    old = task.get("demoted_from")
    if old is not None:
        task["tier"] = int(old)
    parts = [
        part for part in (task.get("why") or "").split(" | ")
        if part.strip() and part.strip() != "demoted: stale"
    ]
    task["why"] = " | ".join(parts).strip(" |")


def clear_checkin(task: dict) -> None:
    for key in (
        "checkin_state", "checkin_due", "checkin_until", "checkin_at",
        "checkin_jev", "checkin_reason", "demoted_from", "demoted_at",
    ):
        task.pop(key, None)


def demote(task: dict, due: date, prob: float | None, reason: str) -> None:
    old = int(task.get("tier") or 2)
    task["demoted_from"] = old
    task["tier"] = min(old + 1, 4)
    task["demoted_at"] = tracker.TODAY.isoformat()
    task["checkin_state"] = "demoted"
    task["checkin_due"] = due.isoformat()
    task["checkin_at"] = tracker.TODAY.isoformat()
    task["checkin_jev"] = prob
    task["checkin_reason"] = reason
    task.pop("checkin_until", None)
    why = (task.get("why") or "").strip(" |")
    if "demoted: stale" not in why:
        task["why"] = f"{why} | demoted: stale" if why else "demoted: stale"


def queue_task(task: dict, due: date, prob: float | None, reason: str) -> None:
    task["checkin_state"] = "queued"
    task["checkin_due"] = due.isoformat()
    task["checkin_at"] = tracker.TODAY.isoformat()
    task["checkin_jev"] = prob
    task["checkin_reason"] = reason
    task.pop("checkin_until", None)


def free_signal(task: dict, notes: dict) -> bool:
    if task.get("status") == "started":
        return True
    if task.get("deadline") or task.get("soft_deadline"):
        return True
    since = tracker.parse_date(task.get("demoted_at") or task.get("checkin_at"))
    edited = notes.get(str(task.get("source_note") or ""))
    return bool(since and edited and edited > since)


def judge(candidates: list, all_tasks: list, notes: dict, cache: dict) -> dict:
    key = jev_categorize.api_key()
    if not key:
        return {}
    sigs = {task["id"]: signature(task, notes) for task in candidates}
    out = {}
    todo = []
    for task in candidates:
        hit = cache.get(task["id"])
        if hit and hit.get("sig") == sigs[task["id"]] and isinstance(hit.get("prob"), (int, float)):
            out[task["id"]] = float(hit["prob"])
        else:
            todo.append(task)

    siblings = {}
    for task in all_tasks:
        if task.get("status") not in ("open", "started"):
            continue
        note_id = str(task.get("source_note") or "")
        if note_id:
            siblings.setdefault(note_id, []).append(task.get("title") or "")

    for i in range(0, len(todo), BATCH):
        chunk = todo[i : i + BATCH]
        state = {"batch": []}
        for task in chunk:
            note_id = str(task.get("source_note") or "")
            near = [
                title for title in siblings.get(note_id, [])
                if title != task.get("title")
            ][:5]
            state["batch"].append({
                "title": task.get("title") or "",
                "detail": (task.get("why") or "")[:400],
                "quote": (task.get("quote") or "")[:800],
                "source": (task.get("source_title") or "")[:200],
                "siblings": near,
            })
        questions = {
            f"t{j:03d}": {
                "type": "choice",
                "instructions": (
                    f"Is task `batch.{j}.title` a multi-week project rather than a "
                    "single action? Use the quote, detail and sibling tasks. Answer "
                    "yes only when the note shows a multi-step or multi-week shape."
                ),
                "criteria": {
                    "yes": "the note shows a multi-step or multi-week project",
                    "no": "the task is a single action, or the note gives no project evidence",
                },
            }
            for j in range(len(chunk))
        }
        ans = jev_categorize.call(state, questions, key)
        if "error" in ans:
            print(f"check-in batch {i // BATCH + 1}: {ans['error']}")
            continue
        for j, task in enumerate(chunk):
            answer = (ans.get("answers") or {}).get(f"t{j:03d}") or {}
            probs = answer.get("probabilities") or {}
            try:
                prob = float(probs.get("yes", 0))
            except (TypeError, ValueError):
                prob = 0.0
            if not probs:
                prob = float(answer.get("confidence") or 0) if answer.get("choice") == "yes" else 0.0
            out[task["id"]] = prob
            cache[task["id"]] = {"sig": sigs[task["id"]], "prob": prob}
        save_cache(cache)
        print(f"check-in batch {i // BATCH + 1}: {len(chunk)} judged", flush=True)
    return out


def run(settings: dict | None = None, notes: dict | None = None,
        use_jev: bool = True, dry_run: bool = False) -> dict:
    data = tracker.load_tasks()
    tasks = data["tasks"]
    settings = settings if settings is not None else tracker.load_settings()
    notes = tracker.note_edit_dates() if notes is None else notes
    cache = load_cache()

    restored = reset = expired = 0
    for task in tasks:
        state = task.get("checkin_state")
        if state not in ("queued", "active", "demoted"):
            continue
        if task.get("status") not in ("open", "started"):
            continue
        if free_signal(task, notes):
            if state == "demoted":
                restore_tier(task)
                restored += 1
            else:
                reset += 1
            clear_checkin(task)
            continue
        if state == "queued":
            asked = tracker.parse_date(task.get("checkin_at"))
            wait = int(settings["checkin_answer_days"])
            if asked and (tracker.TODAY - asked).days >= wait:
                due = tracker.parse_date(task.get("checkin_due")) or tracker.TODAY
                demote(task, due, task.get("checkin_jev"), "no answer")
                expired += 1

    rows = tracker.checkin_queue(data, settings, notes=notes)
    by_id = {task["id"]: task for task in tasks}
    due_by_id = {row["id"]: row["checkin_due_date"] for row in rows}
    candidates = [
        by_id[row["id"]] for row in rows
        if row.get("checkin_state") in (None, "active") and row["id"] in by_id
    ]

    has_key = bool(jev_categorize.api_key())
    probs = judge(candidates, tasks, notes, cache) if use_jev and has_key and candidates else {}
    queued = demoted = 0
    decisions = []
    for task in candidates:
        due = tracker.parse_date(due_by_id.get(task["id"])) or tracker.TODAY
        prob = probs.get(task["id"])
        if not use_jev:
            queue_task(task, due, None, "Jev skipped")
            queued += 1
            decisions.append((task, None, "queued"))
        elif not has_key:
            queue_task(task, due, None, "no Jev key")
            queued += 1
            decisions.append((task, None, "queued"))
        elif prob is None:
            queue_task(task, due, None, "Jev unavailable")
            queued += 1
            decisions.append((task, None, "queued"))
        elif prob >= THRESHOLD:
            queue_task(task, due, prob, f"jev {prob:.2f}")
            queued += 1
            decisions.append((task, prob, "queued"))
        else:
            demote(task, due, prob, f"jev {prob:.2f}")
            demoted += 1
            decisions.append((task, prob, "demoted"))

    if not dry_run:
        save_tasks(data)
        save_cache(cache)

    queue_total = sum(1 for task in tasks if task.get("checkin_state") == "queued")
    summary = {
        "due": len(candidates),
        "queued": queued,
        "demoted": demoted,
        "expired": expired,
        "restored": restored,
        "reset": reset,
        "queue_total": queue_total,
        "dry_run": dry_run,
    }
    if dry_run:
        for task, prob, decision in decisions:
            score = "no Jev" if prob is None else f"jev {prob:.2f}"
            print(f"  {task['id']} {score:>8} -> {decision}  {task['title'][:70]}")
    print(
        "check-ins: due {due}, queued {queued}, demoted {demoted}, "
        "expired {expired}, restored {restored}, reset {reset}, total {queue_total}".format(**summary)
    )
    return summary


def answer(task_id: str, action: str, settings: dict | None = None) -> dict:
    data = tracker.load_tasks()
    settings = settings if settings is not None else tracker.load_settings()
    task = next((t for t in data["tasks"] if t["id"] == task_id), None)
    if task is None:
        raise ValueError("no such task")
    if action not in ("active", "done", "not", "unsure", "restore"):
        raise ValueError("bad check-in action")
    due = tracker.parse_date(task.get("checkin_due")) or tracker.TODAY
    active_until = (
        tracker.TODAY + timedelta(days=int(settings["checkin_active_days"]))
    ).isoformat()

    if action in ("active", "restore"):
        if task.get("checkin_state") == "demoted":
            restore_tier(task)
        clear_checkin(task)
        task["checkin_state"] = "active"
        task["checkin_until"] = active_until
        task["checkin_at"] = tracker.TODAY.isoformat()
        task["checkin_reason"] = "still active" if action == "active" else "restored"
    elif action == "done":
        if task.get("checkin_state") == "demoted":
            restore_tier(task)
        clear_checkin(task)
        task["status"] = "done"
        task["completed"] = tracker.TODAY.isoformat()
    elif action == "not":
        if task.get("checkin_state") == "demoted":
            restore_tier(task)
        clear_checkin(task)
        task["status"] = "dropped"
        task["drop_reason"] = task.get("drop_reason") or "not active after check-in"
    elif action == "unsure":
        if task.get("checkin_state") != "demoted":
            demote(task, due, task.get("checkin_jev"), "not sure")

    save_tasks(data)
    return task


def main() -> int:
    args = sys.argv[1:]
    mode = args[0] if args else "run"
    if mode == "run":
        run(use_jev="--no-jev" not in args, dry_run="--dry-run" in args)
        return 0
    if mode == "answer":
        if len(args) < 3:
            print("usage: python checkins.py answer ID active|done|not|unsure|restore")
            return 2
        task = answer(args[1], args[2])
        print(f"{task['id']} {task.get('checkin_state') or task.get('status')} {task['title'][:70]}")
        return 0
    print("usage: python checkins.py run [--dry-run] [--no-jev]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
