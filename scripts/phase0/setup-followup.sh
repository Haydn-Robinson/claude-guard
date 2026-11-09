#!/usr/bin/env bash
# Creates the throwaway project for follow-up test E. Run in a plain terminal, NOT inside Claude.
# Requires setup.sh to have been run already (uses /mnt/d/claude-guard-test/allowed).
set -euo pipefail
P="$HOME/cg-test-proj"
[ -d /mnt/d/claude-guard-test/allowed ] || { echo "run setup.sh first" >&2; exit 1; }
mkdir -p "$P/.claude"
cat > "$P/.claude/settings.local.json" <<JSON
{"sandbox":{"enabled":true,"allowUnsandboxedCommands":false,"filesystem":{"allowWrite":[]}}}
JSON
echo "Created $P with .claude/settings.local.json (empty allowWrite)"
