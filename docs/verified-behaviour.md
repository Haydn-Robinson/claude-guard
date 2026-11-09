# Verified behaviour of Claude Code (reference for claude-guard)

This is the canonical record of what we know about how Claude Code behaves, as far as it matters to `guard`. It exists so the experiments never need repeating unless the behaviour is seen to change. The narrative history is in `phase0-findings.md`; the raw evidence is in the `*_results.md` files.

## How to read this

Every fact has an ID (`VB-xx`), a status, the evidence, what in the tool relies on it, and how to re-check it. Source code and tests cite the IDs (`VB-S8` and so on), and `tests/test_docs.py` fails if a cited ID does not exist here.

| Status | Meaning |
|---|---|
| **VERIFIED** | Observed in our own tests, on the environment below. |
| **DOCUMENTED** | Stated in the Claude Code docs (captured in `claude-permissions.md`), never tested here. `guard` may rely on it, and says so. |
| **UNVERIFIED** | An open question or an assumption. `guard` avoids depending on it, or depends on it in a way that fails safe. |

Evidence names: **A, B, C, D** = `phase0-sandbox-tests.md` sessions; **E, F** = `phase0-followup-tests.md` sessions; **T1a, T1b, T2, T3, T3b, T4** = `phase0b-primitive-tests.md`; **HV** = `hook-verify.md`; **P12** = the earlier raw experiments in `claude-permissions.md` section 12. Raw results: `phase0_results.md`, `phase0_followup_results.md`, `phase0b_primitive_results.md`, `hook_verify_results.md`.

## Environment all VERIFIED facts were observed on

| | |
|---|---|
| Claude Code | 2.1.319 |
| Platform | WSL2 (Linux 6.18.54.1-microsoft-standard-WSL2), shell zsh |
| Sandbox | native (bubblewrap 0.6.1, socat installed) |
| Filesystems | WSL home (ext4); `/mnt/d` and `/mnt/c` (drvfs / 9p) |
| Permission modes | mostly **auto**; **default** and **acceptEdits** where stated |
| Date | 2026-11-08 |

## When to re-verify

Re-run the relevant runbook when: Claude Code is upgraded to a new minor version; `guard doctor` or a real session shows behaviour that contradicts this file; or you move to a platform other than WSL2 (macOS and native Linux were never tested). Update the fact's status, the environment table and the date. Section "Re-verification map" at the end says which runbook covers which IDs.

---

## S. The native sandbox (confines Bash only)

### VB-S1 `allowWrite` accepts every path form `guard` emits
**Status:** VERIFIED
**Fact:** `sandbox.filesystem.allowWrite` entries work as plain absolute paths, paths containing spaces, `//abs`, `~/x`, `/mnt/c/Users/...`, `/mnt/d/...` and WSL home paths. Writes and `rm` inside such a folder succeed.
**Evidence:** A steps 1, 2, 4, 5, 7; D; F steps 1 to 4.
**Relied on by:** `paths.sandbox_path` (emits plain absolute paths); `engine._wants`.
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

### VB-S4 The default writable set (working directory, temp dirs)
**Status:** DOCUMENTED
**Fact:** Outside `allowWrite` the sandbox allows only the working directory, `$TMPDIR`, `/tmp/claude` and a few `/dev` paths. Claude reported this list about its own sandbox in sessions C and E, but no test wrote into the working directory.
**Relied on by:** `explain` treats the project directory as writable and labels it "per docs; not tested".

### VB-S5 Granted folders must exist before the session starts
**Status:** VERIFIED
**Fact:** The sandbox cannot create a missing allowed folder; `mkdir` inside it fails with `Read-only file system` and later steps fail with `No such file or directory`.
**Evidence:** P12 (`claude-permissions.md` 12.4 item 1).
**Relied on by:** `guard allow --create`; the "does not exist" warning; `doctor` "granted folder exists".

### VB-S6 Live reload of `allowWrite` in a real settings file
**Status:** VERIFIED (for `sandbox.filesystem.allowWrite` in `.claude/settings.local.json` only)
**Fact:** Adding then removing an `allowWrite` entry in a real settings file takes effect in a running session with no restart. A file passed with `--settings` is read once at launch and never reloads (checked after a minute).
**Evidence:** E (live); C (`--settings`, never applied).
**Relied on by:** README "Applying changes"; the post-apply message. Live reload of other settings (rules, hooks, `denyWrite`, the user settings file) is not verified: see VB-U3.

### VB-S7 `Edit` rules reach the sandbox
**Status:** VERIFIED
**Fact:** An `Edit` allow rule on a folder gives Bash write access there with no `allowWrite` entry. An `Edit` deny rule on a sub-path makes Bash writes there fail (`Read-only file system`).
**Evidence:** B steps 1, 2 (allow); B step 5 (deny; steps 3 and 4 were stopped earlier by the classifier, VB-C1).
**Relied on by:** `--protect` (emits only an `Edit` deny); the `edit` profile; `explain` ("Edit deny rules also become denyWrite").
**Re-check:** session B.

### VB-S8 `denyWrite` wins over any allow, with no sub-path exception
**Status:** VERIFIED
**Fact:** With an `Edit` allow on a folder, `denyWrite` on the folder and `allowWrite` on `folder/.git`, Bash can write nowhere in the folder, including `.git`. The more specific allow does not override the deny. `git add` and `git commit` fail with `index.lock: Read-only file system`; `git clean`, `rm`, `mv` and `>` fail too. `git status` works.
**Evidence:** T1a and T1b (identical results).
**Relied on by:** `sandbox_deny_write`, profiles `bash-readonly` and `edit-ask`, and the git warning in `engine._conflicts`. Consequence: a Bash-read-only folder cannot be committed to from inside Claude.
**Re-check:** T1.

### VB-S9 The two `denyWrite` spellings behave the same
**Status:** VERIFIED
**Fact:** `denyWrite: ["<folder>"]` and `["<folder>/**"]` both make the whole folder read-only to Bash.
**Evidence:** T1a vs T1b.
**Relied on by:** `engine` emits the plain folder form.

### VB-S10 The Edit and Write tools are not sandboxed
**Status:** VERIFIED
**Fact:** `allowWrite`/`denyWrite` do not affect the Edit and Write tools: they write into a `denyWrite` folder and into a folder outside every `allowWrite`.
**Evidence:** A step 10; F step 8; T1a/T1b steps 5, 6.
**Relied on by:** the README gap section; `confine-edits`; `edit-ask`.

### VB-S11 Sandboxed Bash with literal paths is not prompted
**Status:** VERIFIED (behaviour); the setting name and default are DOCUMENTED
**Fact:** In default and acceptEdits mode, sandboxed `echo > file` and `rm` on literal paths inside allowed folders run with no prompt. `sandbox.autoAllowBashIfSandboxed` (default true) is the documented reason; we did not toggle it.
**Evidence:** F (literal paths). D's prompts all came from `$VAR` targets (VB-S12), not from the sandbox.
**Relied on by:** why `delete`/`delete-recursive` profiles were removed (they granted nothing); `confirm-destructive` (an ask rule is what makes `rm` prompt).

### VB-S12 `$VAR` in a redirect target triggers a parser prompt unrelated to the sandbox
**Status:** VERIFIED
**Fact:** `echo hi > $D/x` prompts with "Redirect target concatenation contains $/` — unanalyzable gap or substitution" in default and acceptEdits mode, whatever the sandbox allows.
**Evidence:** D (all prompts).
**Relied on by:** runbooks use literal paths only. Matters when writing any future test.

---

## E. Edit/Write tools and permission modes

### VB-E1 In auto mode the Write tool writes anywhere with no prompt
**Status:** VERIFIED
**Fact:** Unless an `Edit` deny rule matches, a Write to a path outside the project and outside every grant ran silently in auto mode.
**Evidence:** A step 10; T2 auto step 3.
**Relied on by:** the reason `confine-edits` exists; README gap section; `explain` note when no hook is registered.

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
**Relied on by:** profile `edit-ask` (`edit_ask`).

### VB-E5 An `Edit` deny rule refuses the Edit/Write tools, before any hook runs
**Status:** VERIFIED
**Fact:** Writing or editing a path covered by an `Edit` deny rule fails in auto mode with "File is in a directory that is denied by your permission settings". The PreToolUse hook never sees such calls (no hook-log entry).
**Evidence:** B step 6; HV steps 5, 6 and the hook log.
**Relied on by:** `--protect`, `floor`; the hook's own protected-path deny is only a backstop.

### VB-E6 The Edit tool (and Write over an existing file) need a prior Read
**Status:** VERIFIED
**Fact:** Edit fails with "File has not been read yet. Read it first before writing to it." if the file was not read in the session. This check happens before hooks run. Write over an existing file behaves the same.
**Evidence:** T3 step 5; T2 acceptEdits note.
**Relied on by:** runbooks (`hook-verify.md` tells Claude to Read first). Matters for any future test of Edit.

### VB-E7 The approval prompt does not show a hook's reason
**Status:** VERIFIED
**Fact:** When a hook returns `ask`, the prompt is the ordinary "Do you want to create/make this edit to X?" with no hook reason text.
**Evidence:** T3 step 3; HV prompts 2 and 3.
**Relied on by:** README hook section; the decision log (`hook.log`) is where the reason is recorded.

### VB-E8 Claude Code asks about reads outside the working directories
**Status:** VERIFIED (the prompt); what the choices persist is DOCUMENTED by the prompt text
**Fact:** In auto mode a Read outside the working directories prompts, with options that can persist a setting (including `permissions.blockReadsOutsideWorkingDirectories`). `guard` does not manage this.
**Evidence:** HV prompt 1.
**Relied on by:** nothing; recorded because answering it can change the user's real settings.

---

## C. The auto-mode classifier and other prompts

### VB-C1 A server-side classifier blocks some Bash commands, unpredictably
**Status:** VERIFIED
**Fact:** In auto mode some Bash writes/deletes were blocked before running with "Blocked by auto mode / Auto mode: No reason provided" or "Permission for this action was denied by the Claude Code auto mode classifier ... Irreversible Local Destruction". It is not deterministic: `rm` and `git clean` were blocked in T1a but ran (and hit the sandbox) in T1b.
**Evidence:** B steps 3, 4; T1a steps 2, 8 vs T1b steps 2, 8.
**Relied on by:** nothing (it cannot be relied on). `explain` states it cannot see it.

### VB-C2 Plain `rm` is not gated by location in auto mode
**Status:** VERIFIED
**Fact:** Without the sandbox, `rm` of new and pre-existing files on `/mnt/d` and WSL home ran unprompted in auto mode. A hand-rolled `bwrap ... bash -c "... rm ..."` wrapper did prompt ("This shell -c script runs rm and could not be checked", auto-blocked after about 2 minutes); the native sandbox did not.
**Evidence:** P12 (`claude-permissions.md` 12.2, 12.3).
**Relied on by:** README "auto mode is no protection for the Windows drives".

---

## R. Permission rules

### VB-R1 `Bash(cmd *)` also matches the bare `cmd`
**Status:** VERIFIED for deny rules in default mode. Ask and allow rules are assumed to use the same matcher (VB-U6).
**Fact:** `Bash(uname *)`, `Bash(hostname *)` and `Bash(git stash list *)` denied the bare commands as well as ones with arguments.
**Evidence:** T4 steps 1, 3, 7.
**Relied on by:** profiles emit only the `cmd *` form; `explain.bash_match`.

### VB-R2 A rule without `*` matches only the exact command
**Status:** VERIFIED
**Fact:** `Bash(whoami)` denied `whoami` but not `whoami --version` (which ran, after a normal approval prompt).
**Evidence:** T4 steps 5, 6.
**Relied on by:** `explain.bash_match`.

### VB-R3 Deny wins over allow
**Status:** VERIFIED for Edit rules and for the sandbox; precedence deny > ask > allow is DOCUMENTED
**Fact:** A path covered by both an `Edit` allow (parent folder) and an `Edit` deny (sub-path) is refused. `denyWrite` beats `allowWrite` (VB-S8). Whether ask beats allow on overlapping patterns was not tested.
**Evidence:** B steps 6 and 7; T1.
**Relied on by:** `--protect`; `explain` verdict order; profile conflict warning.

### VB-R4 Rule path forms
**Status:** VERIFIED for `//abs` in Edit allow, ask and deny rules; other forms DOCUMENTED
**Fact:** `Edit(//mnt/d/x/**)` means an absolute path. Documented but untested: `~/path` (home), `/path` (relative to the settings file's source, a trap), bare `path` (relative to cwd). Sandbox paths use the standard form (`/mnt/d/x`), which differs from rule paths.
**Evidence:** every Edit rule in the tests used `//abs`.
**Relied on by:** `paths.rule_path`; `floor` and `secrets` use `~/` forms (see VB-U4); `explain` reports other forms as "cannot evaluate".

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
**Relied on by:** `hook.decide`; `engine._hook_entry`.
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
**Relied on by:** `engine._hook_entry` always sets it; `doctor` fails when the hook command is missing (every Edit/Write would block). `onFailure` needs Claude Code 2.1.295 or later (DOCUMENTED).

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
**Fact:** Exit code 2 from a hook blocks the call (docs). The payload includes the session `cwd` (docs; `hook.decide` falls back to the process cwd, which is the same directory). `guard` registers an absolute path to its executable so it does not depend on `PATH` (a design choice; whether hooks inherit your `PATH` was not tested).
**Relied on by:** `hook.decide`, `cli._hook_command`.

---

## P. Settings, precedence and platform

### VB-P1 Settings precedence and merging
**Status:** DOCUMENTED
**Fact:** managed > `--settings` > `.claude/settings.local.json` > `.claude/settings.json` > `~/.claude/settings.json`. Permission arrays merge across files; a deny in any scope blocks. Project-level `allow` only takes effect after workspace trust.
**Relied on by:** `settings_view.load_effective`; scopes `local` (default) and `user`.

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
**Evidence:** A, D, F.
**Relied on by:** `PathPolicy` allows these drives, with the refusals described in the README.

---

## U. Open questions (do not rely on these; each fails safe or is flagged)

| ID | Question | How the tool treats it |
|---|---|---|
| VB-U1 | Prefix-less (relative) sandbox paths. | `guard` always emits absolute paths. `explain` reports relative entries as unevaluable. |
| VB-U2 | Does an `Edit` **ask** rule alone (no allow) merge into `allowWrite`? T2 had explicit `allowWrite`. | `edit-ask` adds `denyWrite`, so the answer does not matter there. |
| VB-U3 | Live reload of anything other than `allowWrite` in `settings.local.json`: permission rules, `denyWrite`, hooks, the user settings file. | README claims live reload only for sandbox paths. HV and T-tests set everything before launch. After `guard allow`, a restart is the safe assumption for hooks and rules. |
| VB-U4 | `Edit(~/...)` deny patterns (`floor`) and `Read(~/...)` / `Read(//**/.env)` (`secrets`) actually match. | Profiles use them as documented. No test confirmed they block. Worth a short test before relying on `floor`/`secrets`. |
| VB-U5 | The `lock` settings: `sandbox.enabled=true` is VERIFIED to enable the sandbox (every test config set it). `sandbox.allowUnsandboxedCommands=false` was present in every test config but its own effect (Claude cannot retry a blocked command outside the sandbox) was never tested. `permissions.disableBypassPermissionsMode="disable"` was never tested. | `guard` writes them as documented. |
| VB-U6 | Ask and allow `Bash(cmd *)` rules use the same bare-command matching as deny (VB-R1), and `ask` rules (`git-destructive`, `confirm-destructive`) prompt even for sandboxed commands. | Assumed; `explain` reports them as ask. |
| VB-U7 | The hook fires for `MultiEdit` and `NotebookEdit` (matcher names only). | Registered anyway; `decide` handles both tools' path keys. |
| VB-U8 | Hook `updatedInput` schema. | Not used. |
| VB-U9 | Whether the sandbox writes to the working directory by default (VB-S4). | `explain` labels it "per docs; not tested". |
| VB-U10 | Whether `git` could run unsandboxed in a Bash-read-only folder via `sandbox.excludedCommands`. | Not used. `bash-readonly` warns that git writes fail. |
| VB-U11 | Meaning of a wildcard-free `Edit(//dir)` rule (does it cover children?). | `explain` matches such rules exactly and does not guess. |
| VB-U12 | Case-insensitive matching on drvfs paths. | The hook matches `/mnt/<letter>/` paths case-insensitively (errs toward blocking). |
| VB-U13 | Network restrictions, cache writes outside allowed folders, performance over 9p, macOS and native Linux. | Out of scope; `guard` does not manage them. |

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
| VB-U4, U5, U6 | not yet written | would need a short runbook (suggested before relying on `floor`, `secrets`, `lock`) |

## What each tool feature rests on

| Feature | Facts |
|---|---|
| `edit` profile | VB-S1, S7, E2, E3 |
| `edit-ask`, `bash-readonly` | VB-S8, S9, E4, S10 |
| `--protect`, self-protection | VB-S7, E5, R3 |
| `confine-edits` hook | VB-E1, H1 to H5, E7 |
| `confirm-destructive`, `git-destructive` | VB-S11, R1, U6 |
| `floor`, `secrets`, `lock` | VB-R4, U4, U5 (documented only) |
| `guard explain` | VB-R1 to R5, S4, S7, S8, C1 |
| `doctor` | VB-H3, P2, S5 |
