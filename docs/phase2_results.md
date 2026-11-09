# Phase 2 results

Runbook: `phase2-verification.md`. For each step write: **silent** / **prompted** / **blocked**, and paste exact messages. Leave a cell empty if a step was skipped, and say why.

## Environment

| | |
|---|---|
| Date | 2026-11-09 |
| `claude --version` | 2.1.321 (Claude Code) |
| Platform | WSL2 |
| Cache folders setup printed | `~/.config/cache/uv` (uv), `~/.npm` (npm), `~/.cache`. pip not detected. `XDG_CACHE_HOME` appears to be `~/.config/cache` |
| User settings contain permissions / sandbox / hooks? | |
| Windows test folder (session F) | |

## Session A: open by default

| Step | Result | Message / notes |
|---|---|---|
| 1 pwd | silent | `/home/haydn/cgc/proj` |
| 2 project root | silent, succeeded | |
| 3 project sub-folder | silent, succeeded | |
| 4 /tmp/claude | silent, **failed** | `mkdir: cannot create directory '/tmp/claude': Read-only file system` |
| 5 TMPDIR value | silent | `/tmp/claude-1000` |
| 6 TMPDIR write | silent, succeeded | `/tmp/claude-1000/cgc-a.txt` |
| 7 /tmp | silent, failed | `(eval):1: read-only file system: /tmp/cgc-a.txt` |
| 8 outside (control) | silent, failed (as expected) | `(eval):1: read-only file system: /home/haydn/cgc/outside/a-bash.txt` |
| 9 touch `~/.config/cache/uv` | silent, **failed** | `Read-only file system` |
| 10 touch `~/.npm` | silent, **failed** | `touch: cannot touch '/home/haydn/.npm/cgc-phase2-probe': Read-only file system` |
| 11 touch `~/.cache` | silent, **failed** | `touch: cannot touch '/home/haydn/.cache/cgc-phase2-probe': Read-only file system` |
| 12 uv sync | silent, **failed** (exit 2) | `error: Could not acquire lock / cause: Could not create temporary file / cause: Read-only file system (os error 30) at path "/home/haydn/.config/cache/uv/.tmpV9xEX7"` |
| 13 git status | silent, succeeded | Lists untracked files the fixtures never created (see below) |

No approval prompts in the whole session.

**Unexpected: placeholder paths inside the sandbox.** Step 13 listed these as untracked in `~/cgc/proj`, none of which setup created: `.bash_profile`, `.bashrc`, `.profile`, `.zprofile`, `.zshrc`, `.gitconfig`, `.gitmodules`, `.ripgreprc`, `.mcp.json`, `.idea`, `.vscode`, `.claude/hooks`, `.claude/launch.json`, `.claude/loop.md`, `.claude/output-styles`, `.claude/routines`, `.claude/scheduled_tasks.json`, `.claude/workflows`, and `marker-root.txt`. After the session none of them exist on disk (checked with `ls -la`). They look like mount points the sandbox creates for paths it makes read-only inside the working directory, visible only inside the sandbox: a built-in list, plus `marker-root.txt` from the project's own `Edit` deny rule (consistent with VB-S7). `.claude/skills`, `agents` and `commands` are not on the list. One artifact does stay on disk: `.claude/.cc-writes/` (mode 700, created during the session).

### Conclusions

- **Project folder writable by default: confirmed** (VB-S4, VB-U9), including sub-folders.
- **The documented `/tmp/claude` is wrong here.** The writable temp folder is `$TMPDIR`, which is `/tmp/claude-<uid>`; `/tmp` itself and `/tmp/claude` are read-only. `guard propose` must write under `$TMPDIR`.
- **Caches must be opened by `baseline`.** Every cache folder was read-only, and a real `uv sync` fails. The list must come from each tool's configured location (`uv cache dir`, `npm config get cache`, `$XDG_CACHE_HOME`), not hard-coded `~/.cache`.
- **New lead:** Claude Code's sandbox appears to protect a built-in list of config paths in the working directory (shell startup files, `.gitconfig`, `.mcp.json`, `.vscode`, `.idea`, `.claude/hooks` and others) from Bash. To confirm in session D (a Bash write to one of them) before the design relies on it.

## Session B: deny patterns and ask rules

| Step | Result | Message / notes |
|---|---|---|
| 1 Edit(~/) Write tool | blocked | `File is in a directory that is denied by your permission settings` |
| 2 Edit(~/) via Bash | blocked by the **auto-mode classifier** (inconclusive) | "dangerous", no explanation. `home-pat/` is empty afterwards |
| 3 Read(~/) Read tool | blocked | `File is in a directory that is denied by your permission settings` |
| 4 Read(~/) via cat | blocked by the **classifier** (inconclusive) | same as step 2 |
| 5 .env | blocked | permission-settings error |
| 6 .env.local | blocked | permission-settings error |
| 7 config/.env.production | blocked | permission-settings error |
| 8 .env.local via cat | blocked by the **classifier** (inconclusive) | same as step 2 |
| 9 nested .claude/settings, Edit | Read succeeded (`{"x": "orig"}`); Edit blocked | `File is in a directory that is denied by your permission settings` |
| 10 nested .claude/settings, Bash | **prompted**, approved, **succeeded** | File on disk is now `{}`: the `//**/` deny did not stop Bash |
| 11 rm (ask rule, sandboxed) | **prompted**, approved, succeeded | `victim.txt` gone |
| 12 bare pwd (ask rule) | **prompted**, approved | `/home/haydn/cgc/proj` |

### Conclusions

- **VB-U4, tools side: confirmed.** `Edit(~/...)`, `Read(~/...)`, `Read(//**/.env)`, `Read(//**/.env.*)` and `Edit(//**/.claude/settings*.json)` all block the Edit, Write and Read tools, including at depth (`config/.env.production`, `nested/.claude/`). Note that `Read` deny does not stop the Read tool from *reading* a file covered only by an `Edit` deny (step 9), as expected.
- **A `//**/` Edit deny does not reach the sandbox.** Step 10 wrote the file through Bash; the only protection was a prompt (apparently Claude Code noticed the redirect target matched a deny rule, but asked rather than refused). VB-S7 (Edit deny reaches Bash) holds for a plain path, not for this glob form. Consequence: locking settings files with a `**` pattern protects them from the Edit tools only; Bash needs explicit paths (`denyWrite`) or the sandbox default.
- **Steps 2, 4, 8 are inconclusive**: the auto-mode classifier blocked them before the sandbox or rules were reached (VB-C1). Re-run in default mode, which has no classifier: prompt `B2`.
- **VB-U6 confirmed for auto mode:** a `Bash(rm *)` ask rule prompts for a sandboxed `rm` that the sandbox would allow.
- **VB-R1 extends to ask rules:** `Bash(pwd *)` ask prompted for the bare `pwd`.

### B2: re-run in default mode

`cd ~/cgc/proj && claude --settings ~/cgc/settings/B.json --permission-mode default`, prompt `B2`.

| Step | Result | Message / notes |
|---|---|---|
| 1 Edit(~/) via Bash | blocked by the **sandbox** | `(eval):1: read-only file system: /home/haydn/cgc/proj/home-pat/bash.txt`. `home-pat/` still empty on disk |
| 2 Read(~/) via cat | blocked by the **sandbox**: file hidden | `cat: /home/haydn/cgc/proj/secret/key.txt: No such file or directory`. The file exists on disk (13 bytes), so the sandbox hid it rather than the path being wrong |
| 3 .env.local via cat | **not blocked** | printed `TOKEN=dotenv-local` |
| 4 nested .claude/settings via Bash | **succeeded** (no prompt reported) | File on disk is now `{"y": 1}`; before the step it held `{}`, written by session B step 10 |

### B2 conclusions

- **Deny rules with a plain or `~/` path reach the sandbox, for both writes and reads.** `Edit(~/...)` makes the path read-only to Bash (VB-S7 extends to the `~/` form). `Read(~/...)` hides the path from Bash entirely: it reports "No such file or directory", which is even more confusing than VB-S3's `Read-only file system`.
- **Deny rules with a `//**/` glob do not reach the sandbox at all**, for writes (B step 10, B2 step 4) or reads (B2 step 3). They protect the Edit, Write and Read tools only. In default mode there was not even the prompt seen in auto mode.
- Consequences for the design:
  - Locked settings files and secrets that Bash could reach (inside the project or a grant) need **explicit paths**, not a `**` pattern. `guard` knows the project and grant folders, so it can emit `denyWrite` / read-deny entries for the concrete paths, and keep the `**` rule for the file tools elsewhere.
  - `.env` files inside a project are readable by Bash unless named explicitly. Practical options: accept it (they are the project's own files), or have `guard` list the `.env*` files present in the project and grants at apply time (they change, so `doctor` would flag new ones).

## Session C: live reload

| Change | Prompt | Result before restart | Result after restart (if needed) |
|---|---|---|---|
| none | C1 (3 steps) | all silent and succeeded, as expected: Write outside, Write into `hook/deny/` (no hook yet), Bash into `locked/`. All three files on disk. Afterwards Claude Code offered to "teach auto mode about my environment"; answered "not now" (unrelated to the steps) | n/a |
| Edit deny rule | C2 | **blocked: reloaded live**. `File is in a directory that is denied by your permission settings` (the same Write succeeded in C1) | n/a |
| hook | C3 | **blocked: hook reloaded live**. `PreToolUse:Write hook error: cgc probe: deny` (the same Write succeeded in C1) | n/a |
| denyWrite | C4 | **blocked: denyWrite reloaded live**. `(eval):1: read-only file system: /home/haydn/cgc/reload-proj/locked/c4.txt` (C1 wrote there); only `c1.txt` on disk | n/a |

hook.log (read directly from disk):

```
{"t": "2026-11-09T21:19:37", "tool": "Write", "path": "/home/haydn/cgc/hook/deny/c3.txt", "permission_mode": "auto", "payload_cwd": "/home/haydn/cgc/reload-proj", "CLAUDE_PROJECT_DIR": "/home/haydn/cgc/reload-proj", "process_cwd": "/home/haydn/cgc/reload-proj", "existed": false, "decision": "deny"}
```

### Conclusions

- **VB-U3 resolved for a project-local settings file:** permission rules, hook registrations and `sandbox.filesystem.denyWrite` all apply to a running session within about 15 seconds, with no restart. Together with VB-S6, every kind of entry guard writes reloads live. Not tested: the user settings file (`~/.claude/settings.json`), which the runbook deliberately does not touch.
- One log line only: the hook ran for C3 and was not present for C1/C2, as expected.
- Bonus for session E: in hooks, `CLAUDE_PROJECT_DIR` is set (`/home/haydn/cgc/reload-proj`), equal to the payload `cwd` and the hook process's working directory when the session starts at the project root.

## Session D: hook, prompts, `.claude/`

Auto mode:

| Step | Result | Message / notes |
|---|---|---|
| 1 .claude/skills Write | silent, succeeded | |
| 2 .claude/agents Edit | silent, succeeded | |
| 3 .claude/commands Write | silent, succeeded | |
| 4 .claude/settings.json Edit | **silent, succeeded** | File is now `{"x": "edited"}`: no built-in protection of project settings from the Edit tool in auto mode |
| 5 hook ask | **prompted**, approved (option 1) | Prompt below |
| 6 hook deny | blocked | `PreToolUse:Write hook error: cgc probe: deny` |
| 7 modify: new file | silent, succeeded | |
| 8 modify: overwrite existing | blocked | `PreToolUse:Write hook error: cgc probe: deny`; file still `orig` before step 9 |
| 9 modify: Edit existing | silent, succeeded | file now `edited` |
| 10 NotebookEdit | silent, succeeded | Hook log has a `NotebookEdit` line. Claude had to load the tool through ToolSearch first |
| 11 MultiEdit | not run | No MultiEdit tool exists in this version |
| 12 ask rule prompt | **prompted**; chose option 2 ("always allow access to .../rule-ask for this session") | Prompt below |
| 13 ask rule, second file | **prompted again**, only Yes / No offered; approved | |
| 14 hook ask prompt | **prompted**; chose option 2 ("always allow access to .../hook/ask for this session") | Prompt below |
| 15 hook ask, second file | **prompted again**, only Yes / No offered; approved | |
| 16 Bash → .claude/hooks | blocked by the sandbox | `mkdir: cannot create directory '/home/haydn/cgc/proj/.claude/hooks': File exists` |
| 17 Bash → .mcp.json | blocked by the sandbox | `(eval):1: permission denied: /home/haydn/cgc/proj/.mcp.json` |
| 18 Bash → .bashrc | blocked by the sandbox | `(eval):1: permission denied: .../proj/.bashrc` |
| 19 Bash → .claude/skills (control) | **blocked** (expected to succeed) | `(eval):1: read-only file system: /home/haydn/cgc/proj/.claude/skills/bash-probe.txt` |

Prompt at step 5 (hook ask on Write):

```
 Opened changes in Visual Studio Code ⧉
 ../hook/ask/d.txt
 Save file to continue…

 Do you want to create d.txt?
 ❯ 1. Yes
   2. Yes, and always allow access to /home/haydn/cgc/hook/ask for this session (shift+tab)
   3. No
```

Prompt at step 12 (Edit ask rule):

```
 Opened changes in Visual Studio Code ⧉
 ../rule-ask/existing.txt
 Save file to continue…

 Do you want to make this edit to existing.txt?
 ❯ 1. Yes
   2. Yes, and always allow access to /home/haydn/cgc/rule-ask for this session (shift+tab)
   3. No
```

Prompt at step 13 (after choosing option 2 at step 12):

```
 Opened changes in Visual Studio Code ⧉
 ../rule-ask/second.txt
 Save file to continue…

 Do you want to make this edit to second.txt?
 ❯ 1. Yes
   2. No
```

Prompt at step 14 (hook ask on Edit): identical in form to step 12, for `../hook/ask/existing.txt`, option 2 "always allow access to /home/haydn/cgc/hook/ask for this session". Step 15 (after choosing option 2): identical in form to step 13, for `../hook/ask/second.txt`.

hook.log (read from disk; times, tool, path under `~/cgc`, mode, decision, file existed, `CLAUDE_PROJECT_DIR`):

```
21:31:23 Write proj/.claude/skills/cgc-demo/SKILL.md auto none False /home/haydn/cgc/proj
21:31:27 Write proj/.claude/commands/cgc.md auto none False /home/haydn/cgc/proj
21:31:28 Write hook/ask/d.txt auto ask False /home/haydn/cgc/proj
21:35:06 Write hook/deny/d.txt auto deny False /home/haydn/cgc/proj
21:35:06 Write hook/modify/new.txt auto none False /home/haydn/cgc/proj
21:35:09 Edit proj/.claude/agents/cgc-agent.md auto none True /home/haydn/cgc/proj
21:35:18 Edit proj/.claude/settings.json auto none True /home/haydn/cgc/proj
21:35:18 Write hook/modify/existing.txt auto deny True /home/haydn/cgc/proj
21:35:23 Edit rule-ask/existing.txt auto none True /home/haydn/cgc/proj
21:36:03 Edit rule-ask/second.txt auto none True /home/haydn/cgc/proj
21:36:32 Edit hook/ask/existing.txt auto ask True /home/haydn/cgc/proj
21:36:53 Edit hook/ask/second.txt auto ask True /home/haydn/cgc/proj
21:37:28 Edit hook/modify/existing.txt auto none True /home/haydn/cgc/proj
21:37:30 NotebookEdit hook/modify/nb.ipynb auto none True /home/haydn/cgc/proj
```

### Conclusions

- **Approval prompts show the change.** With an IDE connected, the diff opens in VS Code ("Save file to continue…"). Hook `ask` and `Edit` ask-rule prompts look the same, and neither shows the hook's reason (VB-E7).
- **A session-wide "always allow" does not bypass `ask`.** After choosing it, the next edit in the same folder prompted again, for both the ask rule (13) and the hook ask (15), and the "always allow" option was no longer offered. The steps were requested in parallel, but the timestamps show Claude Code processed them one at a time: the hook for step 13 ran 40 seconds after step 12's, so step 13 was checked after you had answered step 12. **So an `ask` ceiling means every change is approved.**
- **`modify` works as designed:** new files and Edits pass; a Write over an existing file is refused by the hook (it can see the file exists before the write).
- **NotebookEdit reaches the hook** (VB-U7). **MultiEdit no longer exists** in this version; keeping it in the matcher is harmless.
- **`CLAUDE_PROJECT_DIR` is set** in every hook call (`/home/haydn/cgc/proj`).
- **The project's `.claude/` is writable through the Edit/Write tools in auto mode with no prompt** (steps 1–4), which suits editing skills, but also means the project's **shared `settings.json` is editable silently**. The locked-settings deny rules are needed.
- **Bash cannot write anywhere in the project's `.claude/`**, including `skills/` (19), nor to `.mcp.json` or `.bashrc` in the project (16–18). This is Claude Code's built-in sandbox protection (the placeholders seen in session A), and it covers the settings files guard wants locked. Whether it also covers `.claude/` inside *granted* folders (not the working directory) is untested; the design should keep explicit `denyWrite` entries there.

Other modes:

| Step | acceptEdits | default |
|---|---|---|
| 1 hook ask | **prompted**, approved, succeeded (log: mode `acceptEdits`, decision `ask`) | **prompted**, approved, succeeded (log: mode `default`, decision `ask`) |
| 2 hook deny | blocked: `PreToolUse:Write hook error: cgc probe: deny` | blocked: same message |
| 3 outside (control) | **prompted**, approved, succeeded (hook: no opinion), as VB-E2 predicts | **prompted**, approved, succeeded (hook: no opinion) |

**Conclusion:** hook `ask` and `deny` behave the same in auto, acceptEdits and default mode. Protected paths can rely on the hook in every mode.

## Session E: project root

| | Result |
|---|---|
| 1 marker-root.txt | **blocked**: `File is in a directory that is denied by your permission settings` (no hook log line: denied before the hook, VB-E5) |
| 2 marker-sub.txt | **blocked**: same message, no hook log line |
| 3 sub/e.txt | silent, succeeded |
| CLAUDE_PROJECT_DIR (log) | `/home/haydn/cgc/proj/sub` (the launch folder, not the git root) |
| payload_cwd (log) | `/home/haydn/cgc/proj/sub`; hook process cwd the same |

No prompts. (Claude sent the three calls in parallel; the results do not depend on order.)

### Conclusions

- **Both `.claude/settings.local.json` files were in force**: the sub-folder's and the git root's. A session started in a sub-folder reads project settings from the launch folder *and* from the enclosing repo root. Not tested: whether it stops at the git root or reads every ancestor folder.
- **`CLAUDE_PROJECT_DIR` and the payload `cwd` are the launch folder** (`proj/sub`), not the git root.
- Consequences for the design:
  - Writing project grants into the **git root's** local file (design 7.1) is right: they also apply to sessions started in sub-folders.
  - The hook's "inside the project" test uses `CLAUDE_PROJECT_DIR`, i.e. the launch folder. A session started in `proj/sub` would treat `proj/other/` as outside the project (the hook asks), while the root's grants still apply. To match Claude Code, the hook should treat the **git root containing `CLAUDE_PROJECT_DIR`** as the project when there is one. Whether the sandbox's default write access covers the git root or only the launch folder is unknown (session A started at the root); until checked, the hook should follow the git root and `explain` should say Bash may only write in the launch folder.

## Session F: `edit` access on a Windows drive

| Step | Result | Message / notes |
|---|---|---|
| 1 Write new | silent, succeeded | `File created successfully at: /mnt/c/Users/haydn/cgc-win/new.txt` |
| 2 Edit existing | silent, succeeded | Read showed `orig`; Edit succeeded |
| 3 Bash write | blocked by the sandbox | `(eval):1: read-only file system: /mnt/c/Users/haydn/cgc-win/bash.txt` |
| 4 Bash rm | blocked by the **auto-mode classifier** before running | `[Irreversible Local Destruction]`. Inconclusive for the sandbox, but `keep.txt` survived |
| 5 Bash mv | blocked by the sandbox | `mv: cannot move '.../keep.txt' to '.../renamed.txt': Read-only file system` |
| 6 cat | succeeded | printed `edited` |

`ls -la` afterwards (read from disk):

```
-rwxrwxrwx 1 haydn haydn   7 Nov  9 21:55 existing.txt
-rwxrwxrwx 1 haydn haydn   5 Nov  9 20:53 keep.txt
-rwxrwxrwx 1 haydn haydn   2 Nov  9 21:55 new.txt
```

No `bash.txt`, no `renamed.txt`, `keep.txt` intact.

### Conclusions

- **The `edit` access level works on a Windows drive, set from a project-local file:** the Edit/Write tools create and modify files with no prompt, and Bash can neither write nor rename there. Step 4 never reached the sandbox, but `rm` under `denyWrite` was already shown to fail (VB-S8), and the same mechanism blocked steps 3 and 5 here.

## Session G: lock and `!`

| Check | Result |
|---|---|
| `--permission-mode bypassPermissions` | **refused, silently**: Claude started with no message, the footer showed "auto mode on" (the user's default mode), shift+tab offered no bypass mode, and a test Write outside the project was labelled "Allowed by auto mode" |
| `--dangerously-skip-permissions` | **refused, silently**: same footer and shift+tab result. The test Write targeted the existing `g-bypass.txt` (old file name) and failed with `File has not been read yet` (VB-E6), which says nothing about the mode; the footer and mode list are the evidence |
| 1 control write | blocked by the sandbox: `(eval):1: read-only file system: /home/haydn/cgc/outside/g.txt` |
| 2 unsandboxed retry | blocked by the **auto-mode classifier** (`[Security Weaken]`) before reaching the setting: inconclusive for `allowUnsandboxedCommands`. Claude added that "this session's configuration also has that option disabled", unverified. Re-run in default mode: see G2 below |
| 3 probe run by Claude | sandboxed, no terminal: `stdin is a terminal: no`, `stdout is a terminal: no`, `CLAUDECODE=1`, `write to /home/haydn/cgc/outside: FAILED (sandboxed)`, `no input (timeout or no stdin)` |

G2 (default mode, no classifier): `cd ~/cgc/proj && claude --settings ~/cgc/settings/G.json --permission-mode default`, then ask Claude to run `echo hi > /home/haydn/cgc/outside/g2.txt` with `dangerouslyDisableSandbox: true`.

| Check | Result |
|---|---|
| unsandboxed command with `allowUnsandboxedCommands: false` | **not run.** (An earlier version of this file recorded it as refused, based on a misread message; that was wrong.) `allowUnsandboxedCommands` remains unverified |

`! ~/cgc/bin/tty-probe` (typed by the user), as reported:

- write to `/home/haydn/cgc/outside`: **SUCCEEDED (not sandboxed)**
- stdin and stdout: **not terminals**
- "Type yes" prompt: no input, timed out

**Conclusion:** `!` commands run **outside the sandbox** (so they could write settings files), but get **no terminal and no keyboard input**, exactly like Claude's own Bash calls. Their output also goes into the conversation, so Claude sees it. So `guard review` cannot ask for confirmation when run with `!`, and it cannot tell a `!` run from one of Claude's own Bash calls by the terminal alone (`CLAUDECODE=1` in both). It must run in a separate terminal.

## Anything unexpected
