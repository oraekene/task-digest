"""Split notes into batches for the state audit.

Each note goes with the tasks already extracted from it, so the auditor can
compare the note text against the current state of each task. Only notes that
have at least one extracted task are dumped: with no task listed, the auditor
has nothing to judge.

Scopes:
    full     every note that has tasks.
    retry    only the notes carried over from a batch that failed.
    monthly  the notes worth a second look since the last audit:
               1. notes edited since that note was last audited,
               2. notes carried over from an audit batch that failed,
               3. notes holding a task whose category is none, or a task Jev
                  labelled with low confidence, once the note has gone
                  recheck-days (default 90) without an audit; the longest
                  unaudited come first and at most recheck-cap (default 150)
                  come in one run,
               4. a random sample (10 percent by default) of all the others,
                  seeded by the month so a rerun in the same month picks the
                  same notes.
             Rule 3 is the all-none rule with a limit on it. Taken literally
             on the real notes it picks 79 percent of every note every month,
             because most notes hold at least one none task. With the limit,
             every none note still comes back, about once a quarter, and a run
             never costs more than the cap plus the notes that really changed.
             --recheck-days 0 --recheck-cap 0 gives the literal rule.

Usage:
    python audit_dump.py [batch_size] [--scope full|monthly] [--since ISO_DATE]
                         [--sample-pct 10] [--recheck-days 90] [--recheck-cap 150]
                         [--month YYYY-MM] [--dry-run]

Writes audit_in_01.json ... and removes the previous audit_in_*.json and
audit_out_*.json, because old outputs belong to old inputs.
"""
import argparse
import json
import math
import pathlib
import random
import sqlite3
import time
from datetime import datetime

HERE = pathlib.Path(__file__).parent
CONTENT_LIMIT = 12000
DEFAULT_BATCH = 80
DEFAULT_SAMPLE_PCT = 10.0
DEFAULT_RECHECK_DAYS = 90
DEFAULT_RECHECK_CAP = 150
JEV_LOW = 0.6


def jev_is_low(confidence) -> bool:
    """True when Jev's stored confidence is low or unknown."""
    if confidence is None:
        return True
    if isinstance(confidence, bool):
        return True
    if isinstance(confidence, (int, float)):
        value = float(confidence)
        return (value / 100 if value > 1 else value) < JEV_LOW
    text = str(confidence).strip().lower()
    try:
        value = float(text)
        return (value / 100 if value > 1 else value) < JEV_LOW
    except ValueError:
        return text in ("", "low")


def jev_low_task_ids() -> set:
    path = HERE / "jev_cache.json"
    if not path.exists():
        return set()
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {tid for tid, row in cache.items() if jev_is_low((row or {}).get("confidence"))}


def load_tasks_by_note() -> dict:
    tasks = json.loads((HERE / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    by_note = {}
    for t in tasks:
        if t.get("status") == "dropped":  # you dropped it on purpose; nothing to audit
            continue
        by_note.setdefault(t["source_note"], []).append(
            {
                "id": t["id"],
                "title": t["title"],
                "status": t.get("status"),
                "tier": t.get("tier"),
                "category": t.get("category") or "none",
            }
        )
    return by_note


def load_notes(by_note: dict) -> list:
    """Every live note that has tasks, oldest edit first. `ts` is kept for scoping."""
    conn = sqlite3.connect(HERE / "notes.db")
    try:
        rows = conn.execute(
            "SELECT id, modification_ts, content FROM notes WHERE deleted=0 ORDER BY modification_ts"
        ).fetchall()
    finally:
        conn.close()
    notes = []
    for nid, ts, content in rows:
        if nid not in by_note:
            continue
        notes.append(
            {
                "id": nid,
                "ts": ts or 0,
                "date": datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "?",
                "truncated": len(content) > CONTENT_LIMIT,
                "tasks": by_note[nid],
                "content": content[:CONTENT_LIMIT],
            }
        )
    return notes


def select_monthly(notes, baseline_ts, sample_pct, month, ledger=None, carry_ids=(),
                   recheck_days=DEFAULT_RECHECK_DAYS, recheck_cap=DEFAULT_RECHECK_CAP, now_ts=None):
    """Return (selected notes, counts by reason). A note counts under its first reason.

    ledger maps note id -> when that note was last audited. A note missing from
    it is treated as audited at baseline_ts, the time of the last full audit.
    """
    ledger = ledger or {}
    now_ts = time.time() if now_ts is None else now_ts
    low_jev = jev_low_task_ids()
    carry = set(carry_ids)
    picked, rest, recheck = [], [], []
    counts = {"edited": 0, "carry-over": 0, "none": 0, "jev-low": 0, "sample": 0}
    for n in notes:
        last = ledger.get(n["id"], baseline_ts)
        if n["ts"] > last:
            counts["edited"] += 1
        elif n["id"] in carry:
            counts["carry-over"] += 1
        elif now_ts - last >= max(0, recheck_days) * 86400 and any(t["category"] == "none" for t in n["tasks"]):
            recheck.append((last, n["id"], n, "none"))
            continue
        elif now_ts - last >= max(0, recheck_days) * 86400 and any(t["id"] in low_jev for t in n["tasks"]):
            recheck.append((last, n["id"], n, "jev-low"))
            continue
        else:
            rest.append(n)
            continue
        picked.append(n)
    recheck.sort(key=lambda r: (r[0], r[1]))
    take = recheck if not recheck_cap else recheck[: int(recheck_cap)]
    for _, _, n, why in take:
        counts[why] += 1
        picked.append(n)
    rest.extend(n for _, _, n, _ in recheck[len(take):])
    rest.sort(key=lambda n: n["id"])
    k = min(len(rest), math.ceil(len(rest) * sample_pct / 100.0))
    sample = random.Random(month).sample(rest, k) if k else []
    counts["sample"] = len(sample)
    picked.extend(sample)
    picked.sort(key=lambda n: (n["ts"], n["id"]))
    return picked, counts


def build(scope="full", batch=DEFAULT_BATCH, since_ts=None, sample_pct=DEFAULT_SAMPLE_PCT,
          month=None, carry_ids=(), write=True, ledger=None, recheck_days=DEFAULT_RECHECK_DAYS,
          recheck_cap=DEFAULT_RECHECK_CAP, now_ts=None) -> dict:
    """Select notes, write audit_in_NN.json, and describe what was done."""
    by_note = load_tasks_by_note()
    notes = load_notes(by_note)
    reasons = {}
    if scope == "monthly":
        if since_ts is None:
            raise ValueError("monthly scope needs a since timestamp")
        month = month or datetime.now().strftime("%Y-%m")
        selected, reasons = select_monthly(notes, since_ts, sample_pct, month, ledger, carry_ids,
                                           recheck_days, recheck_cap, now_ts)
    elif scope == "full":
        selected = notes
    elif scope == "retry":
        wanted = set(carry_ids)
        selected = [n for n in notes if n["id"] in wanted]
    else:
        raise ValueError(f"unknown scope {scope!r}")

    batches = [selected[i : i + batch] for i in range(0, len(selected), batch)]
    if write:
        for old in list(HERE.glob("audit_in_*.json")) + list(HERE.glob("audit_out_*.json")):
            old.unlink()
        for number, chunk in enumerate(batches, start=1):
            clean = [{k: v for k, v in n.items() if k != "ts"} for n in chunk]
            (HERE / f"audit_in_{number:02d}.json").write_text(
                json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8"
            )
    return {
        "scope": scope,
        "notes_with_tasks": len(notes),
        "all_note_ids": [n["id"] for n in notes],
        "selected": len(selected),
        "reasons": reasons,
        "batches": len(batches),
        "batch_size": batch,
        "batch_note_ids": [[n["id"] for n in chunk] for chunk in batches],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("batch_size", nargs="?", type=int, default=DEFAULT_BATCH)
    ap.add_argument("--scope", choices=("full", "monthly"), default="full")
    ap.add_argument("--since", help="monthly scope: edited after this ISO date or datetime")
    ap.add_argument("--sample-pct", type=float, default=DEFAULT_SAMPLE_PCT)
    ap.add_argument("--recheck-days", type=int, default=DEFAULT_RECHECK_DAYS)
    ap.add_argument("--recheck-cap", type=int, default=DEFAULT_RECHECK_CAP)
    ap.add_argument("--month", help="seed for the random sample, default the current YYYY-MM")
    ap.add_argument("--dry-run", action="store_true", help="count only, write nothing")
    args = ap.parse_args(argv)

    since_ts = None
    if args.since:
        since_ts = datetime.fromisoformat(args.since).timestamp()
    info = build(args.scope, args.batch_size, since_ts, args.sample_pct, args.month,
                 write=not args.dry_run, recheck_days=args.recheck_days, recheck_cap=args.recheck_cap)
    line = (
        f"{info['notes_with_tasks']} notes have tasks; scope {info['scope']} selects {info['selected']} "
        f"in {info['batches']} batches of up to {info['batch_size']}"
    )
    if info["reasons"]:
        line += " (" + ", ".join(f"{k} {v}" for k, v in info["reasons"].items()) + ")"
    print(line + (" [dry run, nothing written]" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
