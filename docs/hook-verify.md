# Verify the Edit/Write hook in a real session

Unit tests cover the decision logic. This checks that Claude Code really runs the hook that `guard` registers, for both the Write and **Edit** tools (Edit was not exercised in Phase 0b). Everything uses throwaway folders and a throwaway state dir. Your real settings are not touched.

Legend: 🖥️ **TERMINAL** = type it yourself in an ordinary terminal. 💬 **CLAUDE** = paste into the test Claude session as one message. Never use the `!` prefix.

## Setup

🖥️ **TERMINAL** (all in the same terminal window; the `export` only lasts for this window):

```bash
export CLAUDE_GUARD_HOME=/tmp/cg-verify-state
export PATH=~/Repositories/claude-guard/.venv/bin:$PATH
mkdir -p ~/cg-verify/proj/.claude ~/cg-verify/granted/.protected ~/cg-verify/outside
for f in proj granted outside granted/.protected; do echo orig > ~/cg-verify/$f/existing.txt; done
guard allow --global confine-edits --project ~/cg-verify/proj --yes
guard allow ~/cg-verify/granted edit --protect ".protected/**" --project ~/cg-verify/proj --yes
cat ~/cg-verify/proj/.claude/settings.local.json
```

Expected: both grants applied (warnings about the sandbox being off are fine here) and the settings file contains a `hooks.PreToolUse` entry with `"onFailure": "block"`.

## Test

🖥️ **TERMINAL**:

```bash
cd ~/cg-verify/proj && claude --permission-mode auto
```

💬 **CLAUDE**: paste.

```text
Run each numbered step as its own separate call, using the literal paths exactly as written. Where a step says Edit, read the file first with the Read tool, then use the Edit tool to replace "orig" with "edited". Report the exact output or error of each step. Do not retry or work around any failure.
1. Edit /home/haydn/cg-verify/proj/existing.txt
2. Edit /home/haydn/cg-verify/granted/existing.txt
3. Edit /home/haydn/cg-verify/outside/existing.txt
4. Write tool: create /home/haydn/cg-verify/outside/new.txt containing "hi"
5. Write tool: create /home/haydn/cg-verify/granted/.protected/new.txt containing "hi"
6. Edit /home/haydn/cg-verify/granted/.protected/existing.txt
7. Write tool: create /home/haydn/cg-verify/proj/new.txt containing "hi"
```

Expected (tell me where reality differs, and which steps showed an approval prompt):

| Step | Expected |
|---|---|
| 1, 2, 7 | run silently (project / granted folder) |
| 3, 4 | **prompt** (outside the project and every granted folder) |
| 5, 6 | **blocked**. Expect Claude Code's own message ("denied by your permission settings"), because deny rules are checked before the hook runs |

Quit Claude (`/exit`), then:

🖥️ **TERMINAL**:

```bash
cat /tmp/cg-verify-state/hook.log
```

Expected: one line per file-tool call, including `"tool": "Edit"` lines.

## Cleanup

🖥️ **TERMINAL** (removes only what this runbook created):

```bash
guard revoke ~/cg-verify/granted --project ~/cg-verify/proj --yes
guard revoke --global --project ~/cg-verify/proj --yes
rm -rf ~/cg-verify /tmp/cg-verify-state
```

Paste the results into `docs/hook_verify_results.md` and tell me.
