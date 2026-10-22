"""Scoring variant v2 for the task digest. An experiment, not wired in.

Standalone: reads tasks.json and settings.json, writes nothing. tracker.py is
untouched, so the live digest keeps using the current scorer until you switch.

The problem it answers. The current scorer uses a task's age as its urgency.
Age is a poor stand-in for urgency:
  - it saturates at 25 days, so 29 tier 1 tasks tie at 190.0 and the order
    becomes oldest-first;
  - a new task that is truly urgent but has no deadline starts at the bottom
    of its tier;
  - a deadline replaces the age term, so only a task due today or tomorrow
    beats the undated pile, and a far deadline scores LOWER than no deadline.
Ranking newest-first fixes none of that and adds new-shiny-thing syndrome.

Rank order, first rule that applies wins:
  band 0  hard deadline overdue                most overdue first
  band 1  hard deadline within the horizon     soonest first, then tier, impact
  band 2  everything else                      tier, then urgency + impact,
                                               then oldest first

Urgency is time-to-consequence only: 1 / (days + 1) from the hard `deadline`
or the optional `soft_deadline` (a target date with no hard consequence),
whichever is closer. It is added to the score and never replaces anything, so
a far deadline can never score lower than no deadline. Age adds nothing to the
score. It only breaks exact ties, oldest first, so being new earns no lift.

Stale decay replaces the manual demote_stale.py step. An open task first seen
more than `stale_days` ago, with no live date, sorts one tier lower. This is
computed at ranking time and never written to tasks.json. A task already
flagged "demoted: stale" is not demoted twice.

Settings (all optional, read from settings.json, never written):
  deadline_horizon_days  14    how close a hard deadline must be to jump the queue
  auto_stale             true  apply stale decay at ranking time
  stale_days             45    same cutoff as demote_stale.py

Usage:
    python scoring_v2.py rank [--n 30] [--as-of YYYY-MM-DD]
"""
import argparse
import json
import pathlib
import sys
from datetime import date

HERE = pathlib.Path(__file__).parent
TASKS = HERE / "tasks.json"
SETTINGS = HERE / "settings.json"

DEFAULTS = {"deadline_horizon_days": 14, "auto_stale": True, "stale_days": 45}
DATE_FIELDS = ("deadline", "soft_deadline")


def load_config(path: pathlib.Path = SETTINGS) -> dict:
    cfg = dict(DEFAULTS)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in DEFAULTS:
            if key in data:
                cfg[key] = data[key]
    return cfg


def parse_date(value):
    """An ISO date or None. A malformed string is treated as no date, never a crash."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def days_to(value, today: date):
    parsed = parse_date(value)
    return None if parsed is None else (parsed - today).days


def has_live_date(task: dict, today: date, cfg: dict) -> bool:
    """A hard or soft date that is overdue or inside the horizon."""
    for field in DATE_FIELDS:
        days = days_to(task.get(field), today)
        if days is not None and days <= cfg["deadline_horizon_days"]:
            return True
    return False


def effective_tier(task: dict, today: date, cfg: dict) -> int:
    tier = int(task.get("tier") or 2)
    if not cfg["auto_stale"] or task.get("status") != "open":
        return tier
    if "demoted: stale" in (task.get("why") or ""):
        return tier
    first = parse_date(task.get("first_seen"))
    if first is None or (today - first).days <= cfg["stale_days"]:
        return tier
    if has_live_date(task, today, cfg):
        return tier
    return min(tier + 1, 4)


def urgency(task: dict, today: date) -> float:
    best = 0.0
    for field in DATE_FIELDS:
        days = days_to(task.get(field), today)
        if days is not None:
            best = max(best, 1 / (max(days, 0) + 1))
    return best


def explain(task: dict, today: date, cfg: dict) -> dict:
    tier = effective_tier(task, today, cfg)
    stored = int(task.get("tier") or 2)
    urg = urgency(task, today)
    impact = float(task.get("impact") or 0)
    hard = days_to(task.get("deadline"), today)
    if hard is None or hard > cfg["deadline_horizon_days"]:
        band = 2
    elif hard < 0:
        band = 0
    else:
        band = 1
    first = parse_date(task.get("first_seen")) or today
    return {
        "band": band,
        "hard_days": hard,
        "tier": tier,
        "stored_tier": stored,
        "urgency": urg,
        "impact": impact,
        "score": 100 / 2 ** (tier - 1) * (1 + urg + impact),
        "age": (today - first).days,
        "first_seen": first,
    }


def sort_key(task: dict, info: dict) -> tuple:
    if info["band"] < 2:
        return (info["band"], info["hard_days"], info["tier"], -info["impact"], info["first_seen"], task["id"])
    return (2, info["tier"], -round(info["urgency"] + info["impact"], 6), info["first_seen"], task["id"])


def rank(tasks: list, today: date | None = None, cfg: dict | None = None) -> list:
    """Open and started tasks as (task, info) pairs, best first."""
    today = today or date.today()
    cfg = cfg if cfg is not None else load_config()
    pairs = []
    for task in tasks:
        if task.get("status") not in ("open", "started"):
            continue
        pairs.append((task, explain(task, today, cfg)))
    pairs.sort(key=lambda pair: sort_key(*pair))
    return pairs


def cmd_rank(args) -> int:
    today = date.fromisoformat(args.as_of) if args.as_of else date.today()
    cfg = load_config()
    data = json.loads(TASKS.read_text(encoding="utf-8"))
    pairs = rank(data["tasks"], today, cfg)
    bad = sum(
        1
        for t, _ in pairs
        for field in DATE_FIELDS
        if t.get(field) and parse_date(t.get(field)) is None
    )
    print(f"v2 ranking as of {today}  (horizon {cfg['deadline_horizon_days']}d, "
          f"auto_stale {cfg['auto_stale']} at {cfg['stale_days']}d)")
    for i, (task, info) in enumerate(pairs[: args.n], 1):
        due = ""
        if task.get("deadline"):
            due += f" due {task['deadline']}"
        if task.get("soft_deadline"):
            due += f" soft {task['soft_deadline']}"
        moved = "*" if info["tier"] != info["stored_tier"] else " "
        print(f"{i:3d}. [B{info['band']}] P{info['tier']}{moved} {info['score']:6.1f} "
              f"age {info['age']:3d}d{due}  {task['title'][:70]}")
    bands = {b: sum(1 for _, i in pairs if i["band"] == b) for b in (0, 1, 2)}
    stale = sum(1 for _, i in pairs if i["tier"] != i["stored_tier"])
    print(f"\n{len(pairs)} open. Band 0 (overdue): {bands[0]}, band 1 (inside horizon): {bands[1]}, "
          f"rest: {bands[2]}. Stale decay moved {stale} tasks down one tier (marked *).")
    if bad:
        print(f"WARNING: {bad} date value(s) could not be parsed and were ignored.")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("rank", help="print the v2 ranking")
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--as-of", default=None, help="rank as if today were this date")
    p.set_defaults(fn=cmd_rank)
    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
