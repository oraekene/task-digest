"""Apply categories produced by the language model to tasks.json.

Reads cat_out_*.json, sets the category, then raises tier and impact to the
category floor. Never lowers priority. Never touches a stale-demoted task.

Usage: python categorize_apply.py
"""
import json
import pathlib

import categories

HERE = pathlib.Path(__file__).parent
TASKS = HERE / "tasks.json"
VALID = set(categories.CATEGORY_TIERS)


def main() -> int:
    labels = {}
    for path in sorted(HERE.glob("cat_out_*.json")):
        for row in json.loads(path.read_text(encoding="utf-8")):
            label = row.get("category")
            if label in VALID:
                labels[row["id"]] = label
    if not labels:
        print("no cat_out_*.json files found")
        return 1

    data = json.loads(TASKS.read_text(encoding="utf-8"))
    changed = promoted = 0
    counts = {}
    for task in data["tasks"]:
        label = labels.get(task["id"])
        if not label:
            continue
        task["category"] = label
        counts[label] = counts.get(label, 0) + 1
        if categories.apply(task, use_deadlines=False):
            changed += 1
        if task.get("tier") == 1 and label != "none":
            promoted += 1

    TASKS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"labelled {len(labels)} tasks, {changed} raised in tier or impact")
    for label, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {label:20s} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())