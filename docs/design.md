# claude-guard v2: design

Status: **approved; being built**, updated with the phase 2 verification results (`phase2_results.md`, 2026-11-09). Built so far (phase 3, the core): `model`, `policy` (parse, resolve, format-preserving edits), `presets.toml`, `rules`, `pathsafety`, `compile`, `applied`, `apply`. Not yet: platform detection (caches, settings scripts), `effective`, `proposals`, the v2 hook, views and CLI. Facts about Claude Code are cited by ID from `verified-behaviour.md`; anything not yet verified is called out as such.

## 1. Purpose

Claude Code runs commands and edits files as your user. A rare but catastrophic failure is an agent destroying data outside the work it was asked to do, for example an `rm -rf "$DIR/"` with `$DIR` empty. `guard` sets up Claude Code's own controls so that:

- by default, Claude can read freely but can only **write** inside the current project (plus temp and cache folders);
- you can **grant** more, narrowly (one folder, one project, one kind of access), with one command;
- important paths are **protected**: refused by default, and grantable only up to a ceiling you set per path;
- Claude Code's own settings change only through a **proposal you review**, never by Claude writing to them;
- the whole configuration is **reviewable in one place**.

### Threat model

The main threat is a well-meaning agent making a destructive mistake. A secondary one is an agent steered by text it reads (prompt injection from a repo, web page or file). Both are handled the same way: enforcement must not depend on the agent's intent or on how a command is spelled. So `guard` relies on:

1. **The sandbox** (kernel-enforced, confines Bash whatever the command looks like: VB-S2) for Bash.
2. **Path rules and a hook** (the tool path is structured data, so there is no parsing to get wrong) for the Edit/Write tools, which the sandbox does not cover (VB-S10).
3. **Command-text rules** only as an advisory extra, never as the thing that protects you (VB-R5).

`guard` is a generator for Claude Code's settings plus one hook. It is not a security boundary of its own: Claude Code enforces everything.

## 2. Principles

1. **Three categories.**

   | Category | Examples | Default | Can a grant open it? |
   |---|---|---|---|
   | **Open** | reads anywhere (except secrets); writes in the project and `$TMPDIR` (VB-S4), and in tool caches (opened by `baseline`, VB-S16) | allowed | n/a |
   | **Closed** | writes anywhere else | Bash: read-only (sandbox). Edit/Write tools: the hook asks | yes, to any access level |
   | **Protected** | Windows drives, `/etc`, credentials, shell startup files, scripts that settings run | Bash: read-only. Edit/Write tools: **refused** outside the project, asked inside it | yes, but only up to the path's **ceiling** (section 6.1). A ceiling of `deny` means no grant can open it |

   Claude Code's settings files sit outside this scheme: they are **locked** (deny rules), and you change them by approving proposals (section 9).

2. **Deny rules only where nothing should open the path.** A deny anywhere in Claude Code's settings hierarchy beats every allow, and cannot be scoped to one project (VB-R3, VB-P1). So "closed" and "protected" are built from the sandbox and the hook, which a grant can open. Deny rules are used only for: protected paths whose ceiling is `deny`, locked settings files, secrets reads, and `protect` globs. You can still change any of these in the policy itself; "deny" means no *grant* can override it.

   A deny rule with a concrete path (`//abs` or `~/`) stops both the file tools and Bash (VB-S7, VB-S13). A deny rule with a `//**/` glob stops only the file tools (VB-S14). So `guard` uses globs to cover the file tools everywhere, and emits concrete paths wherever Bash could otherwise reach the file.

3. **One source of truth, generated output.** You maintain one human-readable policy file. Claude Code's settings files carry the generated entries (section 4).

4. **Fewest entries, fewest files.** Each intent compiles to the minimum set of settings entries that enforces it, using verified facts to avoid redundant ones. Entries go to at most two kinds of file: user settings, and the local settings of a project that has project-scoped grants.

5. **Narrow by default.** A grant made inside a project applies to that project's sessions only. Applying it everywhere is an explicit choice.

6. **Say what cannot be enforced.** When an intent can only be partly enforced (Bash cannot "ask"; command rules match text), the compiler says so in its output rather than silently approximating.

7. **No capability loss from v1.** Everything v1 profiles could express can be expressed in the policy (section 13).

## 3. Architecture overview

```
 you ── guard allow / revoke / edit ──▶  policy.toml          (source of truth)
                                             │  compile        (pure function)
                                             ▼
                                     desired entries per file  + warnings
                                             │  plan           (diff against the files and applied.json)
                                             ▼
                                        diff ── you confirm ──▶ apply
                    ┌────────────────────────┼─────────────────────────────┐
                    ▼                        ▼                             ▼
     ~/.claude/settings.json   <project>/.claude/settings.local.json   applied.json
     (everywhere scope)        (one per project section)               (what guard owns;
                    │                        │                          read by the hook)
                    └──── Claude Code reads these and enforces them ────┘

 Claude ── guard propose ──▶ proposal (temp dir) ── you: guard review ──▶ settings file
```

Two layers of interface sit on this:

- **Low level**: write `policy.toml` directly (including a raw passthrough for any setting), then `guard plan` / `guard apply`.
- **High level**: `guard init`, `guard allow`, `guard revoke`, `guard protect`, `guard status`. Each edits `policy.toml` for you, then runs the same plan and apply. There is no second code path, so the two cannot drift apart.

## 4. `policy.toml` and Claude Code's settings files

**Where it lives.** One file per user: `~/.config/claude-guard/policy.toml` (or `$CLAUDE_GUARD_HOME/policy.toml`). It is not inside any project. You can keep it in a dotfiles repo.

**How it relates to the settings JSON files.** Claude Code only ever reads its own settings files (`~/.claude/settings.json`, `<project>/.claude/settings.local.json`, ...); those are what enforce the policy. `policy.toml` is the *source* that `guard` compiles into entries in those files, in the way source code relates to a built binary or a Terraform config to the infrastructure it creates. You edit the source; the output is regenerated from it.

**Is the policy now maintained in several places?** No. You maintain one place, `policy.toml`. The generated entries in the settings files are output: you do not edit them, and if you do, `guard status` and `guard doctor` report the drift and the next `guard apply` restores what the policy says (showing the change in the diff first). The settings files also keep any entries you wrote by hand. `guard` never touches those and shows them in `status` as "not managed by guard".

**Why not make the settings files themselves the source of truth?** Because they cannot carry what a reviewable policy needs:

- JSON has no comments, so there is nowhere to record *why* a grant exists.
- One intent often needs two entries in different places (an `edit` grant is an `Edit` allow plus a `denyWrite`). Read back from JSON, that is two unrelated lines; in the policy it is one line that says `access = "edit"`.
- Project grants live in a separate file in each project. Without a central file you cannot see every grant on the machine in one place.
- `guard` must know which entries it owns, so it can remove them later without touching yours. v1 already needed a bookkeeping file for this (`state.json`). v2 replaces it with a readable policy plus a small machine record (`applied.json`).

**`applied.json`** is the record of what the last apply wrote: per settings file, the entries guard owns, entries that already existed (never removed), prior values of scalar settings, and containers guard created. It also holds what the hook needs: grants with their scope and access, protected paths with their ceilings, and locked paths. It is bookkeeping, like a lock file. You never edit it. The hook reads it, so the hook enforces what was actually applied rather than an unapplied edit to `policy.toml`.

**Why not a policy file inside each project?** Claude can write inside the project, and a cloned repo could grant itself access. Grants for a project are stored in your central policy, keyed by the project path.

## 5. The policy format

```toml
version = 1

# Everywhere: compiled into ~/.claude/settings.json, applies to every session.
[everywhere]
presets = ["baseline"]
write = [
  { path = "~/.claude/skills", access = "full" },
]
commands.ask = ["git push --force *"]

# Applies only to sessions started in this project; compiled into its .claude/settings.local.json.
[projects."~/repos/win-tool"]
write = [
  { path = "/mnt/c/Users/me/AppData/Local/SomeApp/config", access = "edit", protect = ["*.lock"] },
]
presets = ["confirm-destructive"]

# Your own reusable bundles; same schema as a scope.
[presets.notes]
write = [{ path = "~/notes", access = "ask" }]
```

Every scope (`[everywhere]`, each `[projects."<dir>"]`, each `[presets.<name>]`) has the same schema:

| Key | Meaning |
|---|---|
| `presets` | bundles to include (shipped or your own; presets can include presets; cycles rejected) |
| `write` | grants: `{ path, access, protect?, broad? }` (section 6). `broad = true` allows a shallow folder such as `/srv` (v1's `--force-broad`); `/`, `$HOME` and above, and whole drives are always refused |
| `protected` | protected paths: `{ path, ceiling }` (section 6.1) |
| `locked` | paths (or `**/` globs) no grant may open: settings files, guard's own folder. Compiled to `Edit` deny rules, plus concrete `denyWrite` entries inside grants where Bash can write (section 7.2) |
| `closed` | `"ask"` or `"deny"`: registers the hook, and says what it does for a write outside the project and every grant. The stricter value wins across presets and scopes |
| `read.deny` | paths Claude must not read |
| `commands.allow` / `.ask` / `.deny` | Bash command patterns (advisory: text-matched, VB-R5) |
| `raw` | passthrough: `raw.allow` / `.ask` / `.deny` (any permission rule string), `raw.sandbox.allow_write` / `.deny_write`, `raw.settings` (`{ path = "a.b", value = ... }`). Validated and owned like everything else, but not interpreted. This is the escape hatch that guarantees nothing Claude Code can express is out of reach |

**Path forms.** Paths are absolute, start with `~/`, or start with a placeholder that is expanded on this machine at compile time, giving one entry per value or none: `{system_drive}`, `{drives}`, `{caches}`, `{settings_scripts}`, `{guard_home}`, `{managed_settings}`. This keeps shipped presets platform-independent: `{drives}` is empty outside WSL. In `locked` and `read.deny`, a trailing `/` marks a folder (compiled to `//p/**`). Without it, an existing folder is detected, and anything else is matched exactly (`//p`). So a rule never needs both forms (VB-U11), and the presets compile the same whether or not the paths exist yet.

Validation happens at load: unknown keys, bad access levels, relative or glob-bearing grant paths, broad paths (`/`, `$HOME` or above, a whole drive: v1's `PathPolicy` refusals, kept), grants above a ceiling, and conflicts (section 7.4) are all errors before anything is planned.

The high-level CLI edits this file with a format-preserving TOML library (`tomlkit`, the one new dependency), so your comments and layout survive.

## 6. Access levels

A grant gives one folder one access level. The levels are defined by what each write path can do, and are listed from most to least permissive:

| `access` | Edit/Write tools | Bash | Compiles to | Facts |
|---|---|---|---|---|
| `full` | write, no prompt | create, modify, rename, delete | `Edit(//p/**)` allow | VB-E3, VB-S7 |
| `bash` | closed (hook asks) | create, modify, rename, delete | `allowWrite p` | VB-S1 |
| `edit` | write, no prompt | read-only (no delete or rename) | `Edit(//p/**)` allow + `denyWrite p` | VB-E3, VB-S8, VB-S10 |
| `modify` | Edit only, on existing files; Write is refused | read-only | as `edit`, plus a hook rule | VB-S8, VB-H1, VB-H2 |
| `ask` | prompts every time, even in auto mode | read-only | `Edit(//p/**)` ask + `denyWrite p` | VB-E4, VB-S8 |
| `deny` | refused | refused | `Edit(//p/**)` deny | VB-E5, VB-S7 |

`protect = [globs]` on a grant adds `deny` for sub-paths (relative to the grant folder).

Notes:

- The Edit/Write tools cannot delete or rename at all. That is why `edit` gives "edit files but not rename or delete them": Bash is the only route to deletion, and `edit` makes the folder read-only to Bash. Side effect: git cannot write there (`git add`/`commit` fail, VB-S8).
- The Write tool can create files and replace a file's whole content. `modify` closes that by refusing Write (only Edit, MultiEdit and NotebookEdit on existing files pass). A large Edit can still rewrite most of a file, so `modify` limits content loss rather than preventing it.
- `full` needs no `allowWrite` entry and no Bash allow rules: the Edit allow reaches the sandbox (VB-S7), and sandboxed commands run without a prompt (VB-S11). v1 emitted both; v2 does not.
- `bash` exists mainly for the cache folders in the baseline: build tools need to write there, but there is no reason for the file tools to. It ranks above `edit` because it lets Bash delete.

Verified in phase 2: `edit` on a Windows drive from a project-local file (Bash write and rename refused, file tools work: VB-S8, VB-P4); `modify` (a Write over an existing file is refused, new files and Edits pass: VB-H8); `ask` (approval prompts show the diff in the IDE, and "always allow for this session" does not stop the next prompt: VB-E7, VB-E10).

### 6.1 Protected paths and ceilings

A protected path has a **ceiling**: the most permissive access level any grant may give on it or inside it. `guard allow` refuses a grant above the ceiling and says which protected entry blocks it. To go higher you raise the ceiling in the policy, which is deliberate and visible in one place.

How a protected path behaves:

- **No grant**: Bash cannot write (outside the project the sandbox already ensures this; elsewhere the compiler emits `denyWrite`, see below). The Edit/Write tools are **refused** outside the project and **asked** inside it, by the hook, in every permission mode (VB-H8).
- **Ceiling `deny`**: compiled to an `Edit` deny rule, so it is refused before the hook even runs (VB-E5), and no grant can open it.
- **Only a grant that names the protected path or something inside it opens it.** A grant on a parent folder that happens to contain a protected path does not: the compiler emits `denyWrite` for the protected path inside that grant, and the hook still treats it as protected.
- A project's own section can add protected paths or lower a ceiling, but only `[everywhere]` can raise one (an entry there with the same path replaces the preset's ceiling).

## 7. Compilation

### 7.1 Where entries go

- `[everywhere]` → `~/.claude/settings.json`.
- `[projects."<dir>"]` → `<dir>/.claude/settings.local.json`. That file is read only by sessions in that project (VB-P1), and it is personal (not committed).
- `guard` never writes the shared, committed `.claude/settings.json`, or managed settings. (Those change only through `guard review`, section 9.)

**What counts as the project.** For the CLI: the git root of the current directory, or the directory itself if there is none. A session started in a sub-folder reads the git root's local settings too (VB-P5), so grants written there apply wherever in the repo you start Claude. The diff always names the project. Run outside any project (for example in `$HOME`), `guard allow` refuses and asks for `--everywhere` or `--project DIR`.

### 7.2 The `baseline` preset

| Part | Contents | Mechanism | Facts / status |
|---|---|---|---|
| Sandbox on, no unsandboxed retries, no bypass mode | `sandbox.enabled: true`, `sandbox.allowUnsandboxedCommands: false`, `permissions.disableBypassPermissionsMode: "disable"` | settings | `enabled` VERIFIED; bypass refused (silently, VB-P6); `allowUnsandboxedCommands` still VB-U5 |
| Edit/Write hook, `ask` for closed paths | one `hooks.PreToolUse` entry, `onFailure: "block"` | hook | VB-H1 to VB-H3, VB-H8 |
| **Locked**: Claude Code and guard settings | `~/.claude/settings*.json`, `~/.claude.json`, `**/.claude/settings*.json`, `**/.mcp.json`, the managed settings folder, guard's own folder | File tools: `Edit` deny (globs work, VB-U4). Bash: see below. Changed via `guard review` | VB-E5, VB-E9, VB-S14, VB-S15 |
| **Protected, ceiling `deny`**: Windows system folders (WSL only, on the detected system drive) | `Windows`, `Program Files`, `Program Files (x86)`, `ProgramData` | `Edit` deny | VB-E5 |
| **Protected, ceiling `ask`** | `/etc`; `~/.ssh`, `~/.aws`, `~/.gnupg`; shell startup files (`~/.bashrc`, `~/.bash_profile`, `~/.profile`, `~/.zshrc`, `~/.zprofile`, `~/.zshenv`); scripts that settings run (below) | hook (+ `denyWrite` where needed) | VB-H1, VB-H2 |
| **Protected, ceiling `edit`**: Windows drives (WSL only) | every detected `/mnt/<letter>` | hook (+ `denyWrite` where needed) | VB-P4 |
| Secrets (read) | `~/.ssh`, `~/.aws`, `**/.env`, `**/.env.*`, `~/.config/gh`, `~/.kube`, `~/.docker/config.json`, `~/.netrc`, `~/.git-credentials`, `~/.npmrc`, `~/.pypirc`, `~/.config/gcloud`, `~/.azure`, `~/.claude/.credentials.json` | `Read` deny. The `~/` entries also hide the files from Bash (VB-S13); the `**/.env*` globs stop only the file tools (VB-S14), see below | VB-U4, VB-S13, VB-S14 |
| Open: tool caches | each installed tool's configured cache folder, asked from the tool at apply time (`uv cache dir`, `npm config get cache`, `pip cache dir`, `$XDG_CACHE_HOME` …), never a hard-coded `~/.cache` | `bash` access | VB-S16 |
| Advisory | `commands.deny = ["sudo *"]` | `Bash` deny | VB-R5 |

**Keeping Bash away from locked files.** Glob deny rules do not reach the sandbox (VB-S14), so they are not what stops Bash:

- **Outside the project and grants**, the sandbox already makes everything read-only (VB-S4).
- **In the working directory**, Claude Code's own sandbox protection makes the whole `.claude/` folder, `.mcp.json` and the shell and git dotfiles read-only to Bash (VB-S15).
- **Inside a granted folder** (where Bash may write), it is not known whether that built-in protection applies (VB-U17). `guard` emits explicit `denyWrite` entries for the concrete settings files in each grant that gives Bash write access (`<grant>/.claude/settings.json`, `settings.local.json`, `<grant>/.mcp.json`).

**`.env` files in the project are readable through Bash** (a decision: they are the project's own files, reading is not destructive, and the file tools are still blocked). `explain` and `status` say so.

**Scripts that settings run.** A settings file can name commands to run: hook commands, `statusLine`, `apiKeyHelper`. Those run outside the sandbox, so editing such a script is as powerful as editing the settings. At apply time (and after each `guard review`), `guard` reads every settings source, finds commands that point at files, and protects those files with ceiling `ask`. `doctor` lists them.

Deliberately **not** in the baseline:

- Text rules like `Bash(rm -rf /)`. They are trivially rephrased and give false confidence; the sandbox is what stops this.
- Project `.claude/` folders other than the settings files, so editing skills, agents and commands in a project stays frictionless. The file tools write there with no prompt (VB-E9). Bash cannot (VB-S15), so a script that generates skill files has to be run by you, or Claude writes them with the file tools.

Other shipped presets: `confirm-destructive` (`commands.ask` for `rm`, `rmdir`, `unlink`, `shred`), `git-destructive` (`commands.ask` for history-destroying git commands, with their common variants), `strict` (hook `deny` instead of `ask` for closed paths; see section 14 for hardening ideas).

### 7.3 Rule and path forms

Permission rules use `//abs/path` for absolute paths; sandbox entries use `/abs/path` (VB-R4). The compiler always emits absolute forms, expanding `~` itself, so no entry depends on where the settings file sits. Paths are resolved (symlinks, `..`) before compiling, and paths containing glob characters are refused, as in v1.

### 7.4 Conflicts the compiler rejects

Because deny beats allow and `denyWrite` beats `allowWrite` with no sub-path exception (VB-S8):

- A grant nested inside a more restrictive one for the same write path, for example a `full` grant inside an `edit` grant (Bash stays read-only in the inner folder), or anything inside a `deny`.
- A grant above the ceiling of a protected path it names or sits inside.
- A grant on a locked path.
- Two scopes setting the same scalar to different values.
- A grant on a path that a user-wide `ask` or `deny` would shadow in that project.

Each error names both entries and why one cannot win.

## 8. The hook

`guard hook edit` is a PreToolUse hook on `Write|Edit|MultiEdit|NotebookEdit`. It reads the tool call and `applied.json`, and decides, first match wins:

1. No file path in the call → **deny**.
2. Resolve the path (`..`, symlinks; Windows drive paths matched case-insensitively, VB-U12).
3. Locked, ceiling `deny`, or `protect` → **deny**. A backstop: Claude Code already refuses these before the hook runs (VB-E5, VB-H5).
4. Protected:
   - a grant in scope names the protected path or something inside it, and contains this path → go to step 5 with that grant;
   - inside the project → **ask**;
   - otherwise → **deny**.
5. Inside a grant in scope (the most specific wins) → by access level: `modify` and tool `Write` → **deny**; `bash` → treat as closed (step 7); otherwise **no opinion** (the rules apply, so `ask` still prompts).
6. Inside the project → **no opinion**.
7. Closed → **ask** (baseline) or **deny** (`strict`).

It never allows, so it cannot widen anything. It is registered with `onFailure: "block"` (fail-closed, VB-H3), and it logs each decision to `hook.log`, because the prompt does not show the hook's reason (VB-E7).

**The project root.** `CLAUDE_PROJECT_DIR` is set in every hook call, and it is the folder Claude was started in, not the repo root (VB-H7). Since a session started in a sub-folder also applies the repo root's settings (VB-P5), the hook takes as the project **the git root containing `CLAUDE_PROJECT_DIR`**, or `CLAUDE_PROJECT_DIR` itself outside a repo (falling back to the payload `cwd` if it is unset). Whether Bash can write in the whole repo in that case, or only the launch folder, is open (VB-U15); `explain` gives the cautious answer. A project root at or above `$HOME` counts as no project, so starting Claude in `~` does not open your whole home folder.

The matcher stays `Write|Edit|MultiEdit|NotebookEdit`: NotebookEdit reaches the hook, and MultiEdit no longer exists, which is harmless (VB-H8).

**Installation.** `guard` is installed as a tool (`uv tool install`), and the hook is registered by absolute path to that install. `doctor` fails if the hook executable or its package sits anywhere Claude can write (the project, a grant, a sandbox write path), since editing it would disable the hook.

## 9. Changing Claude Code settings: propose and review

There are many good reasons to have Claude help with settings besides permissions: hooks, environment variables, the model, the status line, MCP servers, plugins, output style, sandbox network settings. But most of these can weaken the guardrails: a hook or `apiKeyHelper` runs a command outside the sandbox, an `env` entry changes how tools behave. So settings files are **locked**, and Claude never gets write access to them. Instead:

1. **Claude proposes.** It runs `guard propose`, for example `guard propose ~/.claude/settings.json --set statusLine.command='~/bin/status.sh' --reason "show git branch"`. The proposal is a list of operations (set or remove a key, add or remove an array item) on one target file, plus the reason. `guard propose` validates it and saves it under `$TMPDIR`, which the sandbox lets Claude write to (VB-S4; `/tmp` itself is read-only), then prints: "ask the user to run `guard review` in a separate terminal". A shipped skill tells Claude to use this instead of editing settings.
2. **You review, in a separate terminal.** `guard review` applies the operations to the file *as it is now* (so a proposal never overwrites changes made since), and shows:
   - the diff;
   - each security-relevant change in plain English: hooks, `apiKeyHelper`, `statusLine` commands, `env`, `permissions`, `sandbox`, MCP servers, plugins, `defaultMode`. For example: "adds a hook that runs `./x.sh` before every Bash command, outside the sandbox";
   - a refusal for any change to entries guard manages, pointing to `guard allow` or `guard edit` instead.
3. **You confirm.** `guard` backs up the file, writes it atomically, deletes the proposal, logs it, and re-runs apply so newly referenced scripts become protected.

Properties:

- **Human scrutiny**: every change is shown and explained before it happens.
- **Scoped**: the approval covers exactly the operations shown.
- **Temporary**: no access is ever granted, so there is nothing to revoke.
- **Claude cannot approve its own proposal**: settings files are outside its sandbox (or protected by it, VB-S15), `guard review` has no `--yes`, and it refuses to run without an interactive terminal.

**Why a separate terminal.** A `!` command typed inside a session runs outside the sandbox but has no terminal and gets no keyboard input, and its output goes back into the conversation (VB-P7). So `! guard review` could not ask you to confirm, and `guard` could not tell your `!` from Claude's own Bash call (both have no terminal and `CLAUDECODE=1`). Requiring an interactive terminal means the confirmation can only come from you at a keyboard.

Targets: user settings, user local settings, a project's `.claude/settings.json` and `.claude/settings.local.json`, `.mcp.json` and `~/.claude.json`.

## 10. Plan and apply

1. Load and validate `policy.toml`; read the current settings sources (for scripts that settings run); compile to desired entries per settings file.
2. For each target file (and each file guard owned before but no longer targets):
   - desired and already present but not owned → record as pre-existing, never remove;
   - desired and missing → add (if guard owned it before, it was removed by hand: say so in the plan);
   - owned and no longer desired → remove;
   - owned scalar changed by hand → restore the policy value (shown in the diff);
   - prune containers guard created that are now empty; delete a file guard created that is now empty.
3. Show one diff for all files, with warnings; ask (or `--yes`; `--dry-run` / `guard plan` stops here).
4. Apply under a lock on `applied.json`: validate JSON, back up every changed file, atomic writes, then write `applied.json`; roll back the settings files if that last write fails.

Kept from v1: non-clobbering JSON editing that preserves unknown keys and order (`settingsfile.py`), atomic writes and backups. Dropped: per-grant reference counting. The compiler produces the complete, de-duplicated desired set for each file, so ownership is per file, not per grant.

**Live reload.** Rules, hooks and sandbox paths all reach a running session within seconds, with no restart (VB-S6, verified for a project's local file; the user settings file is assumed to behave the same, VB-U3). So `guard` does not ask you to restart Claude Code after an apply.

## 11. CLI

High level (each edits `policy.toml`, then plans and applies with one confirmation):

| Command | Does |
|---|---|
| `guard init` | Create `policy.toml` with `[everywhere] presets = ["baseline"]`, explain what it does, apply, then run `doctor` |
| `guard allow PATH [--access LEVEL] [--protect GLOB]... [--everywhere \| --project DIR] [--create]` | Add a grant. Default access `full` (capped by any ceiling, with an error saying so); default scope: the current project |
| `guard revoke PATH [--everywhere \| --project DIR]` | Remove a grant |
| `guard protect PATH --ceiling LEVEL [--everywhere \| --project DIR]` | Add a protected path, or change a ceiling |
| `guard status [--verbose] [--json]` | Outcomes, per scope: where Claude can write without asking, where it asks, what is protected (with ceilings) or locked, whether the baseline is in force, pending proposals, and drift. `--verbose` adds the raw entries; settings entries not managed by guard are listed separately |
| `guard explain PATH` / `guard explain --command CMD` | What would happen to this path or command, and which layer decides (from v1, unchanged in spirit). Explains the misleading errors: a sandbox write block shows as `Read-only file system` (VB-S3), a read block as `No such file or directory` (VB-S13) |
| `guard doctor` | Health checks (sandbox prerequisites, settings valid, baseline in force, hook installed safely, scripts that settings run, drift); exit 1 on failure. Notes that a request for bypass mode silently starts in your default mode instead (VB-P6) |
| `guard propose TARGET OPS... --reason TEXT` | For Claude: queue a settings change for review |
| `guard review` | For you, in a separate terminal: review, then accept or reject each pending proposal |

Low level:

| Command | Does |
|---|---|
| `guard edit` | Open `policy.toml` in `$EDITOR`; validate on save; then plan and apply |
| `guard plan` | Compile and show the diff; change nothing |
| `guard apply [--yes]` | Plan, confirm, apply |
| `guard presets` / `guard preset show NAME` | List presets / show what one compiles to |
| `guard uninstall` | Remove every entry guard owns and the hook; leaves your policy file |
| `guard hook edit` | Internal: run by Claude Code |

Example, the "edit Windows files but not rename or delete them, for one project" case:

```bash
cd ~/repos/win-tool
guard allow /mnt/c/Users/me/AppData/Local/SomeApp/config --access edit
```

This adds one line to `policy.toml` and two entries to `~/repos/win-tool/.claude/settings.local.json`. Nothing changes for any other project. `--access full` would be refused: Windows drives have ceiling `edit`.

## 12. Code structure

```
model.py       typed dataclasses: Policy, Scope, Grant, Access, Protected, CommandRules, Raw, Entry, Compiled
policy.py      load / validate / save policy.toml (tomlkit); preset resolution; high-level edits
presets.toml   shipped presets
pathsafety.py  broad-path refusals, drive detection (v1 paths.py)
rules.py       rule and sandbox path forms, glob and Bash matching, rule-string parsing
compile.py     Policy + platform facts + settings-referenced scripts -> Compiled (entries per file,
               hook data, warnings). Pure
settingsfile.py  non-clobbering JSON document (kept from v1)
applied.py     applied.json, locking, atomic writes, backups
apply.py       plan (diff) and commit
effective.py   read every Claude Code settings source in precedence order
proposals.py   propose / review: operations, security annotations, apply one proposal
hook.py        decide(payload, applied) -> decision; light imports for speed
views/         status, explain, doctor: data builders separate from renderers
cli.py         argparse wiring only
```

Dependencies point one way: `model` ← `rules` ← `compile` ← `apply` ← `cli`. The hook depends only on `model`, `rules` and `applied`. Code that depends on a Claude Code behaviour cites its `VB` ID.

Testing:

- compile golden tests: policy in, entries out;
- apply properties: applying twice is a no-op; apply then uninstall restores files byte-for-byte; hand edits are detected;
- a hook decision table, including protected paths, ceilings and parent grants;
- proposal tests: operations apply to the current file, security annotations, refusal of guard-owned entries, no non-interactive accept;
- CLI snapshots;
- mutation checks on settings-mutating code (as `CLAUDE.md` requires).

## 13. From v1

Kept: settings document model, atomic writes and backups, path safety, `explain`, `doctor`, the hook's core logic, snapshot tests, `verified-behaviour.md` discipline.

Removed: profiles, `--global`, per-grant reference counting, `state.json`, Bash allow rules in grants, redundant `allowWrite` entries.

Every v1 profile field maps to the policy:

| v1 field | v2 |
|---|---|
| `edit_paths` | `write` with `access = "full"` (any path, so templates are not needed) |
| `edit_ask` + `sandbox_deny_write` (`edit-ask`) | `access = "ask"` |
| `sandbox_deny_write` alone (`bash-readonly`) | `access = "edit"`, or `raw.sandbox.deny_write` |
| `sandbox_write` | `access = "bash"` |
| `edit_deny`, `protect` | `access = "deny"`, `protect`, or a protected path with ceiling `deny` |
| `edit_hook` | `baseline` (ask) / `strict` (deny) |
| `bash_allow` / `ask` / `deny` | `commands.allow` / `ask` / `deny` |
| `read_deny` | `read.deny` |
| `settings` | `raw.settings` |
| `extends` | `presets` |
| profiles `floor`, `secrets`, `lock`, `confine-edits` | `baseline` |

Migration: none automatic. Revoke v1 grants with v1 before installing v2 (there is one user, and v1 was never released).

## 14. Out of scope (for now)

- **Network.** Reads are open, so data could leave over the network. A future preset could manage the sandbox's network allowlist.
- **MCP tools.** They are neither sandboxed nor matched by the hook. A future preset could make `mcp__*` calls ask; `doctor` could list configured servers.
- **Further hardening against a steered agent**: protecting `.git/hooks`, `.git/config` and `.envrc` in grants, and `~/.claude/` agents. Candidates for `strict`.
- **Bash inside unmanaged projects.** A protected path inside a project that has no section in the policy is writable by Bash, because the project is writable by default and guard emits `denyWrite` only into files it manages. This is narrower than it sounds: Claude Code already keeps Bash out of the working directory's `.claude/` (hook scripts included), `.mcp.json` and dotfiles (VB-S15). The Edit/Write tools are still asked by the hook.
- **Platforms.** Verified on WSL2 only. The design is platform-aware (system folders, drives, sandbox prerequisites) but macOS and native Linux are untested.

## 15. Verification

Phase 2 (`phase2-verification.md`, results in `phase2_results.md`, Claude Code 2.1.321) checked what this design relies on:

| Question | Answer | Design change |
|---|---|---|
| Project folder writable by Bash by default | Yes, and `$TMPDIR` (`/tmp/claude-<uid>`); not `/tmp` or `/tmp/claude` (VB-S4) | Proposals go under `$TMPDIR` |
| Tool caches | Read-only; `uv sync` fails (VB-S16) | `baseline` opens each tool's configured cache |
| Live reload of rules, hooks, `denyWrite` | Yes, within seconds (VB-S6) | No restart advice |
| `~/`, `**/`, `.env.*` deny patterns | All block the file tools; only concrete paths reach Bash (VB-S7, VB-S13, VB-S14) | Concrete `denyWrite` for settings files inside grants; `.env` in the project readable by Bash (accepted) |
| `lock` settings | Bypass refused silently (VB-P6); `allowUnsandboxedCommands` not yet shown (VB-U5) | `doctor` explains the silent fallback |
| `ask` command rules on sandboxed commands | Prompt, and match the bare command (VB-R1) | none |
| Project `.claude/` in auto mode | File tools: silent, including the shared `settings.json` (VB-E9). Bash: read-only, built in (VB-S15) | Locked-settings deny rules are needed; skills stay frictionless |
| Project root for hooks and settings | `CLAUDE_PROJECT_DIR` = launch folder (VB-H7); repo root settings also read (VB-P5) | Hook uses the git root containing `CLAUDE_PROJECT_DIR` |
| `edit` on `/mnt/c` from a project-local file | Works (VB-S8, VB-P4) | none |
| `modify`; NotebookEdit; MultiEdit | Works; reaches the hook; MultiEdit no longer exists (VB-H8) | none |
| Hook `ask`/`deny` across modes | Same in auto, acceptEdits, default (VB-H8) | none |
| Prompts: diff and "allow for this session" | Diff opens in the IDE; the session option does not bypass `ask` (VB-E7, VB-E10) | `ask` ceilings confirmed |
| `! guard review` | Unsandboxed but no terminal or input (VB-P7) | Review in a separate terminal |

Still open, none blocking: `allowUnsandboxedCommands` refusal (VB-U5, VB-U16), folders above the git root (VB-U14), Bash write scope when started in a repo sub-folder (VB-U15), built-in `.claude/` protection inside grants (VB-U17), and live reload of the user settings file (VB-U3). The design fails safe on each. A short follow-up runbook can settle them before the first release.
