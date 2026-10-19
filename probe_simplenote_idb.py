import json
from ccl_chromium_reader import ccl_chromium_indexeddb

LEVELDB = r"C:\Users\rotim\AppData\Local\Packages\22490Automattic.Simplenote_9h07f78gwnchp\LocalCache\Roaming\Simplenote\IndexedDB\file__0.indexeddb.leveldb"
BLOBS = r"C:\Users\rotim\AppData\Local\Packages\22490Automattic.Simplenote_9h07f78gwnchp\LocalCache\Roaming\Simplenote\IndexedDB\file__0.indexeddb.blob"

wrapper = ccl_chromium_indexeddb.WrappedIndexDB(LEVELDB, BLOBS)
db = wrapper["simplenote_v2", "file__0@1"]

store = db["revisions"]
all_ids = {}
live_ids = {}
max_mod = 0
newest = None

for rec in store.iterate_records(live_only=False):
    key = rec.key.value
    val = rec.value
    if isinstance(val, list) and val and isinstance(val[0], list):
        last_version, note = val[-1]
    else:
        continue
    all_ids[key] = rec.is_live
    mod = note.get("modificationDate", 0)
    if rec.is_live:
        live_ids[key] = note
    if mod > max_mod:
        max_mod = mod
        newest = note

import datetime
print("total revision records:", len(all_ids))
print("live records:", len(live_ids))
print("newest live modificationDate:", datetime.datetime.fromtimestamp(max_mod), "ms timestamp:", max_mod)
print("newest content head:", repr(newest.get("content", "")[:120]))
print("known id present:", "ba6e68cf1d8440059deca1451d99800b" in live_ids)
print("live ids sample:", list(live_ids)[:5])

# notes modified on/after Oct 7 2026 (after export snapshot)
cut = datetime.datetime(2026, 10, 7).timestamp()
recent = [(k, v.get("modificationDate", 0)) for k, v in live_ids.items() if v.get("modificationDate", 0) >= cut]
print("live notes modified after 2026-10-07:", len(recent))
for k, m in sorted(recent, key=lambda x: x[1], reverse=True)[:10]:
    print("  ", datetime.datetime.fromtimestamp(m), k, repr(live_ids[k].get("content", "")[:80]))

state = db["state"]
errs = 0
ok = 0
for rec in state.iterate_records(live_only=True, bad_deserializer_data_handler=lambda k, v: None):
    ok += 1
print("state live records decoded:", ok)
