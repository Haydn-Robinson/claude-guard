#!/usr/bin/env bash
# Session C helper: change the test project's real settings.local.json while Claude is running.
# Usage: scripts/phase2/reload.sh rule|hook|denywrite
set -euo pipefail
R="$HOME/cgc"
F="$R/reload-proj/.claude/settings.local.json"
[ -f "$F" ] || { echo "$F missing; run setup.sh first" >&2; exit 1; }
python3 - "$1" "$F" "$R" <<'PY'
import json, sys
what, path, root = sys.argv[1:]
data = json.load(open(path))
if what == "rule":
    data.setdefault("permissions", {}).setdefault("deny", []).append(f"Edit(/{root}/outside/**)")
elif what == "hook":
    data["hooks"] = json.load(open(f"{root}/hook-entry.json"))
elif what == "denywrite":
    data["sandbox"].setdefault("filesystem", {}).setdefault("denyWrite", []).append(f"{root}/reload-proj/locked")
else:
    sys.exit("usage: reload.sh rule|hook|denywrite")
with open(path, "w") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
print(f"updated {path}:")
print(json.dumps(data, indent=2))
PY
