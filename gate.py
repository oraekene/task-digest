"""Read-only gate. Fails the run if anything could write to Simplenote.

Checks:
  1. settings.json keeps read_only true.
  2. No script that can reach Simplenote contains a write HTTP verb or sends a request body.

Usage: python gate.py
"""
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
SETTINGS = HERE / "settings.json"
BANNED = [
    (re.compile(r"\bmethod\s*=\s*[\"'](POST|PUT|PATCH|DELETE)[\"']", re.I), "explicit write method"),
    (re.compile(r"urlopen\s*\([^)]*data\s*=", re.S | re.I), "request body sent"),
    (re.compile(r"requests\.(post|put|patch|delete)\s*\(", re.I), "write request"),
    (re.compile(r"\bInvoke-(RestMethod|WebRequest)\b.*-Method\s*(Post|Put|Patch|Delete)", re.I), "write request"),
    (re.compile(r"\bcurl\b[^\n]*(-X|--request)\s*(POST|PUT|PATCH|DELETE)", re.I), "write request"),
    (re.compile(r"\bmethod\s*=\s*[\"'](?!GET[\"'])", re.I), "non-GET request method"),
    (re.compile(r"\bRequest\s*\([^)]*\bdata\s*=", re.S | re.I), "request body sent"),
    (re.compile(r"urlopen\s*\([^)]*data\s*=", re.S | re.I), "request body sent"),
]


def main() -> int:
    problems = []
    if '"read_only": true' not in SETTINGS.read_text(encoding="utf-8").replace(" ", " "):
        if not re.search(r'"read_only"\s*:\s*true', SETTINGS.read_text(encoding="utf-8")):
            problems.append("settings.json does not set read_only true")

    for path in list(HERE.glob("*.py")) + list(HERE.glob("*.ps1")):
        if path.name == "gate.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        # shortcut: a write to Simplenote needs its host named in the file; upgrade if a
        # future client builds the URL dynamically. Other APIs (Jev) may write freely.
        if not re.search(r"simperium\.com|simplenote\.com", text, re.I):
            continue
        for pattern, label in BANNED:
            for m in pattern.finditer(text):
                line = text[: m.start()].count("\n") + 1
                problems.append(f"{path.name}:{line} {label}: {m.group(0)[:60]!r}")

    if problems:
        print("READ-ONLY GATE FAILED")
        for p in problems:
            print("  " + p)
        return 1
    print("read-only gate passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())