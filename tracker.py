"""Task tracker for the Simplenote digest system.

tasks.json is the single reference list. This script never edits it; the agent
does. The script scores, ranks, and renders.

Commands:
    python tracker.py candidates [--n 30]   -> write candidates.md for extraction
    python tracker.py digest                -> write digest-YYYY-MM-DD.md + list.md
    python tracker.py rank                  -> print ranked open tasks to stdout

Scoring (from the Prioritization Engine spec, specs 01/03):
    tier_weight = 100 / 2**(tier-1)
    urgency     = 1/(days_until_deadline+1) if deadline else min(age_days*0.02, 0.5)
    score       = tier_weight * (1 + urgency + impact)
    sort: score desc, then first_seen ascending (old tasks stay on top at ties)
"""
import argparse
import json
import pathlib
import random
import re
import sqlite3
import sys
from datetime import date, datetime

HERE = pathlib.Path(__file__).parent
TASKS = HERE / "tasks.json"
NOTES_DB = HERE / "notes.db"
SETTINGS = HERE / "settings.json"
TODAY_FILE = HERE / "today.json"
TODAY = date.today()

DEFAULTS = {"use_deadlines": False, "auto_extract": True, "notify_top": 3, "read_only": True}


def load_settings() -> dict:
    data = dict(DEFAULTS)
    if SETTINGS.exists():
        data.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
    return data


def save_settings(data: dict) -> None:
    SETTINGS.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def cmd_config(args) -> int:
    data = load_settings()
    if args.key:
        current = data.get(args.key)
        if isinstance(current, bool):
            value = args.value.lower() in ("1", "true", "yes", "on")
        elif isinstance(current, int):
            value = int(args.value)
        else:
            value = args.value
        data[args.key] = value
        save_settings(data)
        print(f"{args.key} = {data[args.key]}")
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


def score(task: dict) -> float:
    tier = int(task.get("tier") or 2)
    tier_weight = 100 / 2 ** (tier - 1)
    deadline = parse_date(task.get("deadline")) if load_settings()["use_deadlines"] else None
    if deadline:
        urgency = 1 / (max((deadline - TODAY).days, 0) + 1)
    else:
        first = parse_date(task.get("first_seen")) or TODAY
        urgency = min((TODAY - first).days * 0.02, 0.5)
    impact = float(task.get("impact") or 0)
    return tier_weight * (1 + urgency + impact)


def ranked_open(tasks: dict) -> list[dict]:
    open_tasks = [t for t in tasks["tasks"] if t.get("status") in ("open", "started")]
    open_tasks.sort(
        key=lambda t: (-score(t), parse_date(t.get("first_seen")) or TODAY, t["id"])
    )
    return open_tasks


def load_today() -> dict:
    if TODAY_FILE.exists():
        try:
            return json.loads(TODAY_FILE.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"date": TODAY.isoformat(), "mode": "auto", "ids": []}


def save_today(data: dict) -> None:
    TODAY_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def today_picks(tasks: dict, n: int = 7) -> tuple[list[dict], str]:
    """Return the focus list and how it was chosen: auto, shuffle, or manual."""
    ranked = ranked_open(tasks)
    choice = load_today()
    if choice.get("date") == TODAY.isoformat() and choice.get("mode") in ("shuffle", "manual"):
        by_id = {t["id"]: t for t in ranked}
        picks = [by_id[i] for i in choice.get("ids", []) if i in by_id]
        if picks:
            return picks, choice["mode"]
    return ranked[:n], "auto"


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
    return " [started]" if task.get("status") == "started" else ""


def label(task: dict) -> str:
    category = task.get("category") or "none"
    return "" if category == "none" else f" [{category}]"


def cmd_rank(args) -> int:
    tasks = load_tasks()
    for i, task in enumerate(ranked_open(tasks), 1):
        mark = "P" + str(task.get("tier") or 2)
        dl = f" (due {task['deadline']})" if task.get("deadline") else ""
        print(f"{i:3d}. [{mark}]{label(task)}{wip(task)} {task['title']}{dl}  ~{score(task):.1f}")
    return 0


def cmd_shuffle(args) -> int:
    tasks = load_tasks()
    ranked = ranked_open(tasks)
    tier_one = [t for t in ranked if t.get("tier") == 1]
    pool = tier_one if len(tier_one) >= args.n else ranked
    picks = random.sample(pool, min(args.n, len(pool)))
    save_today({"date": TODAY.isoformat(), "mode": "shuffle", "ids": [t["id"] for t in picks]})
    for task in picks:
        print(f"{task['id']}  {task['title'][:80]}")
    return 0


def cmd_pick(args) -> int:
    tasks = load_tasks()
    open_ids = {t["id"] for t in ranked_open(tasks)}
    bad = [i for i in args.ids if i not in open_ids]
    if bad:
        print("not open: " + ", ".join(bad))
        return 1
    save_today({"date": TODAY.isoformat(), "mode": "manual", "ids": args.ids})
    print(f"{len(args.ids)} picks saved for {TODAY.isoformat()}")
    return 0


def cmd_auto(args) -> int:
    save_today({"date": TODAY.isoformat(), "mode": "auto", "ids": []})
    print("focus list back to automatic")
    return 0


def render_digest(tasks: dict, top_n: int = 7) -> str:
    ranked = ranked_open(tasks)
    picks, mode = today_picks(tasks, top_n)
    picked = {t["id"] for t in picks}
    today = TODAY.isoformat()
    lines = [f"# Morning digest — {today}", ""]
    if picks:
        lines.append(f"## Today's focus ({len(picks)})")
        lines.append("")
        if mode == "shuffle":
            lines.append("Shuffled from tier 1.")
            lines.append("")
        elif mode == "manual":
            lines.append("Your own picks.")
            lines.append("")
        for i, task in enumerate(picks, 1):
            dl = f" — due {task['deadline']}" if task.get("deadline") else ""
            src = f" (from note: {task['source_title'][:60]})" if task.get("source_title") else ""
            mar = wip(task)
            lines.append(f"{i}. {task['title']}{mar}{label(task)}{dl}{src}")
        lines.append("")
    rest = [t for t in ranked if t["id"] not in picked][:25]
    lines.append(f"## Next up ({len(rest)} of {len(ranked) - len(picks)})")
    lines.append("")
    for i, task in enumerate(rest, len(picks) + 1):
        dl = f" — due {task['deadline']}" if task.get("deadline") else ""
        lines.append(f"{i}. {task['title']}{dl}")
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
    lines.append(
        "Counts: " + ", ".join(f"P{k}: {v}" for k, v in sorted(per_tier.items()))
    )
    return "\n".join(lines)


def cmd_digest(args) -> int:
    tasks = load_tasks()
    digest = render_digest(tasks)
    digest_path = HERE / f"digest-{TODAY.isoformat()}.md"
    digest_path.write_text(digest, encoding="utf-8")
    ranked = ranked_open(tasks)
    list_lines = [f"# Open tasks by importance — updated {datetime.now():%Y-%m-%d %H:%M}", ""]
    for i, task in enumerate(ranked, 1):
        dl = f" — due {task['deadline']}" if task.get("deadline") else ""
        list_lines.append(f"{i}. [P{task.get('tier') or 2}]{label(task)}{wip(task)} {task['title']}{dl}")
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
    p = sub.add_parser("config")
    p.add_argument("key", nargs="?", default=None, choices=list(DEFAULTS))
    p.add_argument("value", nargs="?", default=None)
    p.set_defaults(fn=cmd_config)
    p = sub.add_parser("digest")
    p.set_defaults(fn=cmd_digest)
    p = sub.add_parser("shuffle")
    p.add_argument("--n", type=int, default=7)
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
