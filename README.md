# claude-guard

Grant, inspect and revoke Claude Code write access to folders, using named, reusable profiles.

`guard` is a **convenience and consistency layer**. It generates and removes entries in Claude Code's own settings files (permission rules, sandbox paths). Claude Code does the enforcing. It is not a command parser and it is not a security boundary of its own: it adds no protection beyond what Claude Code's built-in features give.

What it saves you: one command to grant or revoke a folder instead of editing three or four places by hand, bundles you can reuse, a clean undo, and a readable status view.

## Quick start

```bash
cd ~/Repositories/claude-guard
uv sync
uv run guard profiles                      # what profiles exist
uv run guard allow --global lock           # turn the sandbox on, forbid unsandboxed commands
uv run guard allow --global floor          # baseline denies: sudo, rm -rf /, shell rc files, ~/.ssh, ...
uv run guard allow ~/work/myproj edit git-safe confirm-destructive \
    --protect ".env*" --dry-run                                  # preview the diff
uv run guard allow ~/work/myproj edit git-safe confirm-destructive \
    --protect ".env*"                                            # asks, then applies
uv run guard status
uv run guard doctor
uv run guard revoke ~/work/myproj
```

Every command that changes files shows a diff first and asks for confirmation (or needs `--yes`). `--dry-run` changes nothing. Grants are made by **you** running `guard`, for example with `! guard allow ...` inside a Claude session. If Claude needs access, it should tell you the exact command.

## Commands

| Command | What it does |
|---|---|
| `guard allow FOLDER [PROFILE ...]` | Grant profiles on a folder. Default profile: `edit`. Options: `--scope user\|local` (default `local`), `--project DIR`, `--protect GLOB` (repeatable), `--create`, `--force-broad`, `--no-secrets`, `--dry-run`, `--yes` |
| `guard allow --global PROFILE ...` | Folder-less grant, for example `secrets` or `lock` |
| `guard revoke FOLDER [PROFILE ...]` | Remove the named profiles, or the whole grant if none are named. `--global` for folder-less grants |
| `guard status [--json]` | Grants per settings file, protected paths, the underlying entries, and entries `guard` does not manage ("unmanaged") |
| `guard profiles` / `guard profile show NAME` | List profiles / show what one expands to |
| `guard explain PATH` / `guard explain --command CMD` | Read-only: what would happen to a path (Edit/Write tools, Bash writes) or a Bash command, and which layer decides. `--json` for scripts |
| `guard doctor [--json]` | Sanity checks, report only. Exits 1 if any check fails |

**Scopes.** `local` writes `<project>/.claude/settings.local.json` (personal, uncommitted) and is the default. `user` writes `~/.claude/settings.json`. `guard` never writes the shared, committed `.claude/settings.json`.

**Applying changes.** Claude Code watches real settings files, so a grant or revoke applies to a running session with no restart. This was verified for sandbox paths. It does **not** apply to a file passed with `--settings`, which is read once at launch.

## Profiles

Shipped in `src/claude_guard/default_profiles.toml`. Add or override your own in `~/.config/claude-guard/profiles.toml` (a profile with the same name replaces the default; profiles can `extends` others).

| Profile | Contents |
|---|---|
| `edit` | `Edit` allow for the folder, sandbox write access, `Bash` allows for `mkdir`, `touch`, `cp`, `mv` |
| `edit-ask` | `Edit` **ask** for the folder (the Edit/Write tools prompt every time, **even in auto mode**) plus `bash-readonly`. Edits need your approval and nothing can be deleted through Bash |
| `bash-readonly` | Sandbox `denyWrite` on the folder: Bash cannot write, rename or delete there. The Edit/Write tools are unaffected. Warns that Bash `git add`/`commit` fail there |
| `confine-edits` | **Hook** (global): Edit/Write calls outside the current project and every folder granted with `guard` **ask**, even in auto mode. Paths protected by a managed rule are denied. See "The Edit/Write hook" below |
| `confine-edits-strict` | Same, but outside paths are **denied** instead of asked |
| `confirm-destructive` | `rm`, `rmdir`, `unlink`, `shred` **ask** every time |
| `floor` | Global baseline denies: `sudo`, `mkfs`, `dd of=/dev/*`, `rm -rf /` and `~`; and `Edit` deny for shell startup files, `~/.ssh`, `~/.aws`, `~/.gnupg`, `/etc`, Windows and Program Files folders |
| `git-safe` | `status`, `diff`, `log`, `add`, `commit`, `restore`, `revert`, `show` |
| `git-destructive` | Allows nothing. Makes `reset --hard`, `clean`, `checkout .`, `push --force`, `branch -D` **ask** every time |
| `python` | `uv run`, `uv sync`, `pytest`, `ruff` |
| `secrets` | Denies **reading** `~/.ssh`, `~/.aws` and `.env` files. Applied automatically the first time `guard` manages a settings file; skip with `--no-secrets` |
| `lock` | Disables bypass-permissions mode, enables the sandbox, forbids unsandboxed commands |

Profiles are building blocks. `edit` (sandbox write) and `bash-readonly` (sandbox deny) cancel each other out, because deny beats allow, and `guard allow` warns when you combine them. Custom profiles can use the fields `edit_paths`, `edit_ask`, `edit_deny`, `sandbox_write`, `sandbox_deny_write`, `bash_allow/ask/deny`, `read_deny`, `protect` and `settings` (see the header of `default_profiles.toml`).

`--protect GLOB` marks folder-relative paths as never writable (an `Edit` deny rule, which Claude Code also applies to the sandbox).

## What this does and does not protect against

Read this before relying on it.

### What protects you

1. **The native sandbox (bubblewrap) confines Bash.** With the sandbox on, a Bash command can write only inside the folders you granted. This is enforced by the kernel and does not depend on how the command is spelled: `rm`, `/bin/rm`, `bash -c 'rm ...'` and `python -c 'os.remove(...)'` are all stopped outside granted folders. Verified on this machine for `/mnt/d`, `/mnt/c` and the WSL home, including paths with spaces.
2. **`Edit` allow and deny rules** decide where the built-in Edit/Write tools may write without a prompt, and which sub-paths are never writable. A deny also reaches Bash through the sandbox (verified).
3. **`Read` deny rules** (the `secrets` profile) keep keys and `.env` files out of model context and transcripts.
4. **Self-protection rules.** Every grant also adds `Edit` deny rules for the settings file `guard` manages and for `guard`'s own state directory, so the agent cannot rewrite its own policy with the Edit tool. (The sandbox already protects these from Bash.)

### What does NOT protect you

- **The Edit/Write tools are not sandboxed.** In **auto** mode they can write anywhere your user can, with no prompt, unless an `Edit` deny rule covers the path. This was observed: a Write outside every granted folder succeeded silently in auto mode. In default and acceptEdits mode they prompt for paths outside the project. A broad deny cannot be combined with allow exceptions (deny always wins), so rules alone cannot turn this into an allowlist.
- **`Bash(...)` allow rules are global, not path-scoped.** `Bash(rm *)` is allowed everywhere. It is only safe because the sandbox bounds where the command can write. **If the sandbox is off, those rules are not confined.** `guard allow` prints a warning and `guard doctor` fails in that case. Run `guard allow --global lock`.
- **Bash rules match command text and can be rephrased.** Deny and ask rules (for example `git-destructive`) are a guardrail, not a wall.
- **Reads are unrestricted** apart from the `secrets` list. Anything Claude reads enters model context and transcripts.
- **Network.** `guard` does not manage sandbox network settings.
- **A determined user.** On WSL you can write to `/etc/claude-code`, so managed settings guard against mistakes and project-level overrides, not against you.
- **Sandbox failures look like ordinary errors.** A denied write reports `Read-only file system` and never mentions the sandbox. If something unexpectedly fails, suspect the sandbox first.
- **Granted folders must exist before a session starts.** The sandbox cannot create them. Use `guard allow --create` or create them yourself.
- **Windows drives.** `guard` refuses to grant `/`, your home directory (or anything above it), any whole `/mnt/<letter>` drive, and, with no override, anything at, above or inside `/mnt/c/Windows`, `Program Files*`, `ProgramData`, `Users` and the top level of a user profile. Drive roots come from the live mount table. This is a guard against typos and over-broad grants, not a sandbox.

## `guard explain`

`guard status` lists what guard set up. `guard explain` starts from one path or command and works out the outcome:

```
$ guard explain ~/work/proj/archive/old.md
Edit/Write tools
  deny  [Claude Code rule] Edit(//home/me/work/proj/archive/**)  (local settings, guard)
  => DENIED: ...
Bash writes (sandbox)
  deny  [sandbox, kernel-enforced] denyWrite /home/me/work/proj  (local settings, guard)
  => BLOCKED: Bash cannot write here (...)

$ guard explain --command "git reset --hard"
  ask   [Claude Code rule] Bash(git reset --hard *)  (local settings, guard)
```

Each line names the layer (a Claude Code rule, the guard hook, or the kernel-enforced sandbox) and where the rule came from. Hand-written rules in your settings count too and are shown without the "guard" tag. It changes nothing.

What it cannot do: see the auto-mode classifier (which may block more than it reports), session-only permissions, or rules from other tools. Command rules match text, so a rephrased command can behave differently from what it reports. Rule path forms it cannot evaluate (relative or single-slash patterns) are listed in the notes instead of being guessed.

## The Edit/Write hook

The sandbox confines Bash only, and in auto mode the Edit and Write tools write anywhere without a prompt. Rules cannot say "everywhere except these folders" (deny beats allow), so `guard allow --global confine-edits` registers a small PreToolUse hook, `guard hook edit`, that does:

1. The path matches an `Edit` deny rule that `guard` manages (`--protect` globs, `floor`, guard's own settings and state) -> **deny**.
2. The path is inside the current project or a folder granted with `guard` -> no opinion. Normal rules and prompts apply, so `edit-ask` still asks.
3. Anything else -> **ask** (or **deny** with `confine-edits-strict`).

Properties:

- It only ever tightens. It never approves anything, so it cannot widen what Claude Code allows.
- Paths are resolved first (`..` and symlinks), so a link inside a granted folder pointing elsewhere counts as outside. Windows drive paths match case-insensitively.
- Grants only count for sessions they apply to: user-scope grants everywhere, project grants only when the session started inside that project.
- **Fail-closed.** It is registered with `"onFailure": "block"`, so a crash, bad input or unreadable state blocks the write. Without that setting a crashing hook lets the write through (verified). The price: if the `guard` executable is moved or deleted, every Edit/Write is blocked until you fix it. `guard doctor` fails in that case. The registered command is an absolute path, so it does not depend on `PATH`.
- Each decision is appended to `<state dir>/hook.log`.
- It does **not** cover Bash (the sandbox does) or Read. A different tool could write a file by another route; this guards the file tools only.

Verified in a real auto-mode session with the registration `guard` generates (`docs/hook-verify.md`, results in `docs/hook_verify_results.md`): the hook fires for both Write and Edit, paths in the project or a granted folder pass silently, and paths outside both prompt. Two observations:

- Claude Code rejects paths matching a deny rule ("File is in a directory that is denied by your permission settings") **before** any hook runs, so for protected paths the rule is what stops the write and the hook's own deny is a backstop. The hook is the only layer for the "ask outside the granted folders" case.
- The approval prompt does not show the hook's reason text, so you see a normal "Do you want to make this edit?" without the explanation. `hook.log` records what was decided.

## How it works

Each grant is a declarative record: (folder, settings file, profiles, extra protect globs). From it `guard` computes the exact settings entries it wants, then reconciles with what the grant added before:

- **Never clobbers.** Unknown keys and key order are preserved, entries are only added or removed (never whole arrays overwritten), and an entry that was already in your file is recorded as "already present" and is never removed on revoke.
- **Reference counting.** An entry shared by two grants (for example `Bash(mkdir *)`) is removed only when the last grant using it goes.
- **Clean undo.** Containers `guard` created are pruned when emptied, a settings file `guard` created is deleted when it is empty again, and scalar settings are restored to their previous values. A file in standard `json.dumps` layout round-trips byte-for-byte. A file in some other layout (inline arrays, for example) is reformatted on first write, the diff says so, and it round-trips semantically.
- **Hand edits survive.** If you removed or changed a managed entry by hand, `revoke` leaves your change alone and warns; `doctor` reports it.
- **Safe writes.** Atomic (temp file then rename), JSON validated before replacing, a timestamped backup of every changed file and the state file in `<state dir>/backups/`, and settings files are rolled back if the state file cannot be written.
- **Policy lives outside Claude's reach.** State is in `~/.config/claude-guard/` (or `$CLAUDE_GUARD_HOME`). The format is documented in the docstring of `src/claude_guard/state.py`.

Path forms differ between rules and the sandbox, and `guard` emits both: `Edit(//mnt/d/x/**)` in permission rules (`//` is absolute; a single `/` would be relative to the settings file) and `/mnt/d/x` in `sandbox.filesystem.allowWrite`.

## Status and known gaps

- Built: profiles, `allow`, `revoke`, `status`, `profiles`, `profile show`, `doctor`, `explain`, with diffs, dry-run, backups and tests.
- Built since: `edit-ask`, `bash-readonly`, `confirm-destructive`, `floor`, `confine-edits` and `confine-edits-strict` (primitives verified in `docs/phase0-findings.md`, section "Phase 0b").
- Also built: `guard explain`.
- Not built yet: a Claude Code plugin for `/guard status` (will ask first).
- Not yet verified in a real session: that the `guard`-registered hook fires for the Edit tool (run `docs/hook-verify.md`); whether `Edit(~/...)` deny patterns in `floor` match as documented (docs-based, untested); relative sandbox paths (`guard` always emits absolute ones). `Bash(cmd *)` was verified to match the bare `cmd`.

**What we know about Claude Code's behaviour, and how sure we are, is in `docs/verified-behaviour.md`**: every fact has an ID, a status (verified here, documented only, or unverified), the evidence, and which runbook re-checks it. `docs/phase0-findings.md` is the dated narrative of the experiments.

## Development

```bash
uv sync
uv run pytest            # 244 tests; uses temp dirs and a fake HOME, never your real settings
uv run ruff check . && uv run ruff format --check .
UPDATE_SNAPSHOTS=1 uv run pytest tests/test_cli.py    # refresh CLI output snapshots after reviewing
```

Layout: `paths.py` (folder safety, path forms), `profiles.py` (TOML profiles), `settingsfile.py` (non-clobbering JSON model), `state.py` (state file, atomic writes, backups), `engine.py` (plan / commit / reconcile), `settings_view.py` (effective settings, `status`, `doctor`), `hook.py` (Edit/Write hook), `explain.py`, `cli.py`.
