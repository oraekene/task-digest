"""Run the state audit end to end, with no manual step.

    dump the notes in scope -> one agent per batch -> merge the findings -> record the run

Usage:
    python audit_run.py                  run only when due (30 days since the last good run)
    python audit_run.py --force          run now; the scope is picked automatically
    python audit_run.py --scope full     run now over every note that has tasks
    python audit_run.py --scope monthly  run now over the monthly scope
    python audit_run.py --scope retry    run now over the notes a failed batch left behind
    python audit_run.py --dry-run        show what would run, change nothing

Scope "auto" is a full pass the first time, when no earlier audit can be found,
and the monthly scope after that (see audit_dump.py).

The audit is advisory. It writes audit-report.json and audit-state.json and
never edits tasks.json or touches Simplenote.

Optional keys in settings.json, all with defaults:
    audit_interval_days   30
    audit_sample_pct      10
    audit_recheck_days    90       none and low-confidence notes come back after this many days
    audit_recheck_cap     150      at most this many of them in one run (0 = no limit)
    audit_batch           80       notes per agent batch
    audit_batch_minutes   60       time limit for one batch
    audit_agent           ["opencode","run","--auto","--pure","--dir","{here}","{message}","-f","{prompt}"]
        The command that runs one batch. Placeholders: {here} {message} {prompt} {nn}
        {in_file} {out_file}. The agent must write {out_file}.

State: audit-state.json, kept in git so the schedule survives a new laptop.
Log: logs/audit.log.
"""
import argparse
import json
import math
import os
import pathlib
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime

import audit_apply
import audit_dump

HERE = pathlib.Path(__file__).parent
STATE = HERE / "audit-state.json"
LOGDIR = HERE / "logs"
LOG = LOGDIR / "audit.log"
LOCK = LOGDIR / "audit.lock"
DAILY_LOCK = LOGDIR / "run.lock"
PROMPT = HERE / "audit_prompt.md"

DEFAULTS = {
    "audit_interval_days": 30,
    "audit_sample_pct": audit_dump.DEFAULT_SAMPLE_PCT,
    "audit_recheck_days": audit_dump.DEFAULT_RECHECK_DAYS,
    "audit_recheck_cap": audit_dump.DEFAULT_RECHECK_CAP,
    "audit_batch": audit_dump.DEFAULT_BATCH,
    "audit_batch_minutes": 60,
    "audit_agent": [
        "opencode", "run", "--auto", "--pure", "--dir", "{here}", "{message}", "-f", "{prompt}",
    ],
}
MESSAGE = (
    "Audit batch {nn}. Read audit_in_{nn}.json and write audit_out_{nn}.json "
    "in the same folder. Follow the attached prompt."
)
LOCK_STALE_SECONDS = 15 * 60
DAILY_LOCK_SECONDS = 30 * 60


def log(message: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}"
    print(line, flush=True)
    LOGDIR.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def settings() -> dict:
    cfg = dict(DEFAULTS)
    for key, value in load_json(HERE / "settings.json", {}).items():
        if key in DEFAULTS:
            cfg[key] = value
    return cfg


def write_state(state: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def git_commit_time(path: pathlib.Path):
    """Commit time of the last commit that touched path, or None."""
    try:
        out = subprocess.run(
            ["git", "-C", str(HERE), "log", "-1", "--format=%ct", "--", path.name],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        return float(out) if out else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def baseline_ts(state: dict):
    """When the last audit ran: the state file, else the report's age, else never."""
    if state.get("last_ts"):
        return float(state["last_ts"])
    report = HERE / "audit-report.json"
    if report.exists():
        return git_commit_time(report) or report.stat().st_mtime
    return None


def is_due(state: dict, interval_days: float, now: float):
    """Return (due, why, kind). kind is "scheduled" for a normal pass, "retry" for failed notes only."""
    today = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    if (state.get("last_attempt") or "")[:10] == today:
        return False, "already attempted today", None
    base = baseline_ts(state)
    if base is None:
        return True, "no earlier audit found, first pass", "scheduled"
    age_days = (now - base) / 86400
    if age_days >= interval_days:
        return True, f"last audit {age_days:.0f} days ago", "scheduled"
    if state.get("carry_over"):
        return True, f"{len(state['carry_over'])} notes to retry from a failed batch", "retry"
    return False, f"last audit {age_days:.1f} days ago, next in {math.ceil(interval_days - age_days)} days", None


def lock_is_fresh(path: pathlib.Path, seconds: float) -> bool:
    try:
        return time.time() - path.stat().st_mtime < seconds
    except OSError:
        return False


class Heartbeat:
    """Keeps the lock file fresh while a long agent batch runs."""

    def __init__(self):
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._beat, daemon=True)

    def _beat(self):
        while not self.stop.wait(60):
            try:
                os.utime(LOCK, None)
            except OSError:
                pass

    def __enter__(self):
        LOGDIR.mkdir(exist_ok=True)
        LOCK.write_text(datetime.now().isoformat(), encoding="utf-8")
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        try:
            LOCK.unlink()
        except OSError:
            pass


def agent_command(cfg: dict, number: int) -> list:
    nn = f"{number:02d}"
    fields = {
        "here": str(HERE),
        "nn": nn,
        "in_file": f"audit_in_{nn}.json",
        "out_file": f"audit_out_{nn}.json",
        "prompt": str(PROMPT),
    }
    fields["message"] = MESSAGE.format(**fields)
    args = [str(a).format(**fields) for a in cfg["audit_agent"]]
    resolved = shutil.which(args[0])
    if resolved:
        args[0] = resolved
    return args


def run_batch(cfg: dict, number: int) -> bool:
    """Run the agent for one batch. True when it left a valid audit_out_NN.json."""
    out = HERE / f"audit_out_{number:02d}.json"
    for attempt in (1, 2):
        out.unlink(missing_ok=True)
        try:
            proc = subprocess.run(
                agent_command(cfg, number), cwd=HERE, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=float(cfg["audit_batch_minutes"]) * 60,
            )
            tail = (proc.stdout or "").strip().splitlines()[-1:] or [""]
            log(f"batch {number:02d} attempt {attempt}: exit {proc.returncode}; {tail[0][:200]}")
            if proc.returncode != 0 and (proc.stderr or "").strip():
                log(f"batch {number:02d} stderr: {proc.stderr.strip().splitlines()[-1][:200]}")
        except subprocess.TimeoutExpired:
            log(f"batch {number:02d} attempt {attempt}: timed out after {cfg['audit_batch_minutes']} minutes")
        except OSError as exc:
            log(f"batch {number:02d} attempt {attempt}: could not start the agent ({exc})")
            return False
        if audit_apply.read_out(out) is not None:
            return True
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="skip the due check")
    ap.add_argument("--scope", choices=("auto", "full", "monthly", "retry"), default="auto")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--agent-json", help="override the agent command, a JSON list")
    ap.add_argument("--now", help="pretend it is this ISO date or datetime (for testing the schedule)")
    args = ap.parse_args(argv)

    cfg = settings()
    if args.agent_json:
        cfg["audit_agent"] = json.loads(args.agent_json)
    state = load_json(STATE, {})
    now = datetime.fromisoformat(args.now).timestamp() if args.now else time.time()

    forced = args.force or args.scope != "auto" or args.dry_run
    if not forced:
        due, why, kind = is_due(state, float(cfg["audit_interval_days"]), now)
        if not due:
            print(f"audit not due: {why}")
            return 0
        reason = why
    else:
        reason, kind = "run on request", "scheduled"

    if lock_is_fresh(LOCK, LOCK_STALE_SECONDS):
        print("another audit is running; stopping")
        return 0
    if lock_is_fresh(DAILY_LOCK, DAILY_LOCK_SECONDS) and not args.dry_run:
        print("the morning run is active; the audit will try again on the next check")
        return 0

    base = baseline_ts(state)
    scope = args.scope
    if kind == "retry":
        scope = "retry"
    elif scope == "auto":
        scope = "full" if base is None else "monthly"
    if scope == "monthly" and base is None:
        scope = "full"
        reason += "; no earlier audit, so the scope is full"
    month = datetime.fromtimestamp(now).strftime("%Y-%m")
    # A note with no record of its own is taken as audited at the time of the last audit
    # before the ledger began. That time is fixed once, so it does not creep forward.
    ledger_base = state.get("ledger_base") or base or now

    def dump(write):
        return audit_dump.build(
            scope, int(cfg["audit_batch"]), ledger_base, float(cfg["audit_sample_pct"]), month,
            state.get("carry_over", []), write=write, ledger=state.get("audited", {}),
            recheck_days=int(cfg["audit_recheck_days"]), recheck_cap=int(cfg["audit_recheck_cap"]), now_ts=now,
        )

    if args.dry_run:
        info = dump(False)
        print(f"dry run ({reason}): scope {scope}, {info['selected']} of {info['notes_with_tasks']} notes "
              f"in {info['batches']} batches", info["reasons"] or "")
        return 0

    with Heartbeat():
        log(f"audit start: {reason}; scope {scope}")
        info = dump(True)
        reasons = ", ".join(f"{k} {v}" for k, v in info["reasons"].items())
        log(f"scope: {info['selected']} of {info['notes_with_tasks']} notes in {info['batches']} batches"
            + (f" ({reasons})" if reasons else ""))

        good, bad, in_a_row = [], [], 0
        for number in range(1, info["batches"] + 1):
            if in_a_row >= 2:  # the agent looks down; do not spend more calls on it
                bad.append(number)
                continue
            ok = run_batch(cfg, number)
            (good if ok else bad).append(number)
            in_a_row = 0 if ok else in_a_row + 1
        if in_a_row >= 2:
            log("two batches failed in a row, so the remaining batches were skipped and carried over")

        stamp = datetime.fromtimestamp(now).isoformat(timespec="seconds")
        new_state = dict(state)
        new_state["last_attempt"] = stamp
        if info["batches"] and not good:
            write_state(new_state)
            log(f"audit failed: all {len(bad)} batches failed; nothing merged, will retry on the next check")
            return 1

        result = audit_apply.apply(replace=False)
        retry = scope == "retry"
        carry = sorted({nid for number in bad for nid in info["batch_note_ids"][number - 1]})
        ledger = state.get("audited", {})
        stamped = {nid for number in good for nid in info["batch_note_ids"][number - 1]}
        # Keep a record only for notes that still have tasks; stamp the notes audited now.
        new_ledger = {nid: (now if nid in stamped else ledger[nid])
                      for nid in info["all_note_ids"] if nid in stamped or nid in ledger}
        # A retry mends the last pass. It does not restart the monthly clock.
        clock = {} if retry else {"last_success": stamp, "last_ts": now, "scope": scope}
        new_state.update(
            {
                **clock,
                "notes_audited": sum(len(info["batch_note_ids"][n - 1]) for n in good),
                "batches": info["batches"],
                "failed_batches": bad,
                "report_rows": result["rows"],
                "carry_over": carry,
                "audited": new_ledger,
                "ledger_base": ledger_base,
            }
        )
        write_state(new_state)
        log(
            f"audit done: {new_state['notes_audited']} notes audited, {result['rows']} advisory rows "
            f"({result['new']} new, {result['kept']} kept), {len(bad)} failed batches"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
