#!/usr/bin/env bash
# Removes exactly what setup.sh created. Run in a plain terminal.
set -euo pipefail
WINUSER="${1:?usage: cleanup.sh <windows-username>}"
rm -rf /mnt/d/claude-guard-test "/mnt/c/Users/$WINUSER/claude-guard-test" \
  "$HOME/cg-test-home" "$HOME/cg-test-cwd" "$HOME/cg-test-proj" "$HOME"/cg-test-{A,B,C}.json
echo cleaned
