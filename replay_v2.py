"""Replay three ranking systems on tasks.json, then run scenario checks.

Systems compared:
  current       the live tracker.py scorer, imported unchanged
  newest_first  the brainstorm: tier, then newest first_seen, then impact
  v2            scoring_v2.py

Read only: nothing here writes to tasks.json, settings.json or tracker.py.

Part 1 replays the last N days on your real tasks. It assumes nothing gets
completed and uses today's stored tiers for past days, so it isolates what each
ordering rule does rather than predicting your real digests.

Part 2 injects synthetic tasks (ids starting zz_) into a copy of your tasks and
checks the properties v2 is meant to have. Your real tasks have no deadlines
today, so the deadline logic can only be exercised here. Each check also shows
the rank the current scorer gives the same task, with use_deadlines on.

Usage:
    python replay_v2.py [--as-of YYYY-MM-DD] [--days 19]

Exit status is 1 if any scenario check fails.
"""
import argparse
import importlib.util
import json
import pathlib
import re
import statistics
import sys
from datetime import date, timedelta

HERE = pathlib.Path(__file__).parent
TASKS = HERE / "tasks.json"
OPEN = ("open", "started")


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tracker = load_module("tracker_live", "tracker.py")
v2 = load_module("scoring_v2", "scoring_v2.py")
P = v2.parse_date


def id_number(task: dict) -> int:
    return int(re.sub(r"\D", "", task["id"]) or 0)


def sys_current(pool: list, today: date, deadlines: bool = False) -> list:
    tracker.TODAY = today
    settings = dict(tracker.DEFAULTS)
    settings["use_deadlines"] = deadlines
    return tracker.ranked_open({"tasks": pool}, settings)


def sys_newest(pool: list, today: date) -> list:
    live = [t for t in pool if t.get("status") in OPEN]
    return sorted(
        live,
        key=lambda t: (int(t["tier"]), -P(t["first_seen"]).toordinal(), -float(t.get("impact") or 0), -id_number(t)),
    )


def sys_v2(pool: list, today: date, cfg: dict | None = None) -> list:
    cfg = cfg if cfg is not None else v2.load_config()
    return [t for t, _ in v2.rank(pool, today, cfg)]


def position(order: list, task_id: str) -> int:
    return [t["id"] for t in order].index(task_id) + 1


# ---------------------------------------------------------------- part 1


def replay(open_tasks: list, today: date, days: int) -> dict:
    systems = {"current": sys_current, "newest_first": sys_newest, "v2": sys_v2}
    stats = {name: {"carry": [], "fresh": [], "age": []} for name in systems}
    previous = {}
    for back in range(days - 1, -1, -1):
        day = today - timedelta(days=back)
        pool = [t for t in open_tasks if P(t["first_seen"]) <= day]
        for name, fn in systems.items():
            top = fn(pool, day)[:7]
            ids = {t["id"] for t in top}
            ages = [(day - P(t["first_seen"])).days for t in top]
            if name in previous:
                stats[name]["carry"].append(len(ids & previous[name]))
            stats[name]["fresh"].append(sum(1 for a in ages if a <= 7) / 7)
            stats[name]["age"].append(statistics.median(ages))
            previous[name] = ids
    return stats


def part_one(open_tasks: list, today: date, days: int) -> None:
    hard = sum(1 for t in open_tasks if t.get("deadline"))
    soft = sum(1 for t in open_tasks if t.get("soft_deadline"))
    print(f"PART 1  replay of the last {days} days on {len(open_tasks)} open tasks (as of {today})")
    print(f"Tasks with a hard deadline: {hard}. With a soft deadline: {soft}.")
    if not hard and not soft:
        print("No dates in the data, so v2 ranks here by tier, impact, then oldest. Part 2 exercises the date logic.")
    print()
    stats = replay(open_tasks, today, days)
    print(f"{'system':<14}{'replaced per day':>18}{'top 7 aged <=7d':>18}{'median age of top 7':>22}")
    for name, s in stats.items():
        replaced = 7 - statistics.mean(s["carry"])
        print(f"{name:<14}{replaced:>18.1f}{statistics.mean(s['fresh']):>18.0%}{statistics.mean(s['age']):>20.1f}d")
    print("(replaced per day: of 7 focus tasks, how many are different from yesterday's)")
    print()

    cfg = v2.load_config()
    cur = sys_current(open_tasks, today)
    new = sys_v2(open_tasks, today, cfg)
    info = {t["id"]: i for t, i in v2.rank(open_tasks, today, cfg)}
    cur_top7 = {t["id"] for t in cur[:7]}
    print("v2 top 7 today:")
    for i, t in enumerate(new[:7], 1):
        age = (today - P(t["first_seen"])).days
        print(f"  {i}. {t['id']} P{info[t['id']]['tier']} age {age}d  {t['title'][:64]}")
    print(f"Overlap with the current top 7: {len(cur_top7 & {t['id'] for t in new[:7]})} of 7.")
    moved = [t for t in open_tasks if info[t["id"]]["tier"] != info[t["id"]]["stored_tier"]]
    cur_top25 = [t["id"] for t in cur[:25]]
    moved_top25 = sum(1 for i in cur_top25 if info[i]["tier"] != info[i]["stored_tier"])
    print(f"Stale decay lowers {len(moved)} tasks one tier today; {moved_top25} of them are in the current top 25.")
    print("Where the current top 25 land under v2:", [position(new, i) for i in cur_top25])
    print()


# ---------------------------------------------------------------- part 2


def make_task(tid, tier, impact, today, age=0, deadline=None, soft=None, raw_deadline=None):
    def offset(days):
        return None if days is None else (today + timedelta(days=days)).isoformat()

    return {
        "id": tid,
        "title": f"[scenario {tid}]",
        "status": "open",
        "tier": tier,
        "impact": impact,
        "first_seen": (today - timedelta(days=age)).isoformat(),
        "deadline": raw_deadline if raw_deadline is not None else offset(deadline),
        "soft_deadline": offset(soft),
        "category": "none",
        "why": "",
    }


def part_two(open_tasks: list, today: date) -> bool:
    cfg = dict(v2.DEFAULTS)
    results = []

    def run(extra: list):
        return sys_v2(open_tasks + extra, today, cfg)

    def current_rank(extra: list, tid: str) -> str:
        try:
            return str(position(sys_current(open_tasks + extra, today, deadlines=True), tid))
        except Exception as exc:  # noqa: BLE001 - report the live scorer's failure as data
            return f"crashes ({type(exc).__name__})"

    def undated(t: dict) -> bool:
        return not t.get("deadline") and not t.get("soft_deadline")

    banded = sum(1 for t in open_tasks if v2.explain(t, today, cfg)["band"] < 2)

    # S1 freshness earns no lift
    t = make_task("zz_s1", 1, 0.4, today)
    o = run([t])
    equals = [
        x for x in o
        if x["id"] != t["id"] and undated(x)
        and v2.explain(x, today, cfg)["tier"] == 1 and float(x.get("impact") or 0) == 0.4
    ]
    ok = all(position(o, t["id"]) > position(o, x["id"]) for x in equals)
    results.append(("S1", "new tier 1 task, no date, ranks behind every equal that is older", ok,
                    f"v2 rank {position(o, t['id'])} of {len(o)}, behind {len(equals)} equals; current rank {current_rank([t], t['id'])}"))

    # S2 hard deadline in 3 days beats the whole tier structure
    t = make_task("zz_s2", 4, 0.0, today, age=30, deadline=3)
    o = run([t])
    ok = v2.explain(t, today, cfg)["band"] == 1 and position(o, t["id"]) <= banded + 1
    results.append(("S2", "tier 4 task with a hard deadline in 3 days jumps to the top", ok,
                    f"v2 rank {position(o, t['id'])}; current rank {current_rank([t], t['id'])}"))

    # S3 order inside the deadline bands
    a = make_task("zz_s3a", 3, 0.1, today, age=40, deadline=-10)
    b = make_task("zz_s3b", 1, 0.4, today, age=10, deadline=3)
    c = make_task("zz_s3c", 1, 0.4, today, age=10, deadline=12)
    o = run([a, b, c])
    ok = position(o, a["id"]) < position(o, b["id"]) < position(o, c["id"])
    results.append(("S3", "overdue first, then due in 3 days, then due in 12 days", ok,
                    f"v2 ranks {position(o, a['id'])}, {position(o, b['id'])}, {position(o, c['id'])}; "
                    f"current ranks {current_rank([a, b, c], a['id'])}, {current_rank([a, b, c], b['id'])}, {current_rank([a, b, c], c['id'])}"))

    # S4 a far deadline never scores lower than no deadline
    plain = make_task("zz_s4a", 1, 0.4, today, age=20)
    dated = make_task("zz_s4b", 1, 0.4, today, age=20, deadline=30)
    o = run([plain, dated])
    ok = position(o, dated["id"]) < position(o, plain["id"]) and v2.explain(dated, today, cfg)["band"] == 2
    results.append(("S4", "deadline 30 days out is not punished and does not jump the queue", ok,
                    f"v2 ranks dated {position(o, dated['id'])} vs plain {position(o, plain['id'])}; "
                    f"current ranks dated {current_rank([plain, dated], dated['id'])} vs plain {current_rank([plain, dated], plain['id'])}"))

    # S5 a soft deadline lifts a brand-new task past the undated tier 1 pile
    t = make_task("zz_s5", 1, 0.4, today, soft=1)
    o = run([t])
    pile = [x for x in o if x["id"] != t["id"] and undated(x) and v2.explain(x, today, cfg)["tier"] == 1]
    ok = all(position(o, t["id"]) < position(o, x["id"]) for x in pile)
    results.append(("S5", "new task with a soft date tomorrow outranks every undated tier 1 task", ok,
                    f"v2 rank {position(o, t['id'])} ahead of {len(pile)} undated tier 1; current rank {current_rank([t], t['id'])} (field ignored)"))

    # S6 stale decay, and a live date overrides it
    stale = make_task("zz_s6a", 1, 0.4, today, age=60)
    rescued = make_task("zz_s6b", 1, 0.4, today, age=60, deadline=2)
    o = run([stale, rescued])
    fresh_t1 = [
        x for x in o
        if not x["id"].startswith("zz_") and undated(x) and v2.explain(x, today, cfg)["tier"] == 1
    ]
    ok = (
        all(position(o, stale["id"]) > position(o, x["id"]) for x in fresh_t1)
        and v2.explain(rescued, today, cfg)["band"] == 1
        and position(o, rescued["id"]) <= banded + 1
    )
    results.append(("S6", "60-day-old tier 1 task drops a tier, unless it has a live deadline", ok,
                    f"v2 ranks stale {position(o, stale['id'])}, with deadline {position(o, rescued['id'])}; "
                    f"current ranks stale {current_rank([stale, rescued], stale['id'])}, with deadline {current_rank([stale, rescued], rescued['id'])}"))

    # S7 identical tasks, older one first
    old = make_task("zz_s7a", 1, 0.4, today, age=30)
    fresh = make_task("zz_s7b", 1, 0.4, today, age=0)
    o = run([old, fresh])
    ok = position(o, old["id"]) < position(o, fresh["id"])
    results.append(("S7", "two identical tasks: the older one ranks first", ok,
                    f"v2 ranks old {position(o, old['id'])}, new {position(o, fresh['id'])}"))

    # S8 a hard deadline outranks a soft one on a higher tier
    hard = make_task("zz_s8a", 4, 0.0, today, age=1, deadline=5)
    soft = make_task("zz_s8b", 1, 0.5, today, age=1, soft=0)
    o = run([hard, soft])
    ok = position(o, hard["id"]) < position(o, soft["id"])
    results.append(("S8", "hard deadline in 5 days outranks a tier 1 task with a soft date today", ok,
                    f"v2 ranks hard {position(o, hard['id'])}, soft {position(o, soft['id'])}"))

    # S9 a malformed date must not break the ranking
    bad = make_task("zz_s9", 1, 0.4, today, raw_deadline="tomorrow")
    try:
        o = run([bad])
        ok = v2.explain(bad, today, cfg)["band"] == 2
        detail = f"v2 ranks it {position(o, bad['id'])} as undated; current scorer: {current_rank([bad], bad['id'])}"
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"v2 crashed: {type(exc).__name__}"
    results.append(("S9", "malformed deadline text is ignored, not a crash", ok, detail))

    # S10 horizon boundary
    inside = make_task("zz_s10a", 2, 0.3, today, deadline=cfg["deadline_horizon_days"])
    outside = make_task("zz_s10b", 2, 0.3, today, deadline=cfg["deadline_horizon_days"] + 1)
    ok = v2.explain(inside, today, cfg)["band"] == 1 and v2.explain(outside, today, cfg)["band"] == 2
    results.append(("S10", f"horizon edge: {cfg['deadline_horizon_days']} days is inside, {cfg['deadline_horizon_days'] + 1} is not", ok, ""))

    print(f"PART 2  scenario checks (synthetic tasks added to a copy of your {len(open_tasks)} open tasks)")
    print()
    for name, text, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'} {name}  {text}")
        if detail:
            print(f"       {detail}")
    failed = [r for r in results if not r[2]]
    print()
    print(f"{len(results) - len(failed)} of {len(results)} scenario checks passed.")
    return not failed


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--as-of", default=None, help="treat this date as today")
    parser.add_argument("--days", type=int, default=19, help="days of replay in part 1")
    args = parser.parse_args()
    today = date.fromisoformat(args.as_of) if args.as_of else date.today()
    data = json.loads(TASKS.read_text(encoding="utf-8"))
    open_tasks = [t for t in data["tasks"] if t.get("status") in OPEN]
    part_one(open_tasks, today, args.days)
    ok = part_two(open_tasks, today)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
