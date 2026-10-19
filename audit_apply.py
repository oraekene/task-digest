"""Merge audit_out_*.json files into audit-report.json for the console advisory tab.

Validates every row: known task id, allowed status, status differs from current,
and no drift (the "was" recorded by the auditor must still match tasks.json).
Read-only on tasks.json.

Usage: python audit_apply.py
"""
import json
import pathlib

HERE = pathlib.Path(__file__).parent
ALLOWED = {"open", "started", "done"}


def main() -> int:
    tasks = {
        t["id"]: t
        for t in json.loads((HERE / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    }
    rows = []
    seen = set()
    skipped = 0
    for path in sorted(HERE.glob("audit_out_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            print(f"{path.name}: not valid JSON ({exc}); skipped")
            skipped += 1
            continue
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
    rows.sort(key=lambda r: r["task_id"])
    (HERE / "audit-report.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"{len(rows)} valid rows, {skipped} skipped, from {len(list(HERE.glob('audit_out_*.json')))} batches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())