# Phase 0 findings

> The canonical, maintained record of what is known (with IDs, status and re-verification runbooks) is **`verified-behaviour.md`**. This file is the dated narrative of how the findings were reached.

Environment: Claude Code 2.1.319, WSL2, bubblewrap 0.6.1. Tested 2026-11-08 using the runbooks `phase0-sandbox-tests.md` and `phase0-followup-tests.md`. Raw results: `phase0_results.md`, `phase0_followup_results.md`.

## Resolved items from handover section 2

| # | Item | Result | Evidence |
|---|---|---|---|
| 1 | Auto-approval of sandboxed Bash | `sandbox.autoAllowBashIfSandboxed`, default `true`. Sandboxed writes, `rm` and `bash -c rm` run with no prompts in default and acceptEdits mode, **when the redirect target is a literal path** | Test F. Prompts seen in the first Session D came only from `$VAR` in redirect targets ("Redirect target concatenation contains `$`"), a command-parser gate |
| 2 | Rules vs sandbox merge | `Edit` allow gives Bash write access with no `allowWrite`. `Edit` deny blocks Bash writes (sandbox reports `Read-only file system`) | Session B steps 1, 2, 5 |
| 3 | Mid-session changes | **Applied live** when the change is made in a real settings file (`.claude/settings.local.json`), for both grant and revoke. **Not** applied for a file passed via `--settings` | Test E (live), Session C (`--settings`, never applied, even after a minute) |
| 4 | Path semantics | Plain `/abs`, `//abs`, `~/x`, paths containing spaces and `/mnt/c` all work in `allowWrite`. Prefix-less relative paths were not tested, so `guard` always emits absolute paths | Session A steps 1 to 8 |
| 5 | Hook `updatedInput` schema | Still unverified. Only relevant if the Phase 3 hook is built | n/a |

## Other facts established

- `/mnt/c` behaves the same as `/mnt/d` under the sandbox.
- `bash -c "rm ..."` outside the grants is blocked by the sandbox (Session A step 9, Test F step 6).
- **The Edit/Write tools are not confined by `allowWrite`.**
  - In **auto** mode the Write tool wrote outside every grant with no prompt (Session A step 10).
  - In default and acceptEdits mode it prompts for any path outside the project, granted or not (Test F). `Edit` allow rules are what remove those prompts for granted folders.
  - Consequence: in auto mode the only guard on Edit/Write is explicit `Edit` deny rules. A broad deny cannot be combined with allow exceptions, because deny always wins.
- Auto mode has a server-side classifier that blocked some Bash commands against a denied path with "No reason provided" (Session B steps 3, 4). It is an extra layer that cannot be relied on and gives no explanation.
- Sandbox denials look like plain `Read-only file system` errors and never mention the sandbox.
- `--settings <file>` is read once at launch. It is fine for tests but is not live.

## Design decisions that follow

1. Emit both the `Edit` rules and explicit `sandbox.filesystem.allowWrite` entries, so the settings stay readable and do not depend on the merge behaviour.
2. Protect globs become `Edit` deny rules only. The merge into the sandbox is confirmed by Session B step 5. Explicit `denyWrite` entries are not emitted, because they were never tested.
3. Emit absolute paths in two forms: `//abs` for rules and `/abs` for sandbox entries.
4. `guard allow` and `guard revoke` take effect in running sessions with no restart.
5. State plainly, in the README and in `guard allow` output, that Edit/Write are not confined by the sandbox. `guard doctor` warns when `defaultMode` is `auto` and no deny rules are present.
6. For Phase 3, a deny list of sensitive paths is the rules-only option. A small PreToolUse hook that checks `file_path` for Edit and Write calls against the grant list is the robust one, because the path is structured and needs no shell parsing. It is the user's decision (open question 5).

## Phase 0b findings (primitives T1 to T4)

Raw results: `phase0b_primitive_results.md`. Runbook: `phase0b-primitive-tests.md`.

| Test | Result |
|---|---|
| T1a, T1b | `denyWrite` on a folder makes **Bash** read-only there (writes, `rm`, `mv`, `git clean`), in both the plain and `/**` forms. An `allowWrite` on `folder/.git` does **not** carve an exception: deny wins over a more specific allow, so `git add` and `git commit` fail with `index.lock: Read-only file system`. The Edit and Write tools still work in the folder (they are not sandboxed). |
| T2 | An `Edit` **ask** rule prompts for the Write tool **in auto mode** and in acceptEdits mode. It does not affect Bash redirects. In auto mode a Write to a path with no rule at all is silent. In acceptEdits a path outside the project prompts. |
| T3 | A PreToolUse hook on `Write` runs in auto mode and receives the tool, `file_path` and `permission_mode`. `deny` blocks with `PreToolUse:Write hook error: <reason>`. `ask` produces a normal approval prompt in auto mode (the user saw it; the model's report of "no prompt" was wrong because the model cannot see prompts). A **crash with `onFailure: "block"` blocks** the write (fail-closed). **Without `onFailure` a crash lets the write through** (fail-open, T3b). The hook is not run for Bash. |
| T4 | `Bash(cmd *)` **does match the bare `cmd`** (`uname`, `hostname`, `git stash list` were denied). An exact rule without `*` (`Bash(whoami)`) does not match `whoami --version`. |

Other observations:

- In auto mode the classifier blocked `rm` and `git clean` as "Irreversible Local Destruction" in T1a (not in T1b). It is an unreliable extra layer.
- The hook was not exercised for the **Edit** tool: the Edit was rejected earlier by "File has not been read yet", before any hook ran. Needs a short re-test (Read first, then Edit).
- Whether an `Edit` **ask** rule alone (without allow) merges into the sandbox `allowWrite` is untested. The T2 settings granted `allowWrite` explicitly.

### Consequences for the design

1. **"Edit with approval, no deletes" is achievable without parsing commands:** `Edit(folder/**)` **ask** (prompts even in auto mode), plus `denyWrite` on the folder (Bash cannot write or delete there), plus `Edit` deny on protected globs. The Edit and Write tools cannot delete files.
2. **The cost is git:** under `denyWrite`, Bash cannot run `git add` or `git commit` in that folder, and there is no `.git` exception. Commits must be made by the user, or the folder must not be Bash-read-only.
3. **The hook is worth building (built as `guard hook edit`, profiles `confine-edits[-strict]`):** it is the only way to get "ask or deny for Edit/Write outside the granted folders" in auto mode. It must be registered with `onFailure: "block"`.
4. **Profiles only need the `cmd *` form** for ask and deny rules. The duplicate bare forms in `git-destructive` can go.
5. `edit` bundles an Edit allow (which grants Bash write access) with Bash allows, so it cannot be combined with a Bash-read-only folder. The primitives need to be separate, composable profiles.

## Hook verification in a real session (`hook_verify_results.md`)

Registration generated by `guard allow --global confine-edits`, auto mode:

- The hook fired for **Edit** and **Write**. The log shows the decision for each call: `none` for the project and the granted folder, `ask` for the outside path (Edit and Write), and the two matching prompts appeared.
- Protected paths (Edit deny rule from `--protect`) were refused by Claude Code itself with "File is in a directory that is denied by your permission settings". The hook log has **no entry** for them, so deny rules are evaluated before PreToolUse hooks. The hook's deny branch is a backstop only.
- The approval prompt for an `ask` does not display the hook's reason text.
- An unrelated Claude Code prompt appeared for the Read tool outside the working directories ("Read outside the working directories"), with options that persist a setting (`permissions.blockReadsOutsideWorkingDirectories` or an allow-all). It is not caused by guard, but answering it can change your user settings.

## Still unverified

- The hook `updatedInput` schema (not needed by the hook as built).
- Behaviour with prefix-less (relative) sandbox paths.
- Whether `excludedCommands` could let `git` run unsandboxed in a Bash-read-only folder.
