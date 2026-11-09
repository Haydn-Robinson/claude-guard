# claude-guard

Keep Claude Code's writes where they belong. By default Claude can read freely but write only inside the current project. You grant more, narrowly, with one command. Important paths are protected, and Claude Code's own settings change only through proposals you review.

> Verified on WSL2 with Claude Code 2.1.321. The design and the reasons behind it are in [`docs/design.md`](docs/design.md).

## Contents

1. [Why](#why)
2. [Install](#install)
3. [Concepts](#concepts)
4. [Getting started](#getting-started)
5. [Everyday tasks](#everyday-tasks)
6. [Changing Claude Code settings: propose and review](#changing-claude-code-settings-propose-and-review)
7. [Checking what is in force](#checking-what-is-in-force)
8. [Troubleshooting](#troubleshooting)
9. [Command reference](#command-reference)
10. [`policy.toml` reference](#policytoml-reference)
11. [What `guard` writes, and how it is enforced](#what-guard-writes-and-how-it-is-enforced)
12. [What this protects against, and what it does not](#what-this-protects-against-and-what-it-does-not)
13. [Removing claude-guard](#removing-claude-guard)
14. [Development](#development)

## Why

Claude Code runs commands and edits files as your user. A rare but catastrophic failure is an agent destroying data outside the work it was asked to do, for example `rm -rf "$DIR/"` with `$DIR` empty. A second risk is an agent steered by text it reads (prompt injection from a repo, a web page or a file).

Claude Code has the controls to prevent both: a kernel-enforced sandbox for Bash, permission rules and hooks for its file tools. Setting them up by hand means many entries across several JSON files, and keeping them correct is harder. `guard` generates them from **one readable policy file**, shows you the diff, and keeps the two in step.

`guard` is a generator for Claude Code's settings plus one hook. **Claude Code does the enforcing.** Its protection does not depend on how a command is spelled or on the agent's intent:

1. the **sandbox** confines Bash, in the kernel, whatever the command looks like;
2. **path rules and a hook** cover the Edit/Write tools, which the sandbox does not confine;
3. **command-text rules** (`git push --force` asks, for example) are an advisory extra, never the thing that protects you.

## Install

Requirements: Python 3.15+, [uv](https://docs.astral.sh/uv/), and for Claude Code's sandbox on Linux or WSL2, `bubblewrap` and `socat` (`sudo apt install bubblewrap socat`). Only WSL2 has been tested; macOS and native Linux are untested.

```bash
uv tool install /path/to/claude-guard
guard init
mkdir -p ~/.claude/skills && cp -r /path/to/claude-guard/skills/claude-guard ~/.claude/skills/
```

The last line installs a skill that tells Claude how to work under `guard`: explain a refused write, ask you for the narrowest grant, and propose settings changes instead of attempting them.

Install it as a tool rather than running it from a checkout. The hook is registered by the absolute path of the installed `guard`, and `guard doctor` fails if that path is somewhere Claude can write, since editing it would disable the hook. After updating the source, run `uv tool install --reinstall /path/to/claude-guard`.

## Concepts

### Three categories of path

| Category | What | Default |
|---|---|---|
| **Open** | Reads anywhere, except secrets. Writes in the current project, in `$TMPDIR` and in tool caches | allowed, no prompt |
| **Closed** | Writes anywhere else | Bash: read-only. Edit/Write tools: **ask** |
| **Protected** | Windows drives and system folders, `/etc`, credentials, shell startup files, scripts that Claude Code settings run | Bash: read-only. Edit/Write tools: **refused** outside the project, **asked** inside it |

A **grant** opens a closed path. A protected path can be granted too, but only up to its **ceiling**: the most any grant may give it (see [Protected paths](#protect-a-path-or-change-a-ceiling)).

Claude Code's own settings files are **locked**. No grant can open them; you change them by approving a proposal ([below](#changing-claude-code-settings-propose-and-review)).

### Access levels

A grant gives one folder one access level. Claude writes in two ways, and each level says what each may do:

| `--access` | Edit/Write tools | Bash | Use it for |
|---|---|---|---|
| `full` (default) | write, no prompt | create, change, rename, delete | a folder Claude works in like the project |
| `bash` | ask (as if closed) | create, change, rename, delete | build outputs and caches the file tools do not need |
| `edit` | write, no prompt | read-only | "edit files but never delete or rename them" |
| `modify` | edit existing files only; no new files, no whole-file rewrites | read-only | config you want changed in place, not replaced |
| `ask` | prompt for every change, even in auto mode | read-only | folders where every change needs your eyes |
| `deny` | refused | refused | a no-go folder inside a larger grant |

Things to know:

- **The Edit/Write tools cannot delete or rename.** Only Bash can. That is why `edit`, `modify` and `ask` make the folder read-only to Bash: nothing there can be deleted.
- **Git cannot write in a Bash-read-only folder** (`git add` and `git commit` fail with `Read-only file system`). Commit from your own terminal.
- **`ask` cannot be switched off for the session.** Choosing "always allow for this session" at the prompt does not stop the next one. With an IDE connected, the prompt opens the diff there.
- **`modify` limits damage, it does not prevent it.** A large Edit can still rewrite most of a file.

### Scopes: this project, or everywhere

A grant made in a project applies to **that project's sessions only**. Add `--everywhere` to apply it to every session, or `--project DIR` to target another project.

- **The project** is the git repository root of the current folder, or the folder itself outside a repository. A session started in a sub-folder of the repository also uses the grants made for the repository.
- **Not in a project?** Your home folder or anything above it is never a project. `guard allow` there refuses and asks for `--everywhere` or `--project DIR`.

### One source of truth: `policy.toml`

Everything you configure lives in one file, `~/.config/claude-guard/policy.toml` (or `$CLAUDE_GUARD_HOME/policy.toml`). `guard allow`, `revoke` and `protect` edit it for you, keeping your comments and layout. You can also edit it yourself with `guard edit`.

`guard` compiles the policy into entries in Claude Code's settings files:

| Policy section | Settings file it compiles into |
|---|---|
| `[everywhere]` | `~/.claude/settings.json` |
| `[projects."<dir>"]` | `<dir>/.claude/settings.local.json` (read only in that project; not committed) |

Think of it like source code and a build: you edit the policy, and the settings entries are the output. If someone edits a generated entry by hand, `guard status` and `guard doctor` report the drift, and the next apply restores it, shown in the diff first. Entries you wrote yourself are never touched; `status` lists them as "not managed by guard". The policy lives outside every project, so a cloned repository cannot grant itself access.

**Changes apply live.** A running Claude session picks up new settings within seconds. No restart needed.

## Getting started

```bash
guard init
```

`init` creates `policy.toml` containing:

```toml
version = 1

[everywhere]
presets = ["baseline"]
```

It then explains what the baseline does, shows the diff to your settings, asks before applying, and runs `guard doctor`. The `baseline` preset:

| What | How |
|---|---|
| Sandbox on; no unsandboxed retries; bypass-permissions mode disabled (a request for it starts your default mode instead, silently) | settings |
| The Edit/Write tools **ask** before writing outside the project and your grants, in every permission mode | the hook |
| Claude Code's settings files, `~/.claude.json`, every project's `.claude/settings*.json` and `.mcp.json`, the managed settings folder and guard's own folder are **locked** | deny rules |
| Windows system folders (`Windows`, `Program Files`, `Program Files (x86)`, `ProgramData`) can never be written | protected, ceiling `deny` |
| `/etc`, `~/.ssh`, `~/.aws`, `~/.gnupg`, shell startup files and scripts that settings run: grantable up to `ask` at most | protected, ceiling `ask` |
| Windows drives (`/mnt/<letter>`): grantable up to `edit` at most, so Bash can never delete there | protected, ceiling `edit` |
| Secrets cannot be read: `~/.ssh`, `~/.aws`, `.env` files, and the credential files of gh, kube, docker, git, npm, pypi, gcloud, azure and Claude | `Read` deny rules |
| Tool caches (asked from each tool: `uv cache dir`, `npm config get cache`, ...) are writable by Bash, so builds work | `bash` access |
| `sudo` is refused (advisory: text matching) | command rule |

From then on Claude works freely in whatever project it is started in, and needs a grant or your approval anywhere else. Everything `guard` changes in a settings file is shown as a diff first.

## Everyday tasks

The commands that change things (`init`, `allow`, `revoke`, `protect`, `edit`, `apply`, `uninstall`) show one diff covering every file they touch and ask before applying. `--dry-run` shows the diff and stops; `--yes` applies without asking.

### Let Claude work in a folder outside the project

```bash
cd ~/repos/myproj
guard allow ~/data/myproj-output
```

This gives `full` access to that folder, for sessions in `~/repos/myproj` only. It adds one line to `policy.toml` and one entry to `~/repos/myproj/.claude/settings.local.json`; no other project is affected.

- **Every session:** add `--everywhere`.
- **The folder does not exist yet:** add `--create`. The sandbox cannot create a granted folder, and a grant on a missing folder is reported as a warning.

### Edit files on a Windows drive, but never delete or rename them

```bash
cd ~/repos/win-tool
guard allow /mnt/c/Users/me/AppData/Local/SomeApp/config --access edit
```

The Edit/Write tools can create and change files there; Bash cannot change anything. Without `--access edit` this is refused, because Windows drives have ceiling `edit` and the default `full` is above it. The error says which protected path blocks it.

### Ask before every change, or allow in-place edits only

```bash
guard allow ~/Documents/contracts --access ask
guard allow ~/.config/someapp --access modify
```

### Keep some files inside a grant untouchable

```bash
guard allow ~/repos/site-assets --protect "*.lock" --protect "release/**"
```

`--protect` globs are relative to the granted folder and are refused for the Edit/Write tools. If the grant lets Bash write (`full`, `bash`), a wildcard pattern may not stop Bash; `guard` warns when that is the case. Use a more restrictive access level for files that must survive Bash too.

### Remove a grant

```bash
guard revoke ~/data/myproj-output                # from this project
guard revoke ~/notes --everywhere
```

### Protect a path, or change a ceiling

```bash
guard protect ~/bin --ceiling ask                # nothing may get more than `ask` there
guard protect /mnt/d/archive --ceiling deny      # nothing may ever write there
```

A protected path with no grant behaves like the baseline's: Bash cannot write, and the Edit/Write tools are refused outside the project (asked inside it). Ceiling `deny` is enforced by deny rules, so no grant can open the path.

- **Only a grant that names the protected path, or a folder inside it, opens it.** A grant on a parent folder does not: the protected path inside stays protected.
- **Raising a ceiling is deliberate.** A project can add protected paths or lower a ceiling. Only `[everywhere]` can raise one, for example `guard protect /mnt/d --ceiling full --everywhere`.

### Ask before destructive commands

Add a shipped preset to a scope by editing the policy:

```bash
guard edit
```

```toml
[everywhere]
presets = ["baseline", "confirm-destructive", "git-destructive"]
```

On save, `guard edit` validates the file, then shows the diff and asks. `confirm-destructive` makes `rm`, `rmdir`, `unlink` and `shred` ask; `git-destructive` does the same for history-destroying git commands (`reset --hard`, `clean`, `push --force`, `branch -D`, `rebase`, ...). These match command text, so they are a guardrail; the sandbox is what limits where a delete can land.

### Granting while you are in a Claude session

Grants are made by **you**. Claude's own Bash runs in the sandbox, where `policy.toml` and the settings files are read-only, so Claude cannot run `guard allow` for itself. If Claude needs access, it should tell you the command. You can run it:

- in a separate terminal (recommended: you see the diff and answer the question), or
- inside the session with the `!` prefix. A `!` command runs outside the sandbox but has no terminal, so it cannot answer a question. Review with `--dry-run`, then apply with `--yes`:

  ```text
  ! guard allow ~/data/out --dry-run
  ! guard allow ~/data/out --yes
  ```

## Changing Claude Code settings: propose and review

Hooks, environment variables, the status line, MCP servers, plugins and permissions can all weaken the guardrails: a hook, for example, runs a command outside the sandbox. So Claude Code's settings files are locked, and Claude never gets write access to them. Instead:

1. **Claude proposes** a change, with a reason:

   ```bash
   guard propose ~/.claude/settings.json --set statusLine.command='~/bin/status.sh' --reason "show the git branch"
   ```

   A proposal is a list of operations on one file: `--set KEY=VALUE` and `--unset KEY` for keys, `--add KEY=VALUE` and `--remove KEY=VALUE` for array items. `guard propose` validates it, saves it in Claude's temporary folder, and tells Claude to ask you to review it. The skill in `skills/claude-guard` (see [Install](#install)) teaches Claude to do this instead of editing settings.

2. **You review, in a separate terminal:**

   ```bash
   guard review
   ```

   For each pending proposal you see the diff against the file **as it is now**, and a plain-English note for each security-relevant change. For example: *"adds a hook that runs `./x.sh` before every Bash command, outside the sandbox"*. Changes to entries `guard` manages are refused, with a pointer to `guard allow` or `guard edit`.

3. **You accept or reject each one.** On accept, `guard` backs up the file, writes it, deletes the proposal, and re-applies the policy, so any newly referenced script becomes protected.

Claude cannot approve its own proposal. `guard review` has no `--yes`, and it refuses to run without an interactive terminal. That includes `! guard review` inside a session, which has none. So the confirmation can only come from you at a keyboard.

Proposals can target `~/.claude/settings.json`, `~/.claude/settings.local.json`, a project's `.claude/settings.json` or `.claude/settings.local.json`, `.mcp.json` and `~/.claude.json`.

## Checking what is in force

### `guard status`

What is in force, per scope:

- where Claude can write without asking;
- where it asks;
- what is protected, and each ceiling;
- what is locked;
- whether the baseline is in force;
- pending proposals;
- any drift from hand edits.

`--verbose` adds the raw settings entries behind each line, and lists entries not managed by guard separately. `--json` for scripts.

### `guard explain`

`guard explain PATH` says what would happen to one path, for the Edit/Write tools and for Bash, and which layer decides: a rule, the hook, or the sandbox. `guard explain --command "CMD"` shows which command rules match. Use it whenever something is blocked and you do not see why.

### `guard doctor`

Health checks; exits 1 if any fails:

- the sandbox prerequisites are installed;
- every settings file parses;
- the baseline is in force;
- the hook is installed, and its executable is out of Claude's reach;
- the scripts that settings run are protected;
- generated entries are unchanged.

It also notes that a request for bypass mode silently falls back to your default mode.

## Troubleshooting

**`Read-only file system` (or `permission denied`) from a Bash command.** The sandbox refused a write; it never says so by name. Run `guard explain <path>`. Common causes:

- the folder is not granted;
- the grant is `edit`, `modify` or `ask`;
- it is a protected path;
- it is inside the project's `.claude/` folder, `.mcp.json` or a dotfile, which Claude Code keeps read-only to Bash by itself.

**`No such file or directory` for a file that exists.** A secrets read rule hides it from Bash as well (for example `~/.ssh`).

**A prompt for a write you expected to be allowed.** The hook asks for closed paths and for protected paths inside the project. The prompt does not show why, so look in `~/.config/claude-guard/hook.log`, or run `guard explain`.

**`guard allow` refuses with "above the ceiling".** The folder is in or under a protected path. Use a lower `--access`, or raise the ceiling with `guard protect ... --everywhere` if you really mean it.

**`guard allow` refuses a broad folder.** `/`, your home folder and anything above it, and whole drives are always refused. A shallow folder such as `/srv` needs `broad = true` on the grant in `policy.toml`.

**`guard allow` refuses because of a conflict.** A grant can only be stricter than a grant around it. For example, `full` inside an `edit` grant would still be read-only to Bash. Deny beats allow, so the error names both grants and explains why one cannot win.

**`git add` / `git commit` fail.** The folder is read-only to Bash (`edit`, `modify`, `ask`). Commit from your own terminal.

**Every Edit/Write is blocked with a hook error.** The hook is fail-closed: if the `guard` it points at is missing or broken, writes are refused rather than let through. Run `guard doctor`, then reinstall `guard`.

**Bypass mode does not start.** The baseline disables it; Claude starts in your default mode without saying so.

**A `.env` file in the project is readable through Bash.** This is intentional: it is the project's own file and reading it is not destructive. The Read tool is still blocked.

## Command reference

High level (each edits `policy.toml`, then shows one diff and applies on confirmation):

| Command | Does |
|---|---|
| `guard init` | Create `policy.toml` with the baseline, explain it, apply, run `doctor` |
| `guard allow PATH [--access LEVEL] [--protect GLOB]... [--everywhere \| --project DIR] [--create]` | Add a grant (default access `full`, default scope the current project). A grant for the same path in the same scope is replaced |
| `guard revoke PATH [--everywhere \| --project DIR]` | Remove a grant |
| `guard protect PATH --ceiling LEVEL [--everywhere \| --project DIR]` | Add a protected path, or change its ceiling |
| `guard status [--verbose] [--json]` | What is in force, per scope |
| `guard explain PATH` / `guard explain --command CMD` | What would happen, and which layer decides |
| `guard doctor` | Health checks; exit 1 on failure |
| `guard propose TARGET OPS... --reason TEXT` | For Claude: queue a settings change for your review |
| `guard review` | For you, in a separate terminal: accept or reject each pending proposal |

Low level:

| Command | Does |
|---|---|
| `guard edit` | Open `policy.toml` in `$EDITOR`; validate on save; then plan and apply |
| `guard plan` | Compile and show the diff; change nothing |
| `guard apply [--yes]` | Plan, confirm, apply. Also repairs drift |
| `guard presets` / `guard preset show NAME` | List presets / show what one compiles to |
| `guard uninstall` | Remove every entry guard owns, and the hook; keeps `policy.toml` |
| `guard hook edit` | Run by Claude Code; not for manual use |

Common options: `--dry-run` (show the diff only) and `--yes` (apply without asking; needed without a terminal) on the commands that change things. `$CLAUDE_GUARD_HOME` moves guard's folder (default `~/.config/claude-guard`).

## `policy.toml` reference

```toml
version = 1

# Every session: compiled into ~/.claude/settings.json.
[everywhere]
presets = ["baseline", "confirm-destructive"]
write = [
  { path = "~/.claude/skills", access = "full" },       # edit my skills from any project
  { path = "~/notes", access = "ask", protect = ["archive/**"] },
]
commands.ask = ["git push --force *"]

# Sessions in this project only: compiled into its .claude/settings.local.json.
[projects."~/repos/win-tool"]
write = [
  { path = "/mnt/c/Users/me/AppData/Local/SomeApp/config", access = "edit", protect = ["*.lock"] },
]
presets = ["git-destructive"]

# Your own bundles: same schema as a scope. One with a shipped preset's name replaces it.
[presets.notes]
write = [{ path = "~/notes", access = "ask" }]
```

Every scope (`[everywhere]`, `[projects."<dir>"]`, `[presets.<name>]`) takes the same keys:

| Key | Meaning |
|---|---|
| `presets` | bundles to include. Presets can include presets; cycles are rejected |
| `write` | grants: `{ path, access, protect?, broad? }`. `broad = true` allows a shallow folder such as `/srv` |
| `protected` | `{ path, ceiling }` |
| `locked` | paths (or `**/` globs) no grant may open |
| `closed` | `"ask"` or `"deny"`: what the hook does for writes outside the project and every grant. The stricter value wins |
| `read.deny` | paths Claude must not read |
| `commands.allow` / `.ask` / `.deny` | Bash command patterns, such as `"git push --force *"`. Advisory: text matching; `cmd *` also matches a bare `cmd` |
| `raw` | passthrough for anything else Claude Code can express: `raw.allow` / `.ask` / `.deny` (any permission rule), `raw.sandbox.allow_write` / `.deny_write`, `raw.settings = [{ path = "a.b", value = ... }]`. Validated and owned like everything else, but not interpreted |

**Paths.** A path is absolute, starts with `~/`, or starts with a placeholder that is filled in on this machine:

| Placeholder | Filled in with |
|---|---|
| `{system_drive}` | WSL only: the Windows system drive (for example `/mnt/c`) |
| `{drives}` | WSL only: every mounted drive |
| `{caches}` | each installed tool's cache folder |
| `{settings_scripts}` | the scripts that Claude Code settings run |
| `{guard_home}` | guard's own folder |
| `{managed_settings}` | Claude Code's managed settings folder |

A placeholder with no value (such as `{drives}` outside WSL) produces no entry. Paths are resolved, symlinks and `..` included. Grant and protected paths cannot contain glob characters. In `locked` and `read.deny`, a trailing `/` marks a folder (everything inside is covered); without it, an existing folder is detected and anything else is matched exactly.

**Validation.** Problems are reported before anything changes, with their location (for example `projects."/p".write[1].access: unknown access level 'rw'`):

- unknown keys and bad values;
- relative paths and globs in grants;
- unsafe folders: `/`, your home folder and above, whole drives, and `C:\Users` or the top of a Windows user profile;
- grants above a ceiling;
- grants on locked paths;
- conflicting grants;
- two scopes setting one setting to different values.

**Shipped presets:** `baseline` ([above](#getting-started)), `confirm-destructive`, `git-destructive`, and `strict`, which makes the hook refuse rather than ask for closed paths. `guard preset show NAME` shows exactly what one compiles to.

## What `guard` writes, and how it is enforced

Each access level compiles to the fewest settings entries that enforce it:

| `access` | Entries | Why it works |
|---|---|---|
| `full` | `Edit(//p/**)` allow | the allow rule also opens the folder in the sandbox, so no separate sandbox entry is needed |
| `bash` | `allowWrite p` | the sandbox opens the folder; the file tools stay closed |
| `edit` | `Edit(//p/**)` allow + `denyWrite p` | `denyWrite` beats every allow, so Bash stays read-only |
| `modify` | as `edit`, plus the hook refusing Write | |
| `ask` | `Edit(//p/**)` ask + `denyWrite p` | the ask rule prompts even in auto mode |
| `deny` | `Edit(//p/**)` deny | the deny rule also reaches the sandbox |

Concrete paths in deny rules (`//abs`, `~/`) stop both the file tools and Bash. `**/` glob rules stop only the file tools. So wherever Bash can write inside a grant, `guard` also names the settings files there explicitly (`<grant>/.claude/settings.json`, `settings.local.json`, `.mcp.json`).

**The hook** (`guard hook edit`, on Write, Edit and NotebookEdit) decides as follows, first match wins:

1. no file path → deny;
2. resolve the path (symlinks, `..`; Windows drive paths case-insensitively);
3. locked, ceiling `deny`, or a `protect` glob → deny;
4. protected → if a grant opens it, go on to step 5; otherwise **ask** inside the project and **deny** outside it;
5. inside a grant (the most specific wins) → by access level: `modify` refuses Write, `bash` counts as closed, the rest leave it to the rules;
6. inside the project → no opinion;
7. anywhere else (closed) → **ask** (baseline) or **deny** (`strict`).

It never allows anything, so it can only tighten. It is fail-closed: if it crashes, the write is blocked. It logs each decision to `hook.log`, because the prompt does not show its reason. It enforces what was last **applied**, read from `applied.json`, not unapplied edits to `policy.toml`. The project is the git root containing the folder Claude was started in.

**Apply** writes only the difference:

- **Your entries stay.** Entries already in a file are recorded as yours and never removed. Unknown keys and their order are preserved.
- **Undo is complete.** A setting guard changed gets its previous value back when no longer wanted. A file guard created is deleted when it empties.
- **Writes are safe.** Every changed file is backed up to `~/.config/claude-guard/backups/` and written atomically, under a lock. The apply is refused if a file changed since the diff was shown.
- **Ownership is recorded** in `applied.json`, a bookkeeping file you never edit.

## What this protects against, and what it does not

**Protects:**

- **Bash** is confined by the kernel to the project, `$TMPDIR`, tool caches and your grants, however a command is written: `rm`, `/bin/rm`, `bash -c`, a script.
- **The Edit/Write tools** are confined by rules and the hook, in every permission mode, auto included.
- **Protected paths** keep their ceilings; locked settings change only by your review.
- **Secrets** are hidden from the Read tool, and the `~/` ones from Bash too.

**Does not protect against, or not yet:**

- **Network.** Reads are open, so data could leave over the network. `guard` does not manage the sandbox's network settings.
- **MCP tools.** They are neither sandboxed nor seen by the hook.
- **Command rules are text matching.** `confirm-destructive` and `git-destructive` can be sidestepped by rephrasing. They are a guardrail; the sandbox is the wall.
- **Projects without a section in the policy.** A protected path inside such a project can be written by Bash: the project is open by default, and `guard` writes sandbox entries only into files it manages. Claude Code itself still keeps Bash out of the project's `.claude/`, `.mcp.json` and dotfiles, and the Edit/Write tools are still asked.
- **`.env` files in the project** are readable through Bash (a deliberate choice; see [Troubleshooting](#troubleshooting)).
- **A determined user.** On WSL you can write the managed settings yourself. They guard against mistakes and project overrides, not against you.
- **Platforms.** Verified on WSL2 only.

What is known about Claude Code's behaviour, and how sure we are of each point, is recorded in [`docs/verified-behaviour.md`](docs/verified-behaviour.md). Every fact there has an ID, a status, the evidence and a runbook to re-check it.

## Removing claude-guard

```bash
guard uninstall          # removes every entry guard owns, and the hook; keeps policy.toml
uv tool uninstall claude-guard
```

Run `guard uninstall` **before** uninstalling the tool. The hook is fail-closed, so a hook left registered without `guard` would block every Edit/Write. Backups remain in `~/.config/claude-guard/backups/`.

## Development

```bash
uv sync
uv run pytest                                          # temp dirs and a fake HOME; never your real settings
uv run ruff check . && uv run ruff format --check .
UPDATE_SNAPSHOTS=1 uv run pytest                       # refresh snapshots, after reviewing the diff
```

Contributor rules are in [`CLAUDE.md`](CLAUDE.md):

- read `docs/verified-behaviour.md` before proposing an experiment;
- cite its fact IDs in code that depends on Claude Code's behaviour;
- write settings-mutation code test-first, with mutation checks;
- never touch real settings in tests.

| Module (`src/claude_guard/`) | Role |
|---|---|
| `model.py` | typed policy, views, platform facts, entries |
| `policy.py`, `presets.toml` | parse, validate, resolve and edit `policy.toml`; shipped presets |
| `rules.py`, `pathsafety.py` | rule and sandbox path forms, glob and command matching; unsafe-folder refusals |
| `machine.py`, `effective.py` | drives, caches, scripts that settings run, the project; every settings source in precedence order |
| `compile.py` | policy to settings entries, hook data and warnings (pure) |
| `settingsfile.py`, `applied.py`, `apply.py` | non-clobbering JSON; `applied.json`, locking, atomic writes; plan and commit |
| `hook.py` | the Edit/Write hook (imports kept light: it runs on every file write) |
| `proposals.py` | propose and review |
| `context.py`, `views/`, `cli.py` | the shared pipeline; status, explain, doctor; the CLI |
