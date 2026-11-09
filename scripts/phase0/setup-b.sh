#!/usr/bin/env bash
# Throwaway fixtures for the T1-T4 primitive tests. Run in a plain terminal, NOT inside Claude.
set -euo pipefail
D=/mnt/d/claude-guard-test-b
H="$HOME"
[ ! -e "$D" ] || { echo "$D already exists; run cleanup-b.sh first" >&2; exit 1; }
mkdir -p "$H/cgb-cwd" "$D/outside" "$D/asked" "$D/free" "$D/hook-free" "$D/hook-deny" "$D/hook-ask" "$D/hook-crash"

# T1: a "repo" that is a git repo
mkdir -p "$D/repo"
echo orig > "$D/repo/note.txt"
git -C "$D/repo" init -q
git -C "$D/repo" config user.name cgb
git -C "$D/repo" config user.email cgb@example.invalid
git -C "$D/repo" add -A
git -C "$D/repo" commit -q -m initial
git -C "$D/repo" tag initial
echo orig > "$D/hook-free/existing.txt"

# T4: a harmless git repo for the multi-word command test
mkdir -p "$H/cgb-gitrepo"
git -C "$H/cgb-gitrepo" init -q
git -C "$H/cgb-gitrepo" config user.name cgb
git -C "$H/cgb-gitrepo" config user.email cgb@example.invalid
echo x > "$H/cgb-gitrepo/f.txt"
git -C "$H/cgb-gitrepo" add -A
git -C "$H/cgb-gitrepo" commit -q -m initial

# T3: hook script (stdlib only). Behaviour is chosen by the file path being written.
cat > "$H/cgb-hook.py" <<'PY'
#!/usr/bin/env python3
import datetime, json, os, sys

data = json.load(sys.stdin)
path = (data.get("tool_input") or {}).get("file_path", "") or ""

def log(decision):
    with open(os.path.expanduser("~/cgb-hook.log"), "a") as fh:
        fh.write(json.dumps({"t": datetime.datetime.now().isoformat(timespec="seconds"),
                             "tool": data.get("tool_name"), "path": path,
                             "permission_mode": data.get("permission_mode"),
                             "decision": decision}) + "\n")

if "/hook-crash/" in path:
    log("crash")
    raise RuntimeError("deliberate crash")
if "/hook-deny/" in path:
    decision = "deny"
elif "/hook-ask/" in path:
    decision = "ask"
else:
    log("none")
    sys.exit(0)
log(decision)
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                         "permissionDecision": decision,
                                         "permissionDecisionReason": "cgb hook says " + decision}}))
PY
chmod +x "$H/cgb-hook.py"

# ---- settings files ----
# T1a/T1b: Edit allow on repo (which merges into allowWrite) + denyWrite on repo + allowWrite on repo/.git
for variant in a b; do
  if [ "$variant" = a ]; then DENY="$D/repo"; else DENY="$D/repo/**"; fi
  cat > "$H/cgb-T1$variant.json" <<JSON
{"sandbox":{"enabled":true,"allowUnsandboxedCommands":false,
  "filesystem":{"allowWrite":["$D/repo/.git"],"denyWrite":["$DENY"]}},
 "permissions":{"allow":["Edit(/$D/repo/**)"]}}
JSON
done
# T2: Edit ask on asked/, Edit allow on free/
cat > "$H/cgb-T2.json" <<JSON
{"sandbox":{"enabled":true,"allowUnsandboxedCommands":false,
  "filesystem":{"allowWrite":["$D/asked","$D/free"]}},
 "permissions":{"ask":["Edit(/$D/asked/**)"],"allow":["Edit(/$D/free/**)"]}}
JSON
# T3: PreToolUse hook on the file tools; T3b is the same without onFailure
for variant in "" b; do
  ONFAIL=',"onFailure":"block"'; [ -z "$variant" ] || ONFAIL=""
  cat > "$H/cgb-T3$variant.json" <<JSON
{"hooks":{"PreToolUse":[{"matcher":"Write|Edit|MultiEdit",
  "hooks":[{"type":"command","command":"/usr/bin/python3","args":["$H/cgb-hook.py"]$ONFAIL}]}]}}
JSON
done
# T4: deny rules only; see which command spellings they catch
cat > "$H/cgb-T4.json" <<JSON
{"permissions":{"deny":["Bash(uname *)","Bash(hostname *)","Bash(whoami)","Bash(git stash list *)"]}}
JSON
rm -f "$H/cgb-hook.log"
echo "Created $D, ~/cgb-cwd, ~/cgb-gitrepo, ~/cgb-hook.py and ~/cgb-{T1a,T1b,T2,T3,T3b,T4}.json"
