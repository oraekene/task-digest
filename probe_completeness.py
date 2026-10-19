import json, random, datetime
from ccl_chromium_reader import ccl_chromium_indexeddb

LEVELDB = r"C:\Users\rotim\AppData\Local\Packages\22490Automattic.Simplenote_9h07f78gwnchp\LocalCache\Roaming\Simplenote\IndexedDB\file__0.indexeddb.leveldb"
BLOBS = r"C:\Users\rotim\AppData\Local\Packages\22490Automattic.Simplenote_9h07f78gwnchp\LocalCache\Roaming\Simplenote\IndexedDB\file__0.indexeddb.blob"
EXPORT = r"C:\Users\rotim\Videos\personal_context\my notes (simplenotes, keep, notepad++, .txt files, .md files, and other files)\simplenote-07-10-26\source\notes.json".replace("simplenote-07-10-26", "simplenote-07-10-26")

wrapper = ccl_chromium_indexeddb.WrappedIndexDB(LEVELDB, BLOBS)
db = wrapper["simplenote_v2", "file__0@1"]
live = {}
for rec in db["revisions"].iterate_records(live_only=True):
    val = rec.value
    if isinstance(val, list) and val and isinstance(val[0], list):
        live[rec.key.value] = val[-1][1]

print("local live notes:", len(live))
creates = [n.get("creationDate", 0) for n in live.values()]
print("local creationDate range:", datetime.datetime.fromtimestamp(min(creates)), "to", datetime.datetime.fromtimestamp(max(creates)))
years = {}
for c in creates:
    y = datetime.datetime.fromtimestamp(c).year
    years[y] = years.get(y, 0) + 1
print("local notes per creation year:", dict(sorted(years.items())))

with open(r"C:\Users\rotim\Videos\personal_context\my notes (simplenotes, keep, notepad++, .txt files, .md files, and other files)\simplenotes-07-10-26\source\notes.json", encoding="utf-8") as f:
    export = json.load(f)
exp_ids = [n["id"] for n in export["activeNotes"]]
print("export ids:", len(exp_ids), "unique:", len(set(exp_ids)))
eyears = {}
for n in export["activeNotes"]:
    y = n["creationDate"][:4]
    eyears[y] = eyears.get(y, 0) + 1
print("export notes per creation year:", dict(sorted(eyears.items())))
contents = [n["content"] for n in export["activeNotes"]]
print("export unique contents:", len(set(contents)))
random.seed(42)
sample = random.sample(exp_ids, 40)
present = [i for i in sample if i in live]
print("sampled 40 export ids, present locally:", len(present))
missing = [i for i in sample if i not in live]
exp_by_id = {n["id"]: n for n in export["activeNotes"]}
for i in missing[:8]:
    n = exp_by_id[i]
    print("  missing:", i, "created", n["creationDate"][:10], repr(n["content"][:60]))
