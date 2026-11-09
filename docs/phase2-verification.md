# Phase 2: verify what the v2 design relies on

`docs/design.md` section 15 lists the Claude Code behaviours the v2 design depends on that were never observed here. This runbook checks them in seven short sessions (A to G). Everything uses throwaway folders under `~/cgc` and `--settings` files or test-project settings. **Your real Claude settings are never touched.**

| Session | Checks | Design section |
|---|---|---|
| A | Project and temp folders writable by Bash by default; which tool caches need opening; where proposals can be written | 2 (open category), 7.2 caches, 9 |
| B | `~/`, `**/` and `.env.*` deny patterns, for Edit, Read and Bash; `ask` command rules on sandboxed commands | 7.2 locked paths and secrets; presets |
| C | Do rule, hook and `denyWrite` changes reach a running session? | 10 live reload |
| D | Editing a project's `.claude/` in auto mode; the hook for `modify`; NotebookEdit and MultiEdit; what approval prompts show, and how far "don't ask again" reaches; hook ask and deny in all three modes | 6, 6.1, 8, 9 |
| E | `CLAUDE_PROJECT_DIR` in hooks; which `.claude/` is read when a session starts in a sub-folder | 7.1, 8 |
| F | `edit` access on a Windows drive from a project-local file (WSL only) | 6 |
| G | The `lock` settings; whether `!` commands get a terminal and run outside the sandbox | 7.2 baseline, 9 review |

## Legend (read first)

- 🖥️ **TERMINAL**: you type it in an ordinary terminal, not inside Claude.
- 💬 **CLAUDE**: paste the named prompt into the test session as one message. Get it with `cat ~/cgc/prompts/<name>.txt` in a second terminal and copy it. The prompts contain your literal paths on purpose (shell variables in redirects cause unrelated prompts, VB-S12).
- **Record for every step**: ran silently / **prompted** / blocked, plus the exact error or block message. Claude cannot see approval prompts, so only you can record them. When a prompt appears, **approve it** unless the step says otherwise.
- If a step is blocked by the **auto-mode classifier** ("Blocked by auto mode ..."), record that separately: it is not the mechanism under test (VB-C1).
- Do **not** use the `!` prefix except where session G says so.
- Quit each session with `/exit` before starting the next.
- Fill in `docs/phase2_results.md` as you go (the tables there match the steps here).

## Setup (once)

🖥️ **TERMINAL**:

```bash
claude --version
cd ~/Repositories/claude-guard          # or wherever this repo lives
scripts/phase2/setup.sh                 # add a Windows folder argument for session F, see below
```

For session F on WSL, pass a folder that **does not exist yet**, on a Windows drive inside your Windows user profile, which setup creates and cleanup deletes:

```bash
scripts/phase2/setup.sh /mnt/c/Users/<you>/cgc-win
```

Record the Claude Code version and the cache folders setup prints. Also record whether your **user** settings (`~/.claude/settings.json`) contain any `permissions`, `sandbox` or `hooks` entries, since they merge into every test session (`grep -E '"(permissions|sandbox|hooks)"' ~/.claude/settings.json`). This is read-only.

---

## Session A: what is open by default

Sandbox on, nothing granted.

🖥️ `cd ~/cgc/proj && claude --settings ~/cgc/settings/A.json --permission-mode auto`

💬 prompt **A**

| Steps | What it tests | Expected |
|---|---|---|
| 1 | session directory | `~/cgc/proj` |
| 2, 3 | project folder writable by Bash with no grant (VB-S4, VB-U9) | succeed |
| 4 | `/tmp/claude` writable | succeed |
| 5, 6 | `$TMPDIR` writable (where `guard propose` would write) | succeed |
| 7 | plain `/tmp` | record (may fail) |
| 8 | control: outside everything | `Read-only file system` |
| touch steps | each cache folder setup found | record each: any failure means `baseline` must open that folder |
| `uv sync` (if uv installed) | a real tool under the sandbox | record success or the exact error; a network prompt is fine, approve it |
| last | `git status` in the project | succeeds |

---

## Session B: deny patterns and ask rules

Deny rules in the `~/` and `//**/` forms, and `ask` rules for `rm` and `pwd`. `~/cgc/b-work` is writable to Bash.

🖥️ `cd ~/cgc/proj && claude --settings ~/cgc/settings/B.json --permission-mode auto`

💬 prompt **B**

| Step | What it tests | Expected |
|---|---|---|
| 1 | `Edit(~/...)` deny, Write tool | blocked ("denied by your permission settings") |
| 2 | the same rule reaching Bash through the sandbox | `Read-only file system` |
| 3 | `Read(~/...)` deny, Read tool | blocked |
| 4 | the same rule against `cat` (VB-R5) | record: blocked or prints `secret-value` |
| 5, 6, 7 | `Read(//**/.env)` and `Read(//**/.env.*)` on `.env`, `.env.local`, `config/.env.production` | blocked, all three |
| 8 | `.env.*` rule against `cat` | record |
| 9 | `Edit(//**/.claude/settings*.json)` on a nested project's settings | blocked |
| 10 | the same rule reaching Bash | `Read-only file system` |
| 11 | `Bash(rm *)` **ask** on a sandboxed, permitted `rm` (VB-U6) | **prompted**. Record whether it ran silently instead |
| 12 | `Bash(pwd *)` ask on the bare `pwd` (VB-R1 for ask rules) | **prompted** |

If step 1 or 9 is not blocked, the locked-path patterns in the design need another form. Tell me before going further.

**B2** (added after the first run, where the auto-mode classifier blocked the Bash steps before the rules or sandbox were reached): repeat the Bash checks in default mode, which has no classifier.

🖥️ `cd ~/cgc/proj && claude --settings ~/cgc/settings/B.json --permission-mode default`, then 💬 prompt **B2**. Copy the text of any prompt exactly.

---

## Session C: live reload (needs two terminals)

A real project-local settings file (not `--settings`), changed while Claude runs. Wait **15 seconds** after each `reload.sh` before pasting the next prompt.

🖥️ terminal 1: `cd ~/cgc/reload-proj && claude --permission-mode auto`

| Do | Then paste | Expected if live reload works |
|---|---|---|
| (nothing) | 💬 **C1** | step 1 silent, step 2 silent (no hook yet), step 3 succeeds (project folder) |
| 🖥️ terminal 2: `~/Repositories/claude-guard/scripts/phase2/reload.sh rule` | 💬 **C2** | blocked by the new `Edit` deny rule |
| 🖥️ terminal 2: `.../reload.sh hook` | 💬 **C3** | blocked by the hook ("cgc probe: deny") |
| 🖥️ terminal 2: `.../reload.sh denywrite` | 💬 **C4** | `Read-only file system` |

Afterwards in terminal 2: `cat ~/cgc/hook.log`. A C3 line means the hook was picked up live. If a change was not picked up, quit, restart Claude in the same folder, paste that prompt again with a new file name, and record whether it works after the restart.

---

## Session D: the hook, prompts and `.claude/`

The probe hook asks for `~/cgc/hook/ask/`, denies `~/cgc/hook/deny/`, and in `~/cgc/hook/modify/` denies only a Write over an existing file. An `Edit` **ask** rule covers `~/cgc/rule-ask/`.

🖥️ `cd ~/cgc/proj && claude --settings ~/cgc/settings/D.json --permission-mode auto`

💬 prompt **D**

At **steps 12 and 14** (the first approval prompt of each kind), copy the prompt **exactly**, including whether it shows the diff and the full list of options. If one of the options is "don't ask again" or "allow ... for this session", **choose it**. Steps 13 and 15 then show how far that choice reaches.

| Step | What it tests | Expected |
|---|---|---|
| 1, 2, 3 | Write, Edit and Write in the project's `.claude/skills`, `agents`, `commands`, auto mode | record: silent is what the design hopes for |
| 4 | Edit of the project's shared `.claude/settings.json` | record (Claude Code may protect it itself) |
| 5 | hook **ask** on Write | **prompted** |
| 6 | hook **deny** | blocked ("cgc probe: deny") |
| 7 | `modify`: Write of a new file | silent |
| 8 | `modify`: Write over an existing file | blocked |
| 9 | `modify`: Edit of an existing file | silent |
| 10 | NotebookEdit reaches the hook (VB-U7) | silent, and a `NotebookEdit` line in the log |
| 11 | MultiEdit reaches the hook (VB-U7) | record whether the tool exists; if it does, a `MultiEdit` log line |
| 12 | `Edit` **ask** rule: prompt contents and options | prompted. Copy it exactly |
| 13 | after a "don't ask again" choice at 12: does it cover another file in the same folder? | record: prompted again, or silent |
| 14 | hook **ask**: prompt contents and options | prompted. Copy it exactly |
| 15 | the same question for a hook ask | record |
| 16, 17, 18 | does the sandbox's built-in list (seen in session A) protect `.claude/hooks`, `.mcp.json` and `.bashrc` in the project from Bash? | `Read-only file system` if so |
| 19 | control: `.claude/skills` from Bash | succeeds |

Then 🖥️ `cat ~/cgc/hook.log` and paste it into the results.

Repeat in the other two modes (new session each time, same settings):

| Mode | Launch | Prompt | Expected |
|---|---|---|---|
| acceptEdits | `cd ~/cgc/proj && claude --settings ~/cgc/settings/D.json --permission-mode acceptEdits` | 💬 **D-acceptEdits** | 1 prompted, 2 blocked, 3 prompted (outside the project, VB-E2) |
| default | `... --permission-mode default` | 💬 **D-default** | same as acceptEdits |

---

## Session E: which project, and `CLAUDE_PROJECT_DIR`

The project `~/cgc/proj` (a git repo) has a local settings file denying `marker-root.txt`; its sub-folder `sub/` has its own denying `marker-sub.txt`. The session starts in `sub/`.

🖥️ `cd ~/cgc/proj/sub && claude --settings ~/cgc/settings/E.json --permission-mode auto`

💬 prompt **E**, then 🖥️ `tail -3 ~/cgc/hook.log`

| Result | Means |
|---|---|
| step 1 blocked | the repo root's `.claude/` is read |
| step 2 blocked | the sub-folder's `.claude/` is read |
| both or neither | record exactly |
| log `CLAUDE_PROJECT_DIR` | set or null, and which folder |
| log `payload_cwd` | which folder |

---

## Session F: `edit` access on a Windows drive (WSL only; skip if setup had no folder)

The test project's local settings give an `Edit` allow plus a `denyWrite` on the Windows test folder: the design's `edit` level.

🖥️ `cd ~/cgc/winproj && claude --permission-mode auto`

💬 prompt **F**, then 🖥️ `ls -la <your Windows test folder>`

| Step | Expected |
|---|---|
| 1, 2 | silent: Write creates `new.txt`, Edit changes `existing.txt` |
| 3, 4, 5 | `Read-only file system`: no new file through Bash, `keep.txt` neither deleted nor renamed |
| 6 | prints `edited` |

---

## Session G: `lock` settings and `!` commands

🖥️ First, try to start in bypass mode with the lock settings. Each should refuse or fall back to another mode. Record exactly what happens, then `/exit`:

```bash
cd ~/cgc/proj && claude --settings ~/cgc/settings/G.json --permission-mode bypassPermissions
cd ~/cgc/proj && claude --settings ~/cgc/settings/G.json --dangerously-skip-permissions
```

Then a normal session:

🖥️ `cd ~/cgc/proj && claude --settings ~/cgc/settings/G.json --permission-mode auto`

💬 prompt **G**

| Step | What it tests | Expected |
|---|---|---|
| 1 | control | `Read-only file system` |
| 2 | `allowUnsandboxedCommands: false` refuses an unsandboxed retry (VB-U5) | refused, no prompt offered |
| 3 | the probe run by Claude | not a terminal, `write ... FAILED (sandboxed)`, no input |

Now the one place you **do** use `!`. Type this into the Claude prompt yourself:

```text
! ~/cgc/bin/tty-probe
```

When it asks, try typing `yes` and Enter. Record its whole output. This tells us whether `guard review` can run as `! guard review` inside a session (it needs a terminal for its confirmation, and to run outside the sandbox to write settings), or must run in a separate terminal.

---

## Cleanup

🖥️ **TERMINAL**:

```bash
~/Repositories/claude-guard/scripts/phase2/cleanup.sh
```

It removes `~/cgc`, the Windows test folder, the temp and cache probe files. If session A step 6 wrote into a temp folder other than `$TMPDIR` as your terminal sees it, delete that `cgc-a.txt` by hand.

Then tell me. I will update `verified-behaviour.md` from `docs/phase2_results.md`, and fix anything in `design.md` the results contradict before any code depends on it.
