"""Merge audit_out_*.json files into audit-report.json for the console advisory tab.

Validates every row: known task id, allowed status, status differs from current,
and no drift (the "was" recorded by the auditor must still match tasks.json).
Read-only on tasks.json.

By default the new findings are merged into the existing report:
  - a task that was audited in this run gets only its fresh result, so a
    finding the auditor no longer repeats is dropped;
  - a task that was not audited in this run keeps its earlier row, as long as
    the row is still true (the task exists, still has the status the row says
    it has, and the suggestion still differs from it).
A batch counts as audited only when its audit_out_NN.json exists and parses, so
a failed batch never erases earlier findings.

Usage: python audit_apply.py [--replace]
    --replace  rebuild the report from this run alone (old behaviour)
"""
import argparse
import json
import pathlib

HERE = pathlib.Path(__file__).parent
ALLOWED = {"open", "started", "done"}


def read_out(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, list) else None


def audited_task_ids(good_batches) -> set:
    ids = set()
    for number in good_batches:
        path = HERE / f"audit_in_{number}.json"
        if not path.exists():
            continue
        try:
            notes = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        for note in notes:
            ids.update(t["id"] for t in note.get("tasks", []))
    return ids


def apply(replace=False) -> dict:
    tasks = {
        t["id"]: t
        for t in json.loads((HERE / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    }
    rows, seen, skipped = [], set(), 0
    good_batches = []
    outs = sorted(HERE.glob("audit_out_*.json"))
    for path in outs:
        data = read_out(path)
        if data is None:
            print(f"{path.name}: not a valid JSON array; skipped")
            skipped += 1
            continue
        good_batches.append(path.stem.rsplit("_", 1)[1])
        for row in data:
            task = tasks.get(row.get("task_id"))
            if (
                task is None
                or row.get("status") not in ALLOWED
                or row.get("status") == task.get("status")
                or row.get("was") != task.get("status")
                or not (row.get("evidence") or "").strip()
                or row["task_id"] in seen
            ):
                skipped += 1
                continue
            seen.add(row["task_id"])
            rows.append(
                {
                    "task_id": row["task_id"],
                    "title": task["title"],
                    "current": task.get("status"),
                    "suggested": row["status"],
                    "evidence": row["evidence"].strip()[:200],
                    "confidence": row.get("confidence") or "medium",
                    "source": "audit",
                }
            )

    kept = 0
    report_path = HERE / "audit-report.json"
    if not replace and report_path.exists():
        try:
            earlier = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            earlier = []
        audited = audited_task_ids(good_batches)
        for row in earlier:
            task = tasks.get(row.get("task_id"))
            if (
                task is None
                or row["task_id"] in audited
                or row["task_id"] in seen
                or row.get("current") != task.get("status")
                or row.get("suggested") == task.get("status")
            ):
                continue
            rows.append(row)
            kept += 1

    rows.sort(key=lambda r: r["task_id"])
    report_path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return {
        "rows": len(rows),
        "new": len(rows) - kept,
        "kept": kept,
        "skipped": skipped,
        "batches": len(outs),
        "good_batches": good_batches,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replace", action="store_true")
    args = ap.parse_args(argv)
    r = apply(replace=args.replace)
    print(
        f"{r['rows']} rows ({r['new']} new, {r['kept']} kept from earlier), "
        f"{r['skipped']} skipped, from {r['batches']} batches"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
