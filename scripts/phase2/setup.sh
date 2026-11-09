#!/usr/bin/env bash
# Throwaway fixtures for the phase 2 verification runbook (docs/phase2-verification.md).
# Run in a plain terminal, NOT inside Claude. Never touches your real Claude settings.
#
# Usage: scripts/phase2/setup.sh [WINDOWS_TEST_DIR]
#   WINDOWS_TEST_DIR (WSL only, optional): a folder that does NOT exist yet on a Windows drive, inside
#   your Windows user profile, e.g. /mnt/c/Users/<you>/cgc-win. Session F is skipped without it.
set -euo pipefail

R="$HOME/cgc"            # fixture root; ~/cgc is also used literally in "~/" rule forms
P="$R/proj"              # main test project (a git repo)
O="$R/outside"           # outside the project and every grant
S="$R/settings"          # --settings files
Q="$R/prompts"           # generated prompts (literal paths)
WIN="${1:-}"

[ ! -e "$R" ] || { echo "$R already exists; run scripts/phase2/cleanup.sh first" >&2; exit 1; }
if [ -n "$WIN" ]; then
  case "$WIN" in /mnt/[a-zA-Z]/*) ;; *) echo "WINDOWS_TEST_DIR must be under /mnt/<drive>/" >&2; exit 1;; esac
  [ ! -e "$WIN" ] || { echo "$WIN already exists; choose a new folder" >&2; exit 1; }
fi

mkdir -p "$P/sub/.claude" "$P/.claude/agents" "$P/.claude/skills" "$P/.claude/commands" \
  "$P/home-pat" "$P/secret" "$P/config" "$P/nested/.claude" "$P/pyproj" \
  "$O" "$R/b-work" "$R/rule-ask" "$R/hook/ask" "$R/hook/deny" "$R/hook/modify" \
  "$R/reload-proj/.claude" "$R/reload-proj/locked" "$R/winproj/.claude" "$R/bin" "$S" "$Q"

# ---- fixture files ----
echo orig > "$O/existing.txt"
echo orig > "$R/b-work/victim.txt"
for f in existing second; do echo orig > "$R/rule-ask/$f.txt"; echo orig > "$R/hook/ask/$f.txt"; done
echo orig > "$R/hook/modify/existing.txt"
echo orig > "$R/hook/modify/multi.txt"
cat > "$R/hook/modify/nb.ipynb" <<'JSON'
{"cells": [{"cell_type": "code", "id": "c1", "metadata": {}, "source": ["orig"], "outputs": [], "execution_count": null}],
 "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
JSON
echo "secret-value" > "$P/secret/key.txt"
echo "TOKEN=dotenv" > "$P/.env"
echo "TOKEN=dotenv-local" > "$P/.env.local"
echo "TOKEN=dotenv-production" > "$P/config/.env.production"
echo '{"x": "orig"}' > "$P/nested/.claude/settings.local.json"
echo '{"x": "orig"}' > "$P/.claude/settings.json"
echo orig > "$P/.claude/agents/cgc-agent.md"
cat > "$P/pyproj/pyproject.toml" <<'TOML'
[project]
name = "cgc-probe"
version = "0"
requires-python = ">=3.8"
dependencies = []
TOML
# Session E markers: which project's local settings does a session started in proj/sub read?
cat > "$P/.claude/settings.local.json" <<JSON
{"permissions": {"deny": ["Edit(/$P/marker-root.txt)"]}}
JSON
cat > "$P/sub/.claude/settings.local.json" <<JSON
{"permissions": {"deny": ["Edit(/$P/marker-sub.txt)"]}}
JSON
git -C "$P" init -q
git -C "$P" config user.name cgc
git -C "$P" config user.email cgc@example.invalid
git -C "$P" add -A
git -C "$P" commit -q -m initial

# Session C: a real (not --settings) project-local settings file that reload.sh changes mid-session
cat > "$R/reload-proj/.claude/settings.local.json" <<'JSON'
{
  "sandbox": {
    "enabled": true,
    "allowUnsandboxedCommands": false
  }
}
JSON

# ---- probe hook: logs every call (with env) and decides by path ----
cat > "$R/bin/probe-hook.py" <<PY
#!/usr/bin/env python3
import datetime, json, os, sys

data = json.load(sys.stdin)
ti = data.get("tool_input") or {}
path = ti.get("file_path") or ti.get("notebook_path") or ""
tool = data.get("tool_name")
decision = None
if "/cgc/hook/deny/" in path:
    decision = "deny"
elif "/cgc/hook/ask/" in path:
    decision = "ask"
elif "/cgc/hook/modify/" in path and tool == "Write" and os.path.exists(path):
    decision = "deny"
with open("$R/hook.log", "a") as fh:
    fh.write(json.dumps({
        "t": datetime.datetime.now().isoformat(timespec="seconds"),
        "tool": tool, "path": path, "permission_mode": data.get("permission_mode"),
        "payload_cwd": data.get("cwd"), "CLAUDE_PROJECT_DIR": os.environ.get("CLAUDE_PROJECT_DIR"),
        "process_cwd": os.getcwd(), "existed": bool(path) and os.path.exists(path),
        "decision": decision or "none"}) + "\n")
if decision:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
        "permissionDecision": decision, "permissionDecisionReason": "cgc probe: " + decision}}))
PY
chmod +x "$R/bin/probe-hook.py"
HOOK='{"PreToolUse":[{"matcher":"Write|Edit|MultiEdit|NotebookEdit","hooks":[{"type":"command","command":"/usr/bin/python3","args":["'"$R"'/bin/probe-hook.py"],"onFailure":"block"}]}]}'

# ---- terminal probe for "!" commands (session G) ----
cat > "$R/bin/tty-probe" <<SH
#!/usr/bin/env bash
echo "stdin is a terminal:  \$([ -t 0 ] && echo yes || echo no)"
echo "stdout is a terminal: \$([ -t 1 ] && echo yes || echo no)"
echo "CLAUDECODE=\${CLAUDECODE:-<unset>}"
if echo bang > "$O/bang-\$\$.txt" 2>/dev/null; then
  echo "write to $O: SUCCEEDED (not sandboxed)"; rm -f "$O/bang-\$\$.txt"
else
  echo "write to $O: FAILED (sandboxed)"
fi
printf 'Type yes and press Enter (30s timeout): '
if read -r -t 30 ans; then echo "got: \$ans"; else echo "no input (timeout or no stdin)"; fi
SH
chmod +x "$R/bin/tty-probe"

# ---- caches (session A): detect where installed tools write ----
CACHES=()
add_cache() { [ -n "$1" ] && [ -d "$1" ] && CACHES+=("$1") || true; }
command -v uv >/dev/null && add_cache "$(uv cache dir 2>/dev/null)"
python3 -m pip --version >/dev/null 2>&1 && add_cache "$(python3 -m pip cache dir 2>/dev/null)"
command -v npm >/dev/null && add_cache "$(npm config get cache 2>/dev/null)"
add_cache "$HOME/.cargo/registry"
add_cache "$HOME/.cache"
printf '%s\n' "${CACHES[@]}" | awk 'NF && !seen[$0]++' > "$R/cache-dirs.txt"

# ---- settings files ----
SANDBOX='"sandbox":{"enabled":true,"allowUnsandboxedCommands":false}'
echo "{$SANDBOX}" > "$S/A.json"
cat > "$S/B.json" <<JSON
{"sandbox":{"enabled":true,"allowUnsandboxedCommands":false,"filesystem":{"allowWrite":["$R/b-work"]}},
 "permissions":{
  "deny":["Edit(~/cgc/proj/home-pat/**)","Read(~/cgc/proj/secret/**)","Read(//**/.env)",
          "Read(//**/.env.*)","Edit(//**/.claude/settings*.json)"],
  "ask":["Bash(rm *)","Bash(pwd *)"]}}
JSON
cat > "$S/D.json" <<JSON
{$SANDBOX,
 "permissions":{"ask":["Edit(/$R/rule-ask/**)"]},
 "hooks":$HOOK}
JSON
cat > "$S/E.json" <<JSON
{"hooks":$HOOK}
JSON
cat > "$S/G.json" <<JSON
{"sandbox":{"enabled":true,"allowUnsandboxedCommands":false},
 "permissions":{"disableBypassPermissionsMode":"disable"}}
JSON
echo "$HOOK" > "$R/hook-entry.json"
if [ -n "$WIN" ]; then
  mkdir -p "$WIN"
  echo orig > "$WIN/existing.txt"
  echo orig > "$WIN/keep.txt"
  echo "$WIN" > "$R/win-dir.txt"
  cat > "$R/winproj/.claude/settings.local.json" <<JSON
{"sandbox":{"enabled":true,"allowUnsandboxedCommands":false,"filesystem":{"denyWrite":["$WIN"]}},
 "permissions":{"allow":["Edit(/$WIN/**)"]}}
JSON
fi

# ---- prompts (literal paths) ----
HEAD='Run each numbered step as its own separate tool call, using the literal paths exactly as written (no shell variables). Where a step says "Read+Edit", first read the file with the Read tool, then use the Edit tool to replace "orig" with "edited". Report the exact output or error of each step. Do not retry, and do not work around any failure.'

n=0; out=""
step() { n=$((n + 1)); out+="$n. $1"$'\n'; }
emit() { { echo "$HEAD"; printf '%s' "$out"; } > "$Q/$1.txt"; n=0; out=""; }

step "Bash: pwd"
step "Bash: echo hi > $P/a-bash.txt"
step "Bash: echo hi > $P/sub/a-bash.txt"
step "Bash: mkdir -p /tmp/claude && echo hi > /tmp/claude/cgc-a.txt"
step "Bash: printenv TMPDIR"
step "If step 5 printed a directory, Bash: echo hi > <that directory, typed out literally>/cgc-a.txt. Otherwise skip this step."
step "Bash: echo hi > /tmp/cgc-a.txt"
step "Bash: echo hi > $O/a-bash.txt"
while IFS= read -r c; do step "Bash: touch \"$c/cgc-phase2-probe\""; done < "$R/cache-dirs.txt"
command -v uv >/dev/null && step "Bash: uv sync --project $P/pyproj"
step "Bash: git -C $P status --short"
emit A

step "Write tool: create $P/home-pat/new.txt containing \"hi\""
step "Bash: echo hi > $P/home-pat/bash.txt"
step "Read tool: $P/secret/key.txt"
step "Bash: cat $P/secret/key.txt"
step "Read tool: $P/.env"
step "Read tool: $P/.env.local"
step "Read tool: $P/config/.env.production"
step "Bash: cat $P/.env.local"
step "Read+Edit $P/nested/.claude/settings.local.json"
step "Bash: echo '{}' > $P/nested/.claude/settings.local.json"
step "Bash: rm $R/b-work/victim.txt"
step "Bash: pwd"
emit B

step "Bash: echo hi > $P/home-pat/bash.txt"
step "Bash: cat $P/secret/key.txt"
step "Bash: cat $P/.env.local"
step "Bash: echo '{\"y\": 1}' > $P/nested/.claude/settings.local.json"
emit B2

step "Write tool: create $O/c1.txt containing \"hi\""
step "Write tool: create $R/hook/deny/c1.txt containing \"hi\""
step "Bash: echo hi > $R/reload-proj/locked/c1.txt"
emit C1
step "Write tool: create $O/c2.txt containing \"hi\""
emit C2
step "Write tool: create $R/hook/deny/c3.txt containing \"hi\""
emit C3
step "Bash: echo hi > $R/reload-proj/locked/c4.txt"
emit C4

step "Write tool: create $P/.claude/skills/cgc-demo/SKILL.md containing \"hi\""
step "Read+Edit $P/.claude/agents/cgc-agent.md"
step "Write tool: create $P/.claude/commands/cgc.md containing \"hi\""
step "Read+Edit $P/.claude/settings.json"
step "Write tool: create $R/hook/ask/d.txt containing \"hi\""
step "Write tool: create $R/hook/deny/d.txt containing \"hi\""
step "Write tool: create $R/hook/modify/new.txt containing \"hi\""
step "Read $R/hook/modify/existing.txt with the Read tool, then use the Write tool to overwrite it with \"replaced\""
step "Read+Edit $R/hook/modify/existing.txt"
step "NotebookEdit tool: in $R/hook/modify/nb.ipynb replace the source of cell c1 with \"edited\" (say so if you have no NotebookEdit tool)"
step "MultiEdit tool: in $R/hook/modify/multi.txt replace \"orig\" with \"edited\" (say so if you have no MultiEdit tool, and do not substitute another tool)"
step "Read+Edit $R/rule-ask/existing.txt"
step "Read+Edit $R/rule-ask/second.txt"
step "Read+Edit $R/hook/ask/existing.txt"
step "Read+Edit $R/hook/ask/second.txt"
step "Bash: mkdir -p $P/.claude/hooks && echo hi > $P/.claude/hooks/cgc.sh"
step "Bash: echo '{}' > $P/.mcp.json"
step "Bash: echo hi >> $P/.bashrc"
step "Bash: echo hi > $P/.claude/skills/bash-probe.txt"
emit D
for mode in acceptEdits default; do
  step "Write tool: create $R/hook/ask/m-$mode.txt containing \"hi\""
  step "Write tool: create $R/hook/deny/m-$mode.txt containing \"hi\""
  step "Write tool: create $O/m-$mode.txt containing \"hi\""
  emit "D-$mode"
done

step "Write tool: create $P/marker-root.txt containing \"hi\""
step "Write tool: create $P/marker-sub.txt containing \"hi\""
step "Write tool: create $P/sub/e.txt containing \"hi\""
emit E

if [ -n "$WIN" ]; then
  step "Write tool: create $WIN/new.txt containing \"hi\""
  step "Read+Edit $WIN/existing.txt"
  step "Bash: echo hi > $WIN/bash.txt"
  step "Bash: rm $WIN/keep.txt"
  step "Bash: mv $WIN/keep.txt $WIN/renamed.txt"
  step "Bash: cat $WIN/existing.txt"
  emit F
fi

step "Bash: echo hi > $O/g.txt"
step "Run exactly the same command again with the sandbox disabled (the Bash tool's dangerouslyDisableSandbox option). Report what happens."
step "Bash: $R/bin/tty-probe"
emit G

echo "Fixtures in $R. Prompts in $Q. Cache folders probed in session A:"
sed 's/^/  /' "$R/cache-dirs.txt"
[ -n "$WIN" ] && echo "Windows test folder: $WIN" || echo "No Windows folder given: session F is skipped."
