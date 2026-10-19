"""Split open tasks into batches for classification by a language model.

Usage: python categorize_dump.py [batch_size]
Writes cat_in_01.json ... and prints the count.
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
TASKS = HERE / "tasks.json"
BATCH = int(sys.argv[1]) if len(sys.argv) > 1 else 250


def main() -> int:
    data = json.loads(TASKS.read_text(encoding="utf-8"))
    rows = [
        {"id": t["id"], "title": t["title"], "why": (t.get("why") or "")[:100]}
        for t in data["tasks"]
        if t.get("status") in ("open", "started")
    ]
    for old in HERE.glob("cat_in_*.json"):
        old.unlink()
    for i in range(0, len(rows), BATCH):
        path = HERE / f"cat_in_{i // BATCH + 1:02d}.json"
        path.write_text(
            json.dumps(rows[i : i + BATCH], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    print(f"{len(rows)} tasks in {-(-len(rows) // BATCH)} batches of {BATCH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())