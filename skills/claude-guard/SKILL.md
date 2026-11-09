---
name: claude-guard
description: Use when you need to write somewhere outside the current project, when a write fails with "Read-only file system" or a guard hook message, or when a task needs a change to Claude Code settings (hooks, permissions, env, statusLine, MCP servers, plugins). Explains how to ask the user for access with claude-guard instead of working around it.
---

# Working under claude-guard

This machine uses claude-guard. Claude Code enforces it: the sandbox confines Bash, and rules plus
a hook confine the Edit/Write tools. Do not try to work around it.

## What you can do without asking

- Write anywhere in the current project (except its `.claude/` settings files and `.mcp.json`).
- Write in `$TMPDIR`.
- Read almost anywhere (not secrets such as `~/.ssh` or `.env` files).

## When a write is refused or asks

- `Read-only file system` from Bash, or a hook message from Edit/Write, means the path is outside
  the project and every grant, or protected.
- Run `guard explain <path>` to see which layer decides and why.
- If the task needs access, **tell the user the exact command**, and why, then stop and wait:

  ```text
  guard allow <folder> --access <full|edit|modify|ask|bash>
  ```

  Pick the least access that does the job: `edit` when files only need changing (Bash stays
  read-only, nothing can be deleted), `full` only when Bash must create or delete there. Never run
  `guard allow` yourself: it cannot work from inside the sandbox, and grants are the user's call.

## Changing Claude Code settings

Settings files are locked. Never edit them, and never ask for a grant on them. Propose instead:

```bash
guard propose <settings file> --set KEY=VALUE --unset KEY --add KEY=VALUE --remove KEY=VALUE \
  --reason "<why, in one sentence the user can judge>"
```

- Targets: `~/.claude/settings.json`, `~/.claude/settings.local.json`, a project's
  `.claude/settings.json` or `.claude/settings.local.json`, `.mcp.json`, `~/.claude.json`.
- VALUE is JSON when it parses (`true`, `3`, `{"a": 1}`), otherwise a string.
- Example: `guard propose ~/.claude/settings.json --set statusLine.command='~/bin/status.sh' --reason "show the git branch in the status line"`.

Then ask the user to run `guard review` **in a separate terminal**: it needs them at a keyboard,
so it does not work with `!` or from your Bash tool. Nothing changes until they accept.
