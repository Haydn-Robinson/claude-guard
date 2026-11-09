#!/usr/bin/env bash
# Creates throwaway test folders. Run in a plain terminal, NOT inside Claude.
# Usage: scripts/phase0/setup.sh <windows-username>
set -euo pipefail
WINUSER="${1:?usage: setup.sh <windows-username>}"
D=/mnt/d/claude-guard-test
C="/mnt/c/Users/$WINUSER/claude-guard-test"
H="$HOME/cg-test-home"
CWD="$HOME/cg-test-cwd"   # session cwd: cwd is writable by default, so keep it a throwaway
for base in "$D" "$C"; do
  mkdir -p "$base/allowed" "$base/sibling" "$base/edit-only" "$base/prot/allowed/.protected" "$base/prot/allowed/ok"
  echo orig > "$base/sibling/victim.txt"
  echo orig > "$base/prot/allowed/.protected/secret.txt"
done
mkdir -p "$D/dslash" "$D/with space/allowed" "$D/with space/sibling"
echo orig > "$D/with space/sibling/victim.txt"
mkdir -p "$H/allowed" "$H/sibling" "$CWD"
echo orig > "$H/sibling/victim.txt"
# settings files for the sessions
mkdir -p "$CWD/.."
cat > "$HOME/cg-test-A.json" <<JSON
{"sandbox":{"enabled":true,"allowUnsandboxedCommands":false,"filesystem":{"allowWrite":["$D/allowed","$D/with space/allowed","$C/allowed","/$D/dslash","~/cg-test-home/allowed"]}}}
JSON
cat > "$HOME/cg-test-B.json" <<JSON
{"sandbox":{"enabled":true,"allowUnsandboxedCommands":false},
 "permissions":{"allow":["Edit(/$D/edit-only/**)","Edit(/$D/prot/allowed/**)"],
                "deny":["Edit(/$D/prot/allowed/.protected/**)"]}}
JSON
cat > "$HOME/cg-test-C.json" <<JSON
{"sandbox":{"enabled":true,"allowUnsandboxedCommands":false,"filesystem":{"allowWrite":[]}}}
JSON
echo "Created. Folders: $D, $C, $H, $CWD ; settings: ~/cg-test-{A,B,C}.json"
