"""Demote stale open tasks one tier.

A task first seen more than 45 days ago and still open has not been acted on
for months, so it cannot be today's priority. Demote it one tier so the digest
shows what is actually live.
"""
import json
import pathlib
from datetime import date, timedelta

HERE = pathlib.Path(__file__).parent
TASKS = HERE / "tasks.json"
DAYS = 45

cutoff = date.today() - timedelta(days=DAYS)
data = json.loads(TASKS.read_text(encoding="utf-8"))
n = 0
for t in data["tasks"]:
    if t.get("status") != "open" or not t.get("first_seen"):
        continue
    if date.fromisoformat(t["first_seen"][:10]) < cutoff and "demoted: stale" not in (t.get("why") or ""):
        t["tier"] = min(int(t.get("tier") or 2) + 1, 4)
        t["why"] = (t.get("why", "") + " | demoted: stale").strip(" |")
        n += 1

TASKS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"demoted {n} stale tasks older than {cutoff}")