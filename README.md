# claude-guard

Grant, inspect and revoke Claude Code's write access to folders, with one command each, using named, reusable **profiles**.

Claude Code runs commands and edits files as your user. A rare but serious failure is an agent destroying data outside the task it was given, for example an `rm -rf "$DIR/"` where `$DIR` turned out to be empty. Claude Code has the controls to prevent this (a kernel-enforced sandbox for Bash, permission rules for its file tools, hooks), but setting them up by hand means editing several places in several JSON files, and undoing it cleanly is harder still. `guard` does that for you:

- one command to grant a folder, and one to revoke it, with a diff you approve first;
- reusable bundles (profiles) such as "may edit but not delete" or "ask before every change";
- a clean undo that never touches entries you wrote yourself;
- read-only views of what is in force (`status`), of what would happen to a path or command (`explain`), and a health check (`doctor`).

`guard` writes Claude Code's own settings files; **Claude Code does the enforcing.** `guard` is not a security boundary of its own and adds no protection beyond Claude Code's built-in features. Read [What this protects against](#what-this-protects-against-and-what-it-does-not) before relying on it.

> **Status.** This README describes the current CLI (v1), which works today. A redesign (v2: one `policy.toml` compiled into settings) is being built alongside it; see [What's next: v2](#whats-next-v2).

## Contents

1. [Install](#install)
2. [Concepts in two minutes](#concepts-in-two-minutes)
3. [First-time setup](#first-time-setup)
4. [Everyday tasks](#everyday-tasks)
5. [Checking what is in force](#checking-what-is-in-force)
6. [Troubleshooting](#troubleshooting)
7. [Command reference](#command-reference)
8. [Profiles](#profiles)
9. [The Edit/Write hook](#the-editwrite-hook)
10. [What this protects against, and what it does not](#what-this-protects-against-and-what-it-does-not)
11. [How it works](#how-it-works)
12. [Removing claude-guard](#removing-claude-guard)
13. [What's next: v2](#whats-next-v2)
14. [Development](#development)

## Install

Requirements: Python 3.15+ and [uv](https://docs.astral.sh/uv/). For the sandbox, Claude Code needs `bubblewrap` and `socat` on Linux and WSL2 (`sudo apt install bubblewrap socat`). Only WSL2 has been tested.

Install `guard` as a tool, outside any project:

```bash
uv tool install /path/to/claude-guard
guard --help
```

Install it this way rather than running it with `uv run` from the repository, because `guard` registers its own absolute path in Claude Code's settings when you use the hook (`confine-edits`). An install under `~/.local/` is outside every project, so Claude cannot edit the hook's code. After pulling changes, reinstall with `uv tool install --reinstall /path/to/claude-guard`.

## Concepts in two minutes

**Two kinds of write, two mechanisms.** Claude writes files in two ways, and Claude Code controls them separately:

| Write path | Controlled by | Notes |
|---|---|---|
| **Bash** commands (`rm`, `mv`, `>`, scripts, build tools) | the **sandbox** (bubblewrap, kernel-enforced) | Bash can write only in the working directory, `$TMPDIR` and folders you grant. How the command is spelled does not matter |
| The **Edit/Write tools** (and NotebookEdit) | **permission rules** (`Edit(...)` allow / ask / deny) and **hooks** | Not sandboxed. In auto mode they write anywhere without a prompt unless a rule or hook stops them |

Most profiles set both. When something "should" work but does not, it is usually because one write path is allowed and the other is not.

**Settings files and scopes.** `guard` writes to one of two files:

| `--scope` | File | Applies to |
|---|---|---|
| `local` (default) | `<project>/.claude/settings.local.json` | sessions in that project only; personal, not committed |
| `user` | `~/.claude/settings.json` | every session |

`guard` never writes the shared, committed `.claude/settings.json`, or the managed settings in `/etc/claude-code`. The project is the current directory, or `--project DIR`.

**Grants and profiles.** A *grant* is a folder plus one or more *profiles* in one settings file. A profile is a bundle of settings entries (for example `edit`: Edit allow plus sandbox write). Some profiles need no folder (`lock`, `floor`, `secrets`, `confine-edits`); grant those with `--global`. Note that `--global` means *no folder*, not *all sessions*: the scope still decides which file is written, so a folder-less profile meant for every session needs `--scope user`.

**Changes apply live.** Claude Code reloads its settings files in a running session within about 15 seconds; no restart needed. This was verified for a project's local file and is expected for the user file too. It does not apply to a file passed with `--settings`, which is read once at launch.

## First-time setup

Recommended once, for every session (`--scope user`):

```bash
guard allow --global lock floor secrets confine-edits --scope user --dry-run   # review the diff
guard allow --global lock floor secrets confine-edits --scope user             # asks, then applies
guard doctor
```

What that sets up:

- `lock`: turns the sandbox on, forbids running commands outside it, and disables bypass-permissions mode. A request for bypass mode then starts Claude in your default mode, silently, with no warning.
- `floor`: denies `sudo` and a few catastrophic commands, and makes shell startup files, `~/.ssh`, `~/.aws`, `~/.gnupg`, `/etc` and the Windows system folders unwritable.
- `secrets`: Claude cannot read `~/.ssh`, `~/.aws` or `.env` files with its Read tool.
- `confine-edits`: the Edit/Write tools **ask** before writing anywhere outside the current project and the folders you granted, even in auto mode.

`doctor` should then report no failures. If `bwrap installed` fails, install bubblewrap and socat.

From then on, Claude can write freely in the project it was started in, and needs your approval (or a grant) anywhere else.

## Everyday tasks

Every command that changes files prints a diff and asks `Apply these changes? [y/N]`. Add `--dry-run` to only see the diff, or `--yes` to skip the question.

### Let Claude work in a folder outside the project

```bash
cd ~/repos/myproj
guard allow ~/data/myproj-output            # default profile: edit
```

`edit` gives the Edit/Write tools and Bash write access to the folder, for sessions in `~/repos/myproj` only. Add `--scope user` to grant it for every session. Add `--create` if the folder does not exist yet: the sandbox cannot create a granted folder, so it must exist before writes there can work.

### Let Claude edit files, but never delete or rename them

```bash
guard allow /mnt/d/notes edit bash-readonly
```

`bash-readonly` makes the folder read-only to Bash, so nothing can be deleted, moved or overwritten by a command. The Edit/Write tools still work: they can create and change files, but they cannot delete or rename. Git cannot write there either (`git add` and `git commit` fail with `Read-only file system`), so commit from your own terminal.

`guard` warns that `edit` and `bash-readonly` together leave Bash read-only: the deny wins over `edit`'s sandbox write, which is the point here.

### Require approval for every change in a folder

```bash
guard allow ~/Documents/important edit-ask
```

The Edit/Write tools prompt for each change, even in auto mode, and "always allow for this session" does not switch the prompts off. With an IDE connected, the prompt opens the diff there. Bash is read-only in the folder.

### Keep some files in a granted folder untouchable

```bash
guard allow ~/repos/site edit --protect ".env*" --protect "deploy/**"
```

Each `--protect` glob (relative to the folder) becomes an `Edit` deny rule. It always stops the Edit/Write tools. Whether it also stops Bash depends on the pattern: a concrete path like `deploy/keys.json` reaches the sandbox, but a wildcard may not. For files that must survive Bash too, combine with `bash-readonly`.

### Ask before any delete

```bash
guard allow --global confirm-destructive --scope user
guard allow --global git-destructive --scope user
```

`rm`, `rmdir`, `unlink`, `shred` and destructive git commands (`reset --hard`, `clean`, `checkout .`, `push --force`, `branch -D`) then prompt every time. These rules match command text, so they are a guardrail, not a wall; the sandbox is what actually limits where a delete can land.

### Let build tools write their caches

Tool caches (uv, npm, pip, ...) are outside the project, so the sandbox makes them read-only, and for example `uv sync` fails with `Read-only file system (os error 30)`. Give Bash, but not the file tools, write access to the cache with a custom profile in `~/.config/claude-guard/profiles.toml`:

```toml
[cache]
description = "Bash may write here; the Edit/Write tools get nothing"
sandbox_write = true
```

```bash
guard allow "$(uv cache dir)" cache --scope user
```

### Remove a grant

```bash
guard revoke ~/data/myproj-output                 # the whole grant
guard revoke /mnt/d/notes bash-readonly           # just one profile
guard revoke --global confine-edits --scope user  # a folder-less profile
```

Use the same `--scope` (and `--project`) as when you granted it. `guard status` shows where each grant lives.

### Granting from inside a Claude session

Grants are made by **you**, never by Claude. If Claude needs access, it should tell you the exact `guard` command. You can run it in a separate terminal (recommended: you get the diff and the confirmation question), or inside the session with the `!` prefix. A `!` command runs outside the sandbox but has no terminal and cannot answer the confirmation question, so review first, then apply:

```text
! guard allow ~/data/out --dry-run
! guard allow ~/data/out --yes
```

## Checking what is in force

```bash
guard status                 # grants per settings file, their entries, and entries guard does not manage
guard explain ~/data/out/report.md
guard explain --command "git reset --hard"
guard doctor
```

**`guard status`** lists, for the user file and the current project's local file, each grant with its profiles and the exact entries it added. Entries you wrote by hand are listed separately as "unmanaged". `--json` for scripts.

**`guard explain PATH`** works out what happens to one path, for the Edit/Write tools and for Bash, and which layer decides:

```text
$ guard explain ~/work/proj/archive/old.md
Edit/Write tools
  deny  [Claude Code rule] Edit(//home/me/work/proj/archive/**)  (local settings, guard)
  => DENIED: ...
Bash writes (sandbox)
  deny  [sandbox, kernel-enforced] denyWrite /home/me/work/proj  (local settings, guard)
  => BLOCKED: Bash cannot write here (...)
```

**`guard explain --command CMD`** shows which `Bash(...)` rules match a command. Hand-written rules count too and are shown without the "guard" tag. `explain` cannot see the auto-mode classifier or permissions granted for one session only, and since command rules match text, a rephrased command can behave differently.

**`guard doctor`** checks that bubblewrap and socat are installed, every settings file parses, the sandbox is on, unsandboxed commands and bypass mode are off, the hook (if registered) points at an existing `guard`, guard's records are consistent, the entries it manages are still in the files, and granted folders exist. It changes nothing, and exits 1 if any check fails.

## Troubleshooting

**`Read-only file system` (or `permission denied`) from a Bash command.** The sandbox blocked a write; it never names itself. Run `guard explain <path>`. Usual causes: the folder is not granted; it is granted with `bash-readonly` or `edit-ask`; it is a tool cache (see [caches](#let-build-tools-write-their-caches)); or it is a protected file inside the project (Claude Code keeps Bash out of the project's `.claude/` folder, `.mcp.json` and shell and git dotfiles).

**`No such file or directory` for a file that exists.** A `Read` deny rule hides the file from Bash, too (for example `~/.ssh` with `secrets`).

**A grant does not work yet.** The folder must exist (`--create`). Changes reach a running session within about 15 seconds. A file passed to `claude --settings` is never reloaded.

**`git add` / `git commit` fail in a folder.** The folder is read-only to Bash (`bash-readonly`, `edit-ask`). This is by design: commit from your own terminal.

**Every Edit/Write is blocked with a hook error.** The hook is fail-closed: if the `guard` it points at was moved or uninstalled, every file-tool write is refused. `guard doctor` says so. Reinstall `guard`, or remove the hook with `guard revoke --global confine-edits --scope user` (run it in a terminal; Claude's Edit tool cannot fix this).

**A prompt appears for a write outside the project, with no explanation.** That is the `confine-edits` hook asking. The prompt does not show its reason; `~/.config/claude-guard/hook.log` records each decision.

**Bypass mode does not start.** `lock` disables it. Claude starts in your default mode without saying so.

**`guard` says "not a terminal: refusing to apply without --yes".** You ran it through `!` or a script. Review with `--dry-run`, then apply with `--yes`.

**`refusing to grant ...`.** `guard` refuses `/`, your home folder and anything above it, a whole drive (`/mnt/<letter>`), the Windows system folders, and `C:\Users` or the top level of a Windows user profile. A shallow folder such as `/srv` needs `--force-broad`. Grant a specific folder instead.

## Command reference

| Command | What it does |
|---|---|
| `guard allow FOLDER [PROFILE ...]` | Grant profiles on a folder (default profile: `edit`). The first grant in a settings file also adds `secrets` unless you pass `--no-secrets` |
| `guard allow --global PROFILE ...` | Grant folder-less profiles (`lock`, `floor`, `secrets`, `confine-edits`, `confirm-destructive`, ...) |
| `guard revoke FOLDER [PROFILE ...]` | Remove the named profiles, or the whole grant if none are named. `--global` for folder-less profiles |
| `guard status [--json]` | Grants per settings file, their entries, and unmanaged entries |
| `guard explain PATH` / `guard explain --command CMD` | What would happen to a path or Bash command, and which layer decides. `--json` |
| `guard doctor [--json]` | Health checks; exit 1 on failure |
| `guard profiles` / `guard profile show NAME` | List profiles / show exactly what one expands to |
| `guard hook edit` | Run by Claude Code for the hook; not for manual use |

Options:

| Option | Commands | Meaning |
|---|---|---|
| `--scope user\|local` | allow, revoke | Which settings file (default `local`) |
| `--project DIR` | all but profiles | The project (default: the current directory) |
| `--global` | allow, revoke | The arguments are profile names, with no folder |
| `--dry-run` | allow, revoke | Show the diff; change nothing |
| `--yes`, `-y` | allow, revoke | Apply without asking (required without a terminal) |
| `--protect GLOB` | allow | Folder-relative path that must never be writable by the Edit/Write tools (repeatable) |
| `--create` | allow | Create the folder if it does not exist |
| `--force-broad` | allow | Allow a shallow folder such as `/srv` |
| `--no-secrets` | allow | Do not add `secrets` on the first grant in a file |

Exit codes: 0 success, 1 declined or a doctor failure, 2 error (the message starts with `error:`).

Environment: `CLAUDE_GUARD_HOME` sets guard's own folder (default `$XDG_CONFIG_HOME/claude-guard`, normally `~/.config/claude-guard`).

## Profiles

Shipped in `src/claude_guard/default_profiles.toml`. `guard profile show NAME` prints exactly what one adds.

| Profile | Needs a folder | Contents |
|---|---|---|
| `edit` | yes | `Edit` allow for the folder, sandbox write access, and `Bash` allows for `mkdir`, `touch`, `cp`, `mv` |
| `bash-readonly` | yes | Sandbox `denyWrite`: Bash cannot write, rename or delete in the folder. The Edit/Write tools are unaffected |
| `edit-ask` | yes | `Edit` **ask** (prompt every time, even in auto mode) plus `bash-readonly` |
| `confine-edits` | no | The [hook](#the-editwrite-hook): Edit/Write outside the project and every granted folder **ask** |
| `confine-edits-strict` | no | Same, but **deny** |
| `confirm-destructive` | no | `rm`, `rmdir`, `unlink`, `shred` ask every time |
| `git-safe` | no | Allows `git status`, `diff`, `log`, `add`, `commit`, `restore`, `revert`, `show` |
| `git-destructive` | no | Allows nothing; `reset --hard`, `clean`, `checkout .`, `push --force`, `branch -D` ask every time |
| `python` | no | Allows `uv run`, `uv sync`, `pytest`, `ruff` |
| `floor` | no | Denies `sudo`, `mkfs`, `dd of=/dev/*`, `rm -rf /` and `~`; `Edit` deny for shell startup files, `~/.ssh`, `~/.aws`, `~/.gnupg`, `/etc` and the Windows system folders |
| `secrets` | no | `Read` deny for `~/.ssh`, `~/.aws` and `.env` files |
| `lock` | no | `sandbox.enabled: true`, `sandbox.allowUnsandboxedCommands: false`, bypass-permissions mode disabled |

Profiles combine. Remember that deny beats allow: `edit` plus `bash-readonly` leaves Bash read-only, and `guard` warns when you combine them.

`Bash(...)` allow rules (in `edit`, `git-safe`, `python`) are **global**, not limited to the folder: `Bash(mv *)` allows `mv` everywhere. That is only safe because the sandbox limits where the command can write. `guard allow` warns, and `doctor` fails, when the sandbox is off.

### Your own profiles

Put them in `~/.config/claude-guard/profiles.toml`. A profile with the same name as a shipped one replaces it, and a profile can `extends` others:

```toml
[notes]
description = "My notes: edits ask, Bash read-only, no edits to the archive"
extends = ["edit-ask"]
protect = ["archive/**"]

[node]
description = "npm and node tooling"
bash_allow = ["Bash(npm run *)", "Bash(npx *)"]
```

Fields (all optional):

| Field | Becomes |
|---|---|
| `description`, `warning`, `extends` | text shown by `guard profiles`; a warning printed when granted; profiles to include |
| `edit_paths` / `edit_ask` | `Edit(...)` allow / ask rules; templates starting with `{folder}`, for example `"{folder}/**"` |
| `edit_deny` | global `Edit(...)` deny patterns, for example `"~/.bashrc"` or `"//etc/**"` |
| `protect` | folder-relative globs, turned into `Edit` deny rules |
| `sandbox_write` / `sandbox_deny_write` | the folder in `sandbox.filesystem.allowWrite` / `denyWrite` |
| `bash_allow` / `bash_ask` / `bash_deny` | full `Bash(...)` rule strings (global) |
| `read_deny` | `Read(...)` deny patterns (global) |
| `edit_hook` | `"ask"` or `"deny"`: register the hook |
| `settings` | scalar settings: `[{ path = "a.b.c", value = ... }]` |

Rule paths: `//abs/path` is absolute and `~/path` is in your home. A single leading `/` would be relative to the settings file, so do not use it.

## The Edit/Write hook

The sandbox confines Bash only, and in auto mode the Edit/Write tools write anywhere without a prompt. Rules cannot express "everywhere except these folders", because deny beats allow. So `confine-edits` registers a small PreToolUse hook (`guard hook edit`) that decides, for each Edit/Write/NotebookEdit call:

1. The path matches an `Edit` deny rule that `guard` manages (`--protect`, `floor`, guard's own files) -> **deny**.
2. The path is inside the current project or a folder granted with `guard` -> no opinion: normal rules and prompts apply, so `edit-ask` still asks.
3. Anything else -> **ask** (`confine-edits`) or **deny** (`confine-edits-strict`).

Properties:

- **It never approves**, so it can only tighten what Claude Code allows.
- **Same in every mode.** It asks or denies in auto, acceptEdits and default mode alike.
- Paths are resolved first (`..`, symlinks), so a symlink inside a granted folder that points elsewhere counts as outside. Windows drive paths are matched case-insensitively.
- A grant counts only where it applies: user-scope grants everywhere, project grants only in sessions started inside that project. "The project" is the folder Claude was started in, so start Claude at the repository root.
- **Fail-closed.** It is registered with `"onFailure": "block"`: a crash blocks the write instead of letting it through. If the `guard` executable disappears, every Edit/Write is blocked until you reinstall or revoke (`doctor` fails in that case).
- Paths covered by a deny rule are refused by Claude Code before the hook runs, so for those the hook is a backstop.
- The prompt does not show the hook's reason; each decision is appended to `~/.config/claude-guard/hook.log`.
- It does not cover Bash (the sandbox does) or reads.

## What this protects against, and what it does not

### What protects you

1. **The sandbox confines Bash**, in the kernel. With it on, a command can write only in the working directory, `$TMPDIR` (`/tmp/claude-<uid>`) and granted folders, however it is written: `rm`, `/bin/rm`, `bash -c '...'`, a script. Everything else is read-only. Inside the working directory, Claude Code also keeps Bash out of `.claude/`, `.mcp.json` and shell and git dotfiles.
2. **`Edit` rules** decide where the Edit/Write tools may write without a prompt (allow), with a prompt (ask) or not at all (deny). Deny and allow rules with concrete paths also reach the sandbox.
3. **The hook** (`confine-edits`) makes the Edit/Write tools ask (or refuse) outside the project and granted folders.
4. **`Read` deny rules** (`secrets`) keep keys out of the model's context. `~/` paths are hidden from Bash too.
5. **Self-protection.** Every grant also adds `Edit` deny rules for the settings file it manages and for guard's own folder, so Claude cannot rewrite its permissions with the Edit tool.

### What it does not protect against

- **The Edit/Write tools without the hook.** In auto mode they write anywhere your user can, unless a deny rule covers the path. Use `confine-edits`.
- **Wildcard deny rules and Bash.** A `//**/` pattern (such as `Read(//**/.env)` in `secrets`) stops only the file tools: Bash can still `cat .env` in the project. Only concrete paths reach the sandbox.
- **Command rules are text matching.** `Bash(rm *)` does not match `/bin/rm`, `sh -c 'rm ...'` or a script that deletes. Ask and deny rules for commands are a guardrail, not a wall. Allow rules are global.
- **No sandbox, no confinement.** If the sandbox is off, nothing confines Bash and the global allow rules apply everywhere. Use `lock`; `doctor` fails without it.
- **Reads** are unrestricted apart from the `secrets` list, and anything read enters the model's context and transcripts.
- **Network access** is not managed.
- **MCP tools** are neither sandboxed nor covered by the hook.
- **The project's own `.claude/` folder.** The Edit/Write tools can change it, including its settings and hooks, without a prompt in auto mode.
- **You.** Managed settings in `/etc/claude-code` are writable by you on WSL; they protect against mistakes and project overrides, not against a determined user.
- **Grants are coarse on Windows drives.** `guard` refuses whole drives and the Windows system area to catch typos and over-broad grants; it is not a sandbox of its own.

What is known about Claude Code's behaviour, and how sure we are of each point, is recorded in [`docs/verified-behaviour.md`](docs/verified-behaviour.md): every fact has an ID, a status (verified here, documented only, or open), the evidence, and the runbook that re-checks it.

## How it works

A grant is a declarative record: folder, settings file, profiles and extra protect globs. From it, `guard` computes the exact settings entries it wants, then reconciles them with what the grant added before:

- **Never clobbers.** Unknown keys and key order are preserved, entries are added or removed one by one (arrays are never overwritten), and an entry that was already in your file is recorded as pre-existing and never removed.
- **Shared entries are reference counted.** An entry used by two grants (for example `Bash(mkdir *)`) goes only when the last grant using it is revoked.
- **Clean undo.** Containers `guard` created are removed when empty, a settings file `guard` created is deleted when empty again, and scalar settings get their previous value back. A file in standard `json.dumps` layout round-trips byte for byte; one in another layout is reformatted on the first write (the diff says so).
- **Hand edits survive.** If you changed or removed a managed entry by hand, `revoke` leaves your change alone and warns; `doctor` reports it.
- **Safe writes.** Atomic (temporary file, then rename), JSON validated first, a timestamped backup of every changed file, and settings files rolled back if guard's own record cannot be written.

guard's own folder (`~/.config/claude-guard/`) holds `state.json` (what each grant added; format in `src/claude_guard/state.py`), `profiles.toml` (your profiles), `backups/` and `hook.log`.

Path forms differ between rules and the sandbox, and `guard` emits the right one for each: `Edit(//mnt/d/x/**)` in permission rules, `/mnt/d/x` in `sandbox.filesystem.allowWrite`.

## Removing claude-guard

Revoke every grant `guard status` lists (run it in each project with local grants), with the same `--scope` and `--project` it was made with:

```bash
guard status
guard revoke ~/data/myproj-output
guard revoke --global lock floor secrets confine-edits --scope user
guard status            # should list no grants
uv tool uninstall claude-guard
```

Revoke `confine-edits` before uninstalling: the hook is fail-closed, so a registered hook without `guard` blocks every Edit/Write. Backups of every file `guard` changed remain in `~/.config/claude-guard/backups/`.

## What's next: v2

v2 replaces profiles with one human-readable policy file, `~/.config/claude-guard/policy.toml`, compiled into the fewest settings entries that enforce it. The design is in [`docs/design.md`](docs/design.md). In short:

- **Open, closed, protected.** Reads and writes in the project are open by default; writes elsewhere are closed but grantable; important paths are protected with a per-path **ceiling** on what any grant may give (Windows system folders: never; `/etc`, credentials and shell files: ask at most; Windows drives: edit at most).
- **Access levels instead of profiles:** `full`, `bash`, `edit`, `modify` (edit existing files only), `ask`, `deny`.
- **Project grants by default**, `--everywhere` to opt in.
- **Claude Code settings are locked**: Claude proposes a change with `guard propose`, you approve it with `guard review` in a separate terminal.
- High-level commands (`init`, `allow`, `revoke`, `protect`, `status`, `explain`, `doctor`) that edit the policy, and low-level ones (`edit`, `plan`, `apply`) for the file itself.

Built so far: the core (policy parsing and validation, presets, the compiler, plan and apply). Not yet: the v2 hook, propose and review, and the CLI. Until then, use the v1 commands above.

## Development

```bash
uv sync
uv run pytest                                          # temp dirs and a fake HOME; never your real settings
uv run ruff check . && uv run ruff format --check .
UPDATE_SNAPSHOTS=1 uv run pytest tests/test_cli.py     # refresh output snapshots, after reviewing them
```

Rules for contributors are in [`CLAUDE.md`](CLAUDE.md): read `docs/verified-behaviour.md` before proposing an experiment, cite its fact IDs in code that depends on Claude Code's behaviour, write settings-mutation code test-first, and never touch real settings in tests.

Layout (`src/claude_guard/`):

| Module | v1 / v2 | Role |
|---|---|---|
| `pathsafety.py` | both | which folders may be granted; drive detection |
| `rules.py` | both | rule and sandbox path forms, glob matching |
| `settingsfile.py` | both | non-clobbering JSON settings document |
| `profiles.py`, `default_profiles.toml` | v1 | profiles |
| `state.py`, `engine.py` | v1 | grant records; plan, reconcile and commit |
| `settings_view.py`, `explain.py`, `hook.py`, `cli.py` | v1 | status and doctor, explain, the hook, the CLI |
| `model.py` | v2 | typed policy, views, platform facts, entries |
| `policy.py`, `presets.toml` | v2 | parse, validate, resolve and edit `policy.toml`; shipped presets |
| `compile.py` | v2 | policy to settings entries, hook data and warnings |
| `applied.py`, `apply.py` | v2 | ownership record, locking, atomic writes; plan and commit |
