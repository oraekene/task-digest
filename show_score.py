import collections
import json
import pathlib
from datetime import date

d = json.loads(pathlib.Path("tasks.json").read_text(encoding="utf-8"))
ts = d["tasks"]
today = date.today()
print("total", len(ts))
print("with deadline:", sum(1 for t in ts if t.get("deadline")))
print("tiers", dict(collections.Counter(t.get("tier") for t in ts)))
print("impacts", dict(collections.Counter(t.get("impact") for t in ts)))
print("demoted flag:", sum(1 for t in ts if "demoted" in (t.get("why") or "")))
print()


def show(t):
    tier = int(t.get("tier") or 2)
    w = 100 / 2 ** (tier - 1)
    age = (today - date.fromisoformat(t["first_seen"][:10])).days
    u = min(age * 0.02, 0.5)
    i = float(t.get("impact") or 0)
    print(
        f"{t['id']} T{tier} w={w} imp={i} age={age}d urg={u:.2f} "
        f"score={w * (1 + u + i):.1f} | {t['title'][:62]}"
    )


wanted = {"t054", "t055", "b0001", "b0002", "t001", "t002"}
for t in ts:
    if t["id"] in wanted:
        show(t)

print()
print("first 5 ranked by the real scorer:")
import tracker  # noqa: E402

ranked = tracker.ranked_open(d)
for t in ranked[:5]:
    show(t)
print()
print("a demoted example:")
for t in ts:
    if "demoted" in (t.get("why") or ""):
        show(t)
        break