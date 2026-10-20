"""Classify tasks with Jev (Typesafe System One) instead of an LLM agent.

Jev is a hosted, cheap classifier: POST https://api.typesafe.ai/v1/systemone with
model jev-latest, body {"state": ..., "questions": ...}. It returns a chosen label,
its probability distribution and a confidence. Needs TYPESAFE_API_KEY.

Modes:
    python jev_categorize.py check [N]   # compare Jev against existing labels
    python jev_categorize.py apply       # label tasks that have no category yet

Every answer is cached in jev_cache.json, so a rerun costs nothing.
"""
import json
import os
import pathlib
import random
import sys
import time
import urllib.error
import urllib.request

import categories

HERE = pathlib.Path(__file__).parent
ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
CACHE = HERE / "jev_cache.json"
BATCH = 20
MIN_PROB = 0.6

CRITERIA = {
    "incoming-promised": "money already owed or agreed, such as an invoice to send or a payment to collect",
    "client-job": "work for a named client that is already agreed",
    "money-machine": "an existing income asset to finish, such as a product, a sales pipeline or a working tool",
    "blocks-work": "something that stops all other work, such as a broken laptop or a lost account",
    "health-safety": "a health or safety matter",
    "legal-account": "a legal, tax or account deadline",
    "outgoing-due": "a bill, fee, tax or subscription to pay",
    "incoming-window": "a known chance to earn, not yet claimed, with a closing window",
    "promise": "keeping your word to a person",
    "to-save": "money to set aside, move or budget",
    "new-opportunity": "a brand new way to earn, still an idea, no live window",
    "none": "no category truly applies",
}

INSTRUCTION = (
    "Which income category fits task `batch.{i}.title`? The extra note is `batch.{i}.detail`. "
    "Most tasks have no category, so choose none unless the task clearly matches one of the "
    "listed categories."
)


def api_key() -> str:
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if key:
        return key
    path = HERE / ".jev_key"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def load_cache() -> dict:
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict) -> None:
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def call(state: dict, questions: dict, key: str, timeout: float = 120.0) -> dict:
    payload = json.dumps({"state": state, "model": MODEL, "questions": questions}).encode()
    req = urllib.request.Request(
        ENDPOINT,
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    last = "unknown error"
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}"
            if e.code not in (429, 500, 502, 503, 504):
                break
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        time.sleep(3)
    return {"error": last}


def questions_for(chunk: list) -> dict:
    return {
        f"t{i:03d}": {
            "type": "choice",
            "instructions": INSTRUCTION.format(i=i),
            "criteria": CRITERIA,
        }
        for i in range(len(chunk))
    }


def classify(tasks: list, key: str, cache: dict) -> dict:
    out = {}
    todo = [t for t in tasks if t["id"] not in cache]
    for t in tasks:
        if t["id"] in cache:
            out[t["id"]] = cache[t["id"]]
    for i in range(0, len(todo), BATCH):
        chunk = todo[i : i + BATCH]
        state = {
            "batch": [
                {"title": t["title"], "detail": (t.get("why") or "")[:200]} for t in chunk
            ]
        }
        ans = call(state, questions_for(chunk), key)
        if "error" in ans:
            print(f"batch {i // BATCH + 1}: {ans['error']}")
            continue
        for j, t in enumerate(chunk):
            a = (ans.get("answers") or {}).get(f"t{j:03d}") or {}
            label = a.get("choice")
            probs = a.get("probabilities") or {}
            if label not in CRITERIA or probs.get(label, 0) < MIN_PROB:
                label = "none"
            out[t["id"]] = {"category": label, "confidence": a.get("confidence")}
            cache[t["id"]] = out[t["id"]]
        save_cache(cache)
        print(f"batch {i // BATCH + 1}: {len(chunk)} tasks classified", flush=True)
    return out


def main() -> int:
    key = api_key()
    if not key:
        print("no TYPESAFE_API_KEY and no .jev_key file; nothing done")
        return 1
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    data = json.loads((HERE / "tasks.json").read_text(encoding="utf-8"))
    tasks = data["tasks"]
    cache = load_cache()

    if mode == "check":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        random.seed(7)
        labelled = [t for t in tasks if (t.get("category") or "none") != "none"]
        none = [t for t in tasks if (t.get("category") or "none") == "none"]
        sample = random.sample(labelled, min(n, len(labelled))) + random.sample(none, min(n // 2, len(none)))
        got = classify(sample, key, cache)
        agree = 0
        for t in sample:
            mine = (t.get("category") or "none")
            theirs = got[t["id"]]["category"]
            ok = mine == theirs
            agree += ok
            if not ok:
                print(f"{'ok ' if ok else 'DIFF'} {t['id']} agent={mine} jev={theirs} conf={got[t['id']]['confidence']} | {t['title'][:70]}")
        print(f"agreement {agree}/{len(sample)}")
        (HERE / "jev-check.json").write_text(
            json.dumps(
                [
                    {
                        "task_id": t["id"],
                        "title": t["title"],
                        "agent": t.get("category") or "none",
                        "jev": got[t["id"]]["category"],
                        "confidence": got[t["id"]]["confidence"],
                    }
                    for t in sample
                ],
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        return 0

    if mode == "apply":
        settings = json.loads((HERE / "settings.json").read_text(encoding="utf-8")) if (HERE / "settings.json").exists() else {}
        use_deadlines = bool(settings.get("use_deadlines", False))
        fresh = [t for t in tasks if not (t.get("category") or "").strip()]
        if not fresh:
            print("every task already has a category")
            return 0
        got = classify(fresh, key, cache)
        changed = []
        for t in fresh:
            label = got[t["id"]]["category"]
            t["category"] = label
            categories.apply(t, use_deadlines=use_deadlines)
            changed.append((t["id"], label))
        (HERE / "tasks.json").write_text(
            json.dumps({"version": 1, "tasks": tasks}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"labelled {len(changed)} tasks")
        for tid, label in changed:
            print(f"  {tid} {label}")
        return 0

    print(f"unknown mode {mode!r}; use check or apply")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())