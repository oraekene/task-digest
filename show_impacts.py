import json
import pathlib

d = json.loads(pathlib.Path("tasks.json").read_text(encoding="utf-8"))
want = {"t001", "t002", "t003", "t049", "t053", "b1319", "b1655", "t045", "t042"}
for t in d["tasks"]:
    if t["id"] in want:
        print(t["id"], "T" + str(t["tier"]), "imp=" + str(t["impact"]),
              t["first_seen"], "|", t["title"][:58])