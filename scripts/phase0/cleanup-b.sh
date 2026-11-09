#!/usr/bin/env bash
# Removes exactly what setup-b.sh created. Run in a plain terminal.
set -euo pipefail
rm -rf /mnt/d/claude-guard-test-b "$HOME/cgb-cwd" "$HOME/cgb-gitrepo" "$HOME/cgb-hook.py" \
  "$HOME/cgb-hook.log" "$HOME"/cgb-T1a.json "$HOME"/cgb-T1b.json "$HOME"/cgb-T2.json \
  "$HOME"/cgb-T3.json "$HOME"/cgb-T3b.json "$HOME"/cgb-T4.json
echo cleaned
