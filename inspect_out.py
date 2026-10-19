"""Peek at one or more batches/out_NN.json files: counts, tiers, samples."""
import collections
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
FIELDS = {"title", "quote", "source_note", "source_date", "tier", "impact", "why"}


def show(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    bad = [i for i, t in enumerate(data) if set(t) != FIELDS]
    print(f"{path.name}: {len(data)} tasks, bad-shape: {len(bad)}")
    print("  tiers:", dict(collections.Counter(t["tier"] for t in data)))
    print("  impacts:", dict(collections.Counter(t["impact"] for t in data)))
    return data


if __name__ == "__main__":
    names = sys.argv[1:] or ["batches/out_01.json"]
    for name in names:
        data = show(HERE / name)
    if len(names) == 1:
        for t in data[:10]:
            print(f"[{t['tier']}|{t['impact']}] {t['title'][:100]}")
