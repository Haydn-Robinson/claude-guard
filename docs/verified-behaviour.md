# Verified behaviour of Claude Code (reference for claude-guard)

This is the canonical record of what we know about how Claude Code behaves, as far as it matters to `guard`. It exists so the experiments never need repeating unless the behaviour is seen to change. The narrative history is in `phase0-findings.md`; the raw evidence is in the `*_results.md` files.

## How to read this

Every fact has an ID (`VB-xx`), a status, the evidence, what in the tool relies on it, and how to re-check it. Source code and tests cite the IDs (`VB-S8` and so on), and `tests/test_docs.py` fails if a cited ID does not exist here.

| Status | Meaning |
|---|---|
| **VERIFIED** | Observed in our own tests, on the environment below. |
| **DOCUMENTED** | Stated in the Claude Code docs (captured in `claude-permissions.md`), never tested here. `guard` may rely on it, and says so. |
| **UNVERIFIED** | An open question or an assumption. `guard` avoids depending on it, or depends on it in a way that fails safe. |

Evidence names: **A, B, C, D** = `phase0-sandbox-tests.md` sessions; **E, F** = `phase0-followup-tests.md` sessions; **T1a, T1b, T2, T3, T3b, T4** = `phase0b-primitive-tests.md`; **HV** = `hook-verify.md`; **P2-A** to **P2-G** (and B2, D-acceptEdits, D-default) = `phase2-verification.md` sessions; **P12** = the earlier raw experiments in `claude-permissions.md` section 12. Raw results: `phase0_results.md`, `phase0_followup_results.md`, `phase0b_primitive_results.md`, `hook_verify_results.md`, `phase2_results.md`.

## Environment all VERIFIED facts were observed on

| | |
|---|---|
| Claude Code | 2.1.319 (phase 0, 0b, HV); 2.1.321 (phase 2, VB-S17) |
| Platform | WSL2 (Linux 6.18.54.1-microsoft-standard-WSL2), shell zsh |
| Sandbox | native (bubblewrap 0.6.1, socat installed) |
| Filesystems | WSL home (ext4); `/mnt/d` and `/mnt/c` (drvfs / 9p) |
| Permission modes | mostly **auto**; **default** and **acceptEdits** where stated |
| IDE | VS Code connected during phase 2 (affects how approval prompts show diffs) |
| Date | 2026-11-08 (phase 0 to HV); 2026-11-09 (phase 2); 2026-11-10 (VB-S17) |

## When to re-verify

Re-run the relevant runbook when: Claude Code is upgraded to a new minor version; `guard doctor` or a real session shows behaviour that contradicts this file; or you move to a platform other than WSL2 (macOS and native Linux were never tested). Update the fact's status, the environment table and the date. Section "Re-verification map" at the end says which runbook covers which IDs.

---

## S. The native sandbox (confines Bash only)

### VB-S1 `allowWrite` accepts every path form `guard` emits
**Status:** VERIFIED
**Fact:** `sandbox.filesystem.allowWrite` entries work as plain absolute paths, paths containing spaces, `//abs`, `~/x`, `/mnt/c/Users/...`, `/mnt/d/...` and WSL home paths. Writes and `rm` inside such a folder succeed.
**Evidence:** A steps 1, 2, 4, 5, 7; D; F steps 1 to 4.
**Relied on by:** `rules.sandbox_path` (plain absolute paths); `compile` (`bash` access, `raw.sandbox.allow_write`).
**Re-check:** `phase0-sandbox-tests.md` session A.

### VB-S2 Everything outside the allowed set is read-only to Bash, enforced by the kernel
**Status:** VERIFIED
**Fact:** A write, `rm`, `mv`, `bash -c 'rm ...'` or nested `bash -c` to a sibling folder outside `allowWrite` fails, on WSL home, `/mnt/d` and `/mnt/c`. The spelling of the command does not matter. The sibling's existing files are left untouched. (Python `os.remove` was verified with raw bubblewrap only, P12, not through the native sandbox.)
**Evidence:** A steps 3, 6, 8, 9; D; F steps 5, 6; E third prompt.
**Relied on by:** the whole "what protects you" case in the README; `explain` Bash verdicts.
**Re-check:** session A, F.

### VB-S3 Sandbox denials look like ordinary filesystem errors
**Status:** VERIFIED
**Fact:** The error never mentions the sandbox. Forms seen: `(eval):1: read-only file system: <path>` (zsh redirect), `rm: cannot remove '<path>': Read-only file system`, `bash: line 1: <path>: Read-only file system`, `mv: cannot move ...: Read-only file system`, `fatal: Unable to create '<repo>/.git/index.lock': Read-only file system`, `warning: failed to remove <file>: Read-only file system`.
**Evidence:** A, B step 5, F, T1a, T1b.
**Relied on by:** README ("suspect the sandbox first"); `guard explain` exists to answer this.

### VB-S4 The default writable set (working directory, `$TMPDIR`)
**Status:** VERIFIED (for a session started at the project root)
**Fact:** With the sandbox on and nothing granted, Bash can write in the working directory and its sub-folders, and in `$TMPDIR`, which Claude Code sets to `/tmp/claude-<uid>`. `/tmp` itself and `/tmp/claude` (named in the docs) are read-only. Everything else is read-only, including tool caches (VB-S16). Exceptions inside the working directory: VB-S15.
**Evidence:** P2-A steps 2 to 8.
**Relied on by:** the design's "open by default" category; `guard propose` writes proposals under `$TMPDIR`. Whether a session started in a sub-folder of a repo can write in the rest of the repo is open (VB-U15).

### VB-S5 Granted folders must exist before the session starts
**Status:** VERIFIED
**Fact:** The sandbox cannot create a missing allowed folder; `mkdir` inside it fails with `Read-only file system` and later steps fail with `No such file or directory`.
**Evidence:** P12 (`claude-permissions.md` 12.4 item 1).
**Relied on by:** `guard allow --create`; the "does not exist" warning; `doctor` "granted folder exists".

### VB-S6 Live reload of a real settings file
**Status:** VERIFIED (for a project's `.claude/settings.local.json`; the user settings file is untested)
**Fact:** Changes to `sandbox.filesystem.allowWrite`, `sandbox.filesystem.denyWrite`, `permissions.deny` rules and `hooks.PreToolUse` registrations in a real settings file take effect in a running session within about 15 seconds, with no restart. A file passed with `--settings` is read once at launch and never reloads (checked after a minute).
**Evidence:** E (`allowWrite` live); P2-C steps C2 to C4 (deny rule, hook, `denyWrite` live); C (`--settings`, never applied).
**Relied on by:** README "Applying changes"; the design's "no restart needed" after `guard apply`.

### VB-S7 `Edit` rules reach the sandbox
**Status:** VERIFIED
**Fact:** An `Edit` allow rule on a folder gives Bash write access there with no `allowWrite` entry. An `Edit` deny rule on a sub-path makes Bash writes there fail (`Read-only file system`). This holds for `//abs` and `~/` paths; it does **not** hold for `//**/` glob patterns (VB-S14).
**Evidence:** B steps 1, 2 (allow); B step 5 (deny; steps 3 and 4 were stopped earlier by the classifier, VB-C1); P2-B2 step 1 (`~/` form).
**Relied on by:** `compile`: `full` is a single `Edit` allow, `deny` and ceiling-`deny` paths a single `Edit` deny, and `protect` globs on concrete paths need nothing else; `views/explain` (allow and deny rules on concrete paths count for Bash).
**Re-check:** session B.

### VB-S8 `denyWrite` wins over any allow, with no sub-path exception
**Status:** VERIFIED
**Fact:** With an `Edit` allow on a folder, `denyWrite` on the folder and `allowWrite` on `folder/.git`, Bash can write nowhere in the folder, including `.git`. The more specific allow does not override the deny. `git add` and `git commit` fail with `index.lock: Read-only file system`; `git clean`, `rm`, `mv` and `>` fail too. `git status` works.
**Evidence:** T1a and T1b (identical results); P2-F (an `Edit` allow plus `denyWrite` on a `/mnt/c` folder, set from a project-local file: the Edit/Write tools created and edited files, Bash write and `mv` failed with `Read-only file system`).
**Relied on by:** the access levels `edit`, `modify` and `ask` (`compile._grant_entries`); the nested-grant conflict check (`compile._nested_conflict`). Consequence: a Bash-read-only folder cannot be committed to from inside Claude.
**Re-check:** T1.

### VB-S9 The two `denyWrite` spellings behave the same
**Status:** VERIFIED
**Fact:** `denyWrite: ["<folder>"]` and `["<folder>/**"]` both make the whole folder read-only to Bash.
**Evidence:** T1a vs T1b.
**Relied on by:** `compile` emits the plain folder form.

### VB-S10 The Edit and Write tools are not sandboxed
**Status:** VERIFIED
**Fact:** `allowWrite`/`denyWrite` do not affect the Edit and Write tools: they write into a `denyWrite` folder and into a folder outside every `allowWrite`.
**Evidence:** A step 10; F step 8; T1a/T1b steps 5, 6.
**Relied on by:** the hook exists because of it (`hook.py`); the `ask` access level.

### VB-S11 Sandboxed Bash with literal paths is not prompted
**Status:** VERIFIED (behaviour); the setting name and default are DOCUMENTED
**Fact:** In default and acceptEdits mode, sandboxed `echo > file` and `rm` on literal paths inside allowed folders run with no prompt. `sandbox.autoAllowBashIfSandboxed` (default true) is the documented reason; we did not toggle it.
**Evidence:** F (literal paths). D's prompts all came from `$VAR` targets (VB-S12), not from the sandbox.
**Relied on by:** `full` needs no Bash allow rules; `confirm-destructive` (an ask rule is what makes `rm` prompt).

### VB-S12 `$VAR` in a redirect target triggers a parser prompt unrelated to the sandbox
**Status:** VERIFIED
**Fact:** `echo hi > $D/x` prompts with "Redirect target concatenation contains $/` — unanalyzable gap or substitution" in default and acceptEdits mode, whatever the sandbox allows.
**Evidence:** D (all prompts).
**Relied on by:** runbooks use literal paths only. Matters when writing any future test.

### VB-S13 `Read` deny rules reach the sandbox: the path is hidden from Bash
**Status:** VERIFIED
**Fact:** A `Read(~/...)` deny rule makes the path invisible to Bash: `cat` reports `No such file or directory` although the file exists. This error is even less obviously a sandbox effect than `Read-only file system` (VB-S3).
**Evidence:** P2-B2 step 2 (file confirmed present on disk afterwards).
**Relied on by:** secrets protection for `~/` paths (baseline `read.deny`); `views/explain` says a hidden file shows up as "No such file or directory".

### VB-S14 Deny rules with a `//**/` glob do not reach the sandbox
**Status:** VERIFIED
**Fact:** `Edit(//**/.claude/settings*.json)` and `Read(//**/.env.*)` block the Edit/Write/Read tools (VB-U4) but not Bash: a redirect overwrote a matching settings file and `cat` printed a matching `.env.local`. In auto mode the Bash overwrite produced an approval prompt (not a refusal); in default mode there was no prompt at all.
**Evidence:** P2-B step 10 (auto, prompted, file overwritten); P2-B2 steps 3, 4 (default, no prompt).
**Relied on by:** the design: glob rules cover the file tools only; anything Bash can reach must be named by a concrete path.

### VB-S15 Built-in Bash protection inside the working directory
**Status:** VERIFIED (behaviour); the exact list is OBSERVED, not documented here
**Fact:** Inside the working directory the sandbox makes these read-only to Bash with no configuration: the whole `.claude/` folder (including `skills/`), and `.bashrc`, `.bash_profile`, `.profile`, `.zprofile`, `.zshrc`, `.gitconfig`, `.gitmodules`, `.ripgreprc`, `.mcp.json`, `.idea`, `.vscode`. Paths that do not exist appear inside the sandbox as placeholders (seen by `git status`), and are gone on the host afterwards. Errors: `permission denied`, `read-only file system`, or `File exists` (for `mkdir` on a placeholder). Claude Code also leaves a `.claude/.cc-writes/` folder on the host.
**Evidence:** P2-A step 13 (placeholders listed); P2-D steps 16 to 19.
**Relied on by:** the design: a project's own settings files are already safe from Bash. Whether the same applies to `.claude/` inside a granted folder is open (VB-U17).

### VB-S16 Tool caches are read-only to Bash by default
**Status:** VERIFIED
**Fact:** With the sandbox on, cache folders outside the project are read-only, so tools that need them fail: `uv sync` failed with `Could not acquire lock ... Read-only file system (os error 30)` in the uv cache. Cache locations come from each tool's configuration (`uv cache dir`, `npm config get cache`, `$XDG_CACHE_HOME`), not a fixed `~/.cache`.
**Evidence:** P2-A cache steps and `uv sync`.
**Relied on by:** the `baseline` preset opens each tool's configured cache folder.

### VB-S17 Sandboxed Bash cannot reach processes outside the sandbox
**Status:** VERIFIED (read-only probes; no write outside the sandbox was attempted)
**Fact:** Inside the sandbox, `socket(AF_UNIX, ...)` fails with `Operation not permitted` (TCP sockets are allowed, through the network proxy). So nothing that works by talking to a running process over a Unix socket can be used to act outside the sandbox: Docker, `systemctl --user`/`systemd-run --user` (reported `offline`), `tmux`/`screen` servers, the VS Code `code` CLI, SSH agents. On WSL, launching a Windows program (`cmd.exe /c ver`) fails with `UtilBindVsockAnyPort: socket failed 1`, so `cmd.exe`/`powershell.exe` interop cannot run Windows processes, which the Linux sandbox would not confine. Not tested: `ssh` to a TCP port on localhost (no key is readable: `~/.ssh` is hidden by the baseline, VB-S13).
**Evidence:** 2026-11-10 session probes: Python `socket.socket(AF_UNIX)` / `AF_INET`; `cmd.exe /c ver`; `tmux ls`; `systemctl --user is-system-running`.
**Relied on by:** the threat model (design section 1): a command cannot escape the sandbox's write confinement by handing the work to another process. Re-check after a Claude Code upgrade, and on any new platform.

---

## E. Edit/Write tools and permission modes

### VB-E1 In auto mode the Write tool writes anywhere with no prompt
**Status:** VERIFIED
**Fact:** Unless an `Edit` deny rule matches, a Write to a path outside the project and outside every grant ran silently in auto mode.
**Evidence:** A step 10; T2 auto step 3.
**Relied on by:** the reason the hook exists; `views/explain` ("no rule: auto mode writes without a prompt").

### VB-E2 In default and acceptEdits mode, writes outside the project prompt
**Status:** VERIFIED
**Fact:** A Write outside the project prompts ("Create file" / "Do you want to create X?" with an "always allow access to <folder> for this session" option), whether or not the folder is in `allowWrite`. `allowWrite` alone does not remove the prompt; an `Edit` allow does (VB-E3).
**Evidence:** F default prompts (allowed and sibling folders); F acceptEdits prompts; T2 acceptEdits (asked and outside prompt).
**Relied on by:** why `edit` carries an `Edit` allow as well as `allowWrite`.

### VB-E3 An `Edit` allow rule removes the prompt
**Status:** VERIFIED
**Fact:** A Write into a folder covered by `Edit(//folder/**)` allow ran with no prompt, in auto mode and in acceptEdits mode.
**Evidence:** T2 (free/ in both modes); HV steps 2 (granted folder, `edit` profile).

### VB-E4 An `Edit` ask rule prompts, even in auto mode, and does not reach Bash
**Status:** VERIFIED
**Fact:** `Edit(//folder/**)` in `permissions.ask` makes the Write tool prompt in auto mode and acceptEdits mode. A Bash redirect into the same folder ran silently (it was in `allowWrite` in that test, see VB-U2).
**Evidence:** T2 steps 1, 4, and the acceptEdits run.
**Relied on by:** the `ask` access level.

### VB-E5 An `Edit` deny rule refuses the Edit/Write tools, before any hook runs
**Status:** VERIFIED
**Fact:** Writing or editing a path covered by an `Edit` deny rule fails in auto mode with "File is in a directory that is denied by your permission settings". The PreToolUse hook never sees such calls (no hook-log entry).
**Evidence:** B step 6; HV steps 5, 6 and the hook log.
**Relied on by:** `deny` access, ceiling-`deny` protected paths, locked paths and `protect` globs; the hook's own deny for these is only a backstop.

### VB-E6 The Edit tool (and Write over an existing file) need a prior Read
**Status:** VERIFIED
**Fact:** Edit fails with "File has not been read yet. Read it first before writing to it." if the file was not read in the session. This check happens before hooks run. Write over an existing file behaves the same.
**Evidence:** T3 step 5; T2 acceptEdits note.
**Relied on by:** runbooks (`hook-verify.md` tells Claude to Read first). Matters for any future test of Edit.

### VB-E7 The approval prompt does not show a hook's reason
**Status:** VERIFIED
**Fact:** When a hook returns `ask`, the prompt is the ordinary "Do you want to create/make this edit to X?" with no hook reason text. Prompts from a hook `ask` and from an `Edit` ask rule look identical. With an IDE connected, the prompt opens the diff there ("Opened changes in Visual Studio Code ... Save file to continue…"), so the change can be inspected before approving. The first prompt for a folder offers "Yes, and always allow access to <folder> for this session" (but see VB-E10).
**Evidence:** T3 step 3; HV prompts 2 and 3; P2-D steps 5, 12 to 15.
**Relied on by:** README hook section; the decision log (`hook.log`) is where the reason is recorded.

### VB-E8 Claude Code asks about reads outside the working directories
**Status:** VERIFIED (the prompt); what the choices persist is DOCUMENTED by the prompt text
**Fact:** In auto mode a Read outside the working directories prompts, with options that can persist a setting (including `permissions.blockReadsOutsideWorkingDirectories`). `guard` does not manage this.
**Evidence:** HV prompt 1.
**Relied on by:** nothing; recorded because answering it can change the user's real settings.

### VB-E9 The Edit/Write tools write anywhere in a project's `.claude/`, silently, in auto mode
**Status:** VERIFIED
**Fact:** In auto mode, with no rules, the Write and Edit tools created and changed files in the project's `.claude/skills`, `.claude/agents`, `.claude/commands`, and changed the shared `.claude/settings.json`, all with no prompt. Claude Code has no built-in protection of the project's settings from the file tools (contrast Bash, VB-S15).
**Evidence:** P2-D steps 1 to 4.
**Relied on by:** the design: editing skills stays frictionless; settings files need explicit `Edit` deny rules.

### VB-E10 "Always allow for this session" does not bypass `ask`
**Status:** VERIFIED
**Fact:** After choosing "Yes, and always allow access to <folder> for this session" on a prompt caused by an `Edit` ask rule, the next edit to another file in that folder prompted again, offering only Yes and No. The same holds for a prompt caused by a hook returning `ask`. Claude Code processed the (parallel) calls one at a time, so the second check happened after the choice was made (the hook log shows the second call 40 seconds after the first).
**Evidence:** P2-D steps 12 to 15, with `hook.log` timestamps.
**Relied on by:** the design's `ask` access level and `ask` ceilings: every change needs its own approval.

---

## C. The auto-mode classifier and other prompts

### VB-C1 A server-side classifier blocks some Bash commands, unpredictably
**Status:** VERIFIED
**Fact:** In auto mode some Bash writes/deletes were blocked before running with "Blocked by auto mode / Auto mode: No reason provided" or "Permission for this action was denied by the Claude Code auto mode classifier ... Irreversible Local Destruction". It is not deterministic: `rm` and `git clean` were blocked in T1a but ran (and hit the sandbox) in T1b.
**Evidence:** B steps 3, 4; T1a steps 2, 8 vs T1b steps 2, 8.
**Relied on by:** nothing (it cannot be relied on). `views/explain` states it cannot see it.

### VB-C2 Plain `rm` is not gated by location in auto mode
**Status:** VERIFIED
**Fact:** Without the sandbox, `rm` of new and pre-existing files on `/mnt/d` and WSL home ran unprompted in auto mode. A hand-rolled `bwrap ... bash -c "... rm ..."` wrapper did prompt ("This shell -c script runs rm and could not be checked", auto-blocked after about 2 minutes); the native sandbox did not.
**Evidence:** P12 (`claude-permissions.md` 12.2, 12.3).
**Relied on by:** README "auto mode is no protection for the Windows drives".

---

## R. Permission rules

### VB-R1 `Bash(cmd *)` also matches the bare `cmd`
**Status:** VERIFIED for deny rules (default mode) and ask rules (auto mode). Allow rules are assumed to use the same matcher.
**Fact:** `Bash(uname *)`, `Bash(hostname *)` and `Bash(git stash list *)` denied the bare commands as well as ones with arguments. `Bash(pwd *)` in `ask` prompted for the bare `pwd`.
**Evidence:** T4 steps 1, 3, 7; P2-B step 12.
**Relied on by:** presets use only the `cmd *` form; `rules.bash_match`.

### VB-R2 A rule without `*` matches only the exact command
**Status:** VERIFIED
**Fact:** `Bash(whoami)` denied `whoami` but not `whoami --version` (which ran, after a normal approval prompt).
**Evidence:** T4 steps 5, 6.
**Relied on by:** `rules.bash_match`.

### VB-R3 Deny wins over allow
**Status:** VERIFIED for Edit rules and for the sandbox; precedence deny > ask > allow is DOCUMENTED
**Fact:** A path covered by both an `Edit` allow (parent folder) and an `Edit` deny (sub-path) is refused. `denyWrite` beats `allowWrite` (VB-S8). Whether ask beats allow on overlapping patterns was not tested.
**Evidence:** B steps 6 and 7; T1.
**Relied on by:** `views/explain` verdict order; the compiler's conflict checks.

### VB-R4 Rule path forms
**Status:** VERIFIED for `//abs` in Edit allow, ask and deny rules; other forms DOCUMENTED
**Fact:** `Edit(//mnt/d/x/**)` means an absolute path. Documented but untested: `~/path` (home), `/path` (relative to the settings file's source, a trap), bare `path` (relative to cwd). Sandbox paths use the standard form (`/mnt/d/x`), which differs from rule paths.
**Evidence:** every Edit rule in the tests used `//abs`.
**Relied on by:** `rules.rule_path` (the compiler always emits `//abs`); `views/explain` reports other forms as "cannot evaluate".

### VB-R5 Bash rules match text only and can be bypassed
**Status:** DOCUMENTED
**Fact:** `/bin/rm`, `sh -c '...'`, variables, wrapper runners and interpreters are not matched; compound commands (`&&`, `;`, `|`) are split and each part checked; `Bash(ls *)` does not match `lsof`; Bash rules cannot be scoped to a directory. Edit/Read deny rules apply to recognised file commands (`cat`, `sed`, `tee`) and redirection targets, not to scripts.
**Evidence:** `claude-permissions.md` section 5.
**Relied on by:** README "what does NOT protect you"; `explain` notes.

---

## H. Hooks (PreToolUse on Edit/Write)

### VB-H1 The hook runs for Write and Edit in auto mode and receives the path and mode
**Status:** VERIFIED
**Fact:** A command hook with matcher `Write|Edit|MultiEdit` is invoked for Write and Edit calls in auto mode. stdin JSON contains `tool_name`, `tool_input.file_path` and `permission_mode`. The registration form `{"type":"command","command":"<exe>","args":[...],"onFailure":"block"}` works.
**Evidence:** T3 hook log; HV hook log (Write and Edit lines).
**Relied on by:** `hook.decide`; `compile._hook_entry`.
**Re-check:** `hook-verify.md`.

### VB-H2 Hook decisions
**Status:** VERIFIED
**Fact:** No output and exit 0 means no opinion (normal rules apply). `hookSpecificOutput.permissionDecision: "deny"` blocks the call, and the model sees `PreToolUse:Write hook error: <reason>`. `"ask"` produces an ordinary approval prompt, in auto mode too (VB-E7). Output format: `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":...,"permissionDecisionReason":...}}`.
**Evidence:** T3 steps 1 to 3; HV.
**Relied on by:** `hook.run_hook`.

### VB-H3 Failure behaviour: fail-open unless `onFailure: "block"`
**Status:** VERIFIED
**Fact:** A crashing hook (exit 1, traceback) blocks the call when the entry has `"onFailure":"block"` (the model sees "blocking because onFailure is \"block\"" and the traceback). Without `onFailure` the same crash lets the write through. A hook that denies still denies in both cases.
**Evidence:** T3 step 4; T3b.
**Relied on by:** `compile._hook_entry` always sets it; `doctor` fails when the hook command is missing (every Edit/Write would block). `onFailure` needs Claude Code 2.1.295 or later (DOCUMENTED).

### VB-H4 The hook is not run for Bash
**Status:** VERIFIED
**Fact:** With the matcher limited to file tools, a Bash redirect into a hook-denied path ran unaffected.
**Evidence:** T3 step 6.
**Relied on by:** the README states the hook guards the file tools only.

### VB-H5 Deny rules are evaluated before hooks
**Status:** VERIFIED (see VB-E5)
**Fact:** Paths covered by an `Edit` deny rule never reach the hook.
**Evidence:** HV steps 5, 6 (blocked, and no entries in `hook.log` for them).
**Relied on by:** `hook.py` docstring: the hook's protected-path deny is a backstop.

### VB-H6 Exit code 2, payload `cwd`, hook command resolution
**Status:** DOCUMENTED
**Fact:** Exit code 2 from a hook blocks the call (docs). `guard` registers an absolute path to its executable so it does not depend on `PATH` (a design choice; whether hooks inherit your `PATH` was not tested). The payload `cwd` and `CLAUDE_PROJECT_DIR` are now verified: VB-H7.
**Relied on by:** `hook.decide`, `machine.hook_command`.

### VB-H7 `CLAUDE_PROJECT_DIR` and the payload `cwd` are the launch folder
**Status:** VERIFIED
**Fact:** Every hook call has `CLAUDE_PROJECT_DIR` set, equal to the payload `cwd` and the hook process's working directory: the folder Claude was started in. Started in a sub-folder of a git repo, all three are the sub-folder, not the repo root (even though the root's settings apply, VB-P5).
**Evidence:** P2-C, P2-D and P2-E `hook.log` lines.
**Relied on by:** the design's hook: the project is the git root containing `CLAUDE_PROJECT_DIR`, or `CLAUDE_PROJECT_DIR` itself.

### VB-H8 Hook decisions in every permission mode, and for NotebookEdit
**Status:** VERIFIED
**Fact:** A hook `deny` blocks and a hook `ask` prompts identically in auto, acceptEdits and default mode. The hook runs for NotebookEdit (with `tool_input.notebook_path`). A hook can refuse a Write that would overwrite an existing file while letting new-file Writes and Edits through: it runs before the write, so the file's existence is visible. There is no MultiEdit tool in Claude Code 2.1.321.
**Evidence:** P2-D steps 5 to 10 (auto), D-acceptEdits, D-default; `hook.log`.
**Relied on by:** protected paths (hook `deny`/`ask` in every mode); the `modify` access level.

---

## P. Settings, precedence and platform

### VB-P1 Settings precedence and merging
**Status:** DOCUMENTED
**Fact:** managed > `--settings` > `.claude/settings.local.json` > `.claude/settings.json` > `~/.claude/settings.json`. Permission arrays merge across files; a deny in any scope blocks. Project-level `allow` only takes effect after workspace trust.
**Relied on by:** `effective.load_effective`; `[everywhere]` compiles to user settings and each project to its local file.

### VB-P2 `defaultMode` auto/bypass is ignored from project and local settings
**Status:** DOCUMENTED (since Claude Code 2.1.257)
**Fact:** `permissions.defaultMode` values `auto` and `bypassPermissions` take effect only from user or managed settings. Tests used `--permission-mode auto` on the command line.
**Relied on by:** `doctor`'s auto-mode check reads the effective value and could report a mode that a local file cannot actually set.

### VB-P3 Managed settings
**Status:** DOCUMENTED
**Fact:** `/etc/claude-code/managed-settings.json` (and `managed-settings.d/*.json`) has the highest precedence. On WSL the user can write there, so it guards against mistakes and project overrides, not against the user. Never tested; `guard` only reads it.
**Evidence:** Claude Code docs as captured in `claude-permissions.md` section 3 (fetched 2026-11-08).

### VB-P4 The sandbox works on Windows drives mounted in WSL
**Status:** VERIFIED
**Fact:** `/mnt/d` and `/mnt/c` (drvfs/9p) behave like the WSL home for every sandbox fact above.
**Evidence:** A, D, F; P2-F (`edit` access on `/mnt/c` from a project-local file).
**Relied on by:** grants on Windows drives (ceiling `edit` in the baseline); `pathsafety` refuses whole drives.

### VB-P5 A session started in a sub-folder reads the repo root's project settings too
**Status:** VERIFIED
**Fact:** Started in `<repo>/sub`, a session applied deny rules from both `<repo>/sub/.claude/settings.local.json` and `<repo>/.claude/settings.local.json`.
**Evidence:** P2-E steps 1, 2.
**Relied on by:** the design writes project grants to the git root's local file. Open: whether folders above the git root are also read (VB-U14).

### VB-P6 `disableBypassPermissionsMode` refuses bypass silently
**Status:** VERIFIED
**Fact:** With `permissions.disableBypassPermissionsMode: "disable"` (here from a `--settings` file), both `--permission-mode bypassPermissions` and `--dangerously-skip-permissions` start Claude with no warning in the user's default mode (auto here: footer "auto mode on", a test Write labelled "Allowed by auto mode"), and shift+tab does not offer bypass mode.
**Evidence:** P2-G launch checks.
**Relied on by:** `baseline`; `doctor` should say that bypass requests fall back silently.

### VB-P7 `!` commands run outside the sandbox, with no terminal
**Status:** VERIFIED
**Fact:** A command the user types with the `!` prefix inside a session ran outside the sandbox (it wrote to a folder the sandbox makes read-only), but stdin and stdout were not terminals and it received no keyboard input (a `read` timed out). Its output goes into the conversation. Claude's own Bash calls also have no terminal, and both have `CLAUDECODE=1`.
**Evidence:** P2-G `tty-probe` run by Claude (step 3) and with `!`.
**Relied on by:** the design: `guard review` must run in a separate terminal; it cannot confirm through `!`, nor tell a `!` run from Claude's own call.

---

## U. Open questions (do not rely on these; each fails safe or is flagged)

| ID | Question | How the tool treats it |
|---|---|---|
| VB-U1 | Prefix-less (relative) sandbox paths. | `guard` always emits absolute paths. `explain` reports relative entries as unevaluable. |
| VB-U2 | Does an `Edit` **ask** rule alone (no allow) merge into `allowWrite`? T2 had explicit `allowWrite`. | `edit-ask` adds `denyWrite`, so the answer does not matter there. |
| VB-U3 | **Resolved** (P2-C): rules, hooks and `denyWrite` reload live from a project-local file (VB-S6). Still open: live reload of the user settings file, which the runbooks do not touch. | Very likely the same; `guard` does not tell the user to restart. |
| VB-U4 | **Resolved** (P2-B): `Edit(~/...)`, `Read(~/...)`, `Read(//**/.env)`, `Read(//**/.env.*)` and `Edit(//**/.claude/settings*.json)` all block the file tools. For Bash: `~/` and `//abs` forms reach the sandbox (VB-S7, VB-S13), `//**/` globs do not (VB-S14). | Globs cover the file tools; concrete paths are emitted where Bash must be stopped. |
| VB-U5 | The `lock` settings. `sandbox.enabled=true`: VERIFIED. `disableBypassPermissionsMode="disable"`: VERIFIED (VB-P6). Still open: `sandbox.allowUnsandboxedCommands=false` refusing an unsandboxed retry. The one attempt (P2-G step 2, auto mode) was stopped by the classifier first; a re-run in default mode is needed. | `guard` writes it as documented. |
| VB-U6 | **Resolved** (P2-B): a `Bash(rm *)` ask rule prompts for a sandboxed `rm` the sandbox would allow, and ask rules match the bare command (VB-R1). Tested in auto mode. | `confirm-destructive`, `git-destructive` rely on it. |
| VB-U7 | **Resolved** (P2-D): the hook fires for NotebookEdit (VB-H8). MultiEdit does not exist in 2.1.321. | `MultiEdit` stays in the matcher; harmless. |
| VB-U8 | Hook `updatedInput` schema. | Not used. |
| VB-U9 | **Resolved** (P2-A): the working directory is writable by default (VB-S4). | `explain` can drop "not tested". |
| VB-U10 | Whether `git` could run unsandboxed in a Bash-read-only folder via `sandbox.excludedCommands`. | Not used. `bash-readonly` warns that git writes fail. |
| VB-U11 | Meaning of a wildcard-free `Edit(//dir)` rule (does it cover children?). | `explain` matches such rules exactly and does not guess. v2 never relies on it: folders get `//p/**`, files `//p` (`rules.concrete_patterns`). |
| VB-U12 | Case-insensitive matching on drvfs paths. | The hook matches `/mnt/<letter>/` paths case-insensitively (errs toward blocking). |
| VB-U13 | Network restrictions, performance over 9p, macOS and native Linux. (Cache writes: resolved, VB-S16.) | Out of scope; `guard` does not manage them. |
| VB-U14 | Whether a session reads `.claude/settings*.json` from folders above the git root as well (VB-P5 shows the launch folder and the git root). | `guard` only writes to the git root's file, which is read either way. |
| VB-U15 | For a session started in a sub-folder of a repo, whether Bash can write in the rest of the repo by default, or only in the launch folder. | The hook treats the git root as the project; `explain` says Bash may be limited to the launch folder. |
| VB-U16 | Whether `allowUnsandboxedCommands=false` refuses outright or prompts (see VB-U5). | Doctor recommends it; nothing depends on the exact behaviour. |
| VB-U17 | Whether the built-in Bash protection of `.claude/` and dotfiles (VB-S15) applies only in the working directory, or also inside granted folders. | `guard` emits explicit `denyWrite` entries for settings files inside grants. |

---

## Re-verification map

| IDs | Runbook | Fixtures |
|---|---|---|
| VB-S1, S2, S3, S10, P4 | `phase0-sandbox-tests.md` session A | `scripts/phase0/setup.sh <windows user>`, `cleanup.sh` |
| VB-S7, E5, R3 | session B | same |
| VB-S6 | sessions C and E | `setup-followup.sh` for E |
| VB-S11, S12, E2 | session D, F | `setup.sh`, `setup-followup.sh` |
| VB-S8, S9, S10 | `phase0b-primitive-tests.md` T1 | `scripts/phase0/setup-b.sh`, `cleanup-b.sh` |
| VB-E3, E4, E1 | T2 | same |
| VB-H1 to H4, E6, E7 | T3, T3b | same |
| VB-R1, R2 | T4 | same |
| VB-H1 to H3 with the real registration, E5 | `hook-verify.md` | its own setup and cleanup |
| VB-S4, S13 to S16, E7, E9, E10, H7, H8, P5 to P7, R1 (ask), and the resolved U3 to U7, U9 | `phase2-verification.md` sessions A to G | `scripts/phase2/setup.sh [windows dir]`, `reload.sh`, `cleanup.sh` |
| VB-S17 | re-run the read-only probes listed in its Evidence, from a sandboxed Bash call | none |
| VB-U5 (`allowUnsandboxedCommands`), U14, U15, U17 | not yet written | a short follow-up: G2 in default mode; a session in a repo sub-folder writing to the repo root; a grant containing a `.claude/` folder |

## What each tool feature rests on

| Feature | Facts |
|---|---|
| `edit` profile | VB-S1, S7, E2, E3 |
| `edit-ask`, `bash-readonly` | VB-S8, S9, E4, S10 |
| `--protect`, self-protection | VB-S7, E5, R3 |
| `confine-edits` hook | VB-E1, H1 to H5, E7 |
| `confirm-destructive`, `git-destructive` | VB-S11, R1, U6 |
| `floor`, `secrets`, `lock` | VB-R4, S13, S14, P6, U5 (`allowUnsandboxedCommands` still open) |
| `guard explain` | VB-R1 to R5, S4, S7, S8, S13, C1 |
| `doctor` | VB-H3, P2, P6, S5 |

v2 design (`design.md`):

| Feature | Facts |
|---|---|
| "Open by default", caches, proposals in `$TMPDIR` | VB-S4, S16 |
| Access levels `full`, `edit`, `modify`, `ask`, `bash`, `deny` | VB-S1, S7, S8, S10, E3, E4, E5, E10, H8 |
| Protected paths and ceilings | VB-H2, H8, E5, E10 |
| Locked settings files | VB-E5, E9, S14, S15, U17 |
| Secrets | VB-S13, S14 |
| Hook project root | VB-H7, P5, U15 |
| No restart after apply | VB-S6 |
| `guard review` in a separate terminal | VB-P7 |
| `baseline` lock settings | VB-P6, U5 |
