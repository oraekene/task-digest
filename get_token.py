"""Extract the Simplenote access token from the desktop app's Local Storage.

Run with the task-digest venv python (needs ccl_chromium_reader):
    .venv\\Scripts\\python.exe get_token.py

Writes .token next to this script and prints the account name.
"""
import json
import pathlib
import sys

from ccl_chromium_reader import ccl_chromium_localstorage

APP = pathlib.Path.home() / (
    r"AppData\Local\Packages\22490Automattic.Simplenote_9h07f78gwnchp\LocalCache\Roaming\Simplenote"
)
LS_DIR = APP / "Local Storage" / "leveldb"
HERE = pathlib.Path(__file__).parent


def find_user_record():
    with ccl_chromium_localstorage.LocalStoreDb(LS_DIR) as local_storage:
        for storage_key in local_storage.iter_storage_keys():
            for record in local_storage.iter_records_for_storage_key(storage_key):
                value = record.value
                if isinstance(value, str) and '"accessToken"' in value:
                    return json.loads(value)
    return None


def main() -> int:
    user = find_user_record()
    if not user or not user.get("accessToken"):
        print("no accessToken found in app Local Storage", file=sys.stderr)
        return 1
    (HERE / ".token").write_text(user["accessToken"], encoding="utf-8")
    print(f"token written, account: {user.get('username')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
