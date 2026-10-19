"""One-off: drop hand-extracted tasks that the agent re-extracted with other words.

Usage: python dedupe_hand.py
"""
import json
import pathlib
import re

HERE = pathlib.Path(__file__).parent
TASKS = HERE / "tasks.json"
STOP = set("the and for with that this into from all any are was were will add new get got use using my me i to do".split())

HAND_DUPES = [
    "t054",  # b1908 Send the cold email
    "t055",  # b1906 Get google plan to work globally
    "t056",  # b1911 Install freellm api globally
    "t057",  # b1915 Integrate freellmapi into IDEs
    "t058",  # b1912 Crawl twitter for advice on applying to YC
    "t059",  # b1913 Go through 61+ bestmodelpicker issues
    "t065",  # b1910 East Africa marketing plans
    "t066",  # b1916 Fix hermes agent
    "t069",  # b1907 github deploy commit state scrapers
    "t070",  # b1909 Refactor and beef up search systems
]


def words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3 and w not in STOP}


def main() -> int:
    data = json.loads(TASKS.read_text(encoding="utf-8"))
    tasks = data["tasks"]
    drop = set(HAND_DUPES)
    kept = [t for t in tasks if t["id"] not in drop]
    print(f"dropped {len(tasks) - len(kept)}, kept {len(kept)}")
    TASKS.write_text(json.dumps({"version": 1, "tasks": kept}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())