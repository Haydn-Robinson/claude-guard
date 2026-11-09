#!/usr/bin/env bash
# Removes exactly what setup.sh and the runbook sessions created. Run in a plain terminal.
set -euo pipefail
R="$HOME/cgc"
if [ -f "$R/cache-dirs.txt" ]; then
  while IFS= read -r c; do rm -f "$c/cgc-phase2-probe"; done < "$R/cache-dirs.txt"
fi
if [ -f "$R/win-dir.txt" ]; then
  WIN="$(cat "$R/win-dir.txt")"
  case "$WIN" in /mnt/[a-zA-Z]/?*) rm -rf "$WIN" ;; *) echo "skipping unexpected Windows dir: $WIN" >&2 ;; esac
fi
rm -f /tmp/claude/cgc-a.txt /tmp/cgc-a.txt "/tmp/claude-$(id -u)/cgc-a.txt"
if [ -n "${TMPDIR:-}" ]; then rm -f "$TMPDIR/cgc-a.txt"; fi
rm -rf "$R"
echo "cleaned. If session A step 6 wrote to a different temp folder, delete <that folder>/cgc-a.txt by hand."
