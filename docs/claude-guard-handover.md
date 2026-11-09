# Handover: build `claude-guard`

You are a fresh Claude Code agent picking up a design worked out in an earlier planning session. This document is the full brief. **Read all of it before doing anything, then start at Phase 0.** Work one phase at a time and check in with me between phases.

Companion file: `claude-permissions.md` (the researched reference on Claude Code permissions, hooks and the sandbox, including empirical test results). I will place a copy in the new repo, or tell you where it is. Read it too. It is the source of truth for the syntax in this brief, and it marks which facts are **UNVERIFIED**.

---

## 1. Context

### Who I am
Senior data scientist / software developer. Comfortable with Python, git, the terminal and Claude Code. I don't need basics explained, but I want to approve anything that touches my settings or my filesystem.

### Environment
- WSL2 (Linux 6.x) on Windows. I have **multiple Windows drives**, mounted under `/mnt/<letter>` (9p/drvfs). **`/mnt/c` is my Windows system drive**; `/mnt/d` holds my Obsidian vault and other data. Treat every `/mnt/<letter>` as a whole-drive root that must never be granted, and treat the system drive as especially sensitive (`/mnt/c/Windows`, `/mnt/c/Program Files*`, `/mnt/c/ProgramData`, `/mnt/c/Users/*` outside explicit grants). Do not assume only `/mnt/d` exists: drives may come and go, so detect mounts at runtime (`/mnt/*`, or `findmnt -t 9p`) rather than hard-coding letters. Always quote paths that contain spaces.
- Only `/mnt/d` has been tested with the sandbox so far; `/mnt/c` is the same kind of mount but is **untested**. Do not assume the results carry over; include `/mnt/c` in the Phase 0 sandbox tests, using a throwaway folder (e.g. `/mnt/c/Users/<me>/claude-guard-test`) that I create and remove myself.
- Claude Code 2.1.x. I run it in **auto permission mode**.
- `bubblewrap` and `socat` are installed (needed for Claude Code's native sandbox).
- Python tooling via `uv`.

### The problem
Claude runs shell commands and edits files as my user, with access to everything my WSL user can reach, including the Windows drives. Rules for Bash match command *text*, which can be rephrased (`/bin/rm`, `bash -c '...'`, `python -c ...`). I want to give Claude write/delete access only in folders I explicitly whitelist, for commands I explicitly agree, with reads unrestricted. Earlier I turned off "auto-edit outside the project folder" and was prompted for everything, which was too annoying. I want protection **and** few prompts.

### What I want
A small, general tool, usable with any project, that makes it easy to **grant, inspect and revoke** write access to folders, with permissions bundled into reusable named profiles. First use: my Obsidian Zettelkasten vault (see section 8).

### What the tool is, and is not
- It **is a convenience and consistency layer that generates and removes entries in Claude Code's built-in settings** (permission rules, sandbox paths, hooks).
- It is **not** a command parser, not a security boundary of its own, and adds no protection beyond what the built-in features give. Do not build a shell-command classifier. This was considered and deliberately rejected: parsing shell text is bypassable, and the native sandbox enforces the boundary at OS level without parsing.
- Its value: one command to grant or revoke a folder instead of editing three or four places; reusable bundles; clean undo; a readable status view.

---

## 2. How the protection actually works (so you build the right thing)

Layers, from least to most robust:

1. **Permission rules** (`allow` / `ask` / `deny`) in settings files. Evaluated deny, then ask, then allow. A deny anywhere wins. `Edit(path)` rules are path-aware and cover Edit/Write. `Bash(pattern)` rules match command text only and **cannot be scoped to a directory**.
2. **Native sandbox** (`sandbox` settings): Bash commands run inside an OS-enforced (bubblewrap) jail where only listed folders are writable. This is the only thing that makes "this command may only affect this folder" true, regardless of how the command is spelled. **It does not cover the built-in Edit/Write/Read tools**, so path rules cover those.
3. **Hooks**: scripts run before tool calls. Optional here, for a small "hard floor" only (Phase 4).
4. **Managed settings**: root-owned, non-overridable. Optional hardening, needs `sudo`.

Therefore the generated config for a grant combines:
- `Edit(//<abs path>/**)` **allow** rules (file-edit tools),
- `sandbox.filesystem.allowWrite` entries (Bash writes),
- `Bash(...)` **allow** rules for the commands in the chosen profiles (these are global, not path-scoped, but the sandbox bounds where they can write),
- `Edit(...)` **deny** rules for protected sub-paths (e.g. `.obsidian/**`).

### Verified facts you can rely on (tested 2026-11-08 on this machine)
- Native sandbox enabled via inline override works on `/mnt/d` (drvfs) and in WSL home: writes and `rm` (including inside `bash -c`) succeed in allowed folders with **no approval prompts**; writes and deletes in sibling folders fail with `Read-only file system`.
- Allowed folders **must exist before the session starts**; the sandbox cannot create them.
- Sandbox denials look like plain `Read-only file system` errors and never mention the sandbox.
- In auto mode, plain `rm` of an existing file on `/mnt/d` is **not** gated. Only `rm` inside a `-c` script outside the native sandbox triggered the prompt "could not be checked".
- Test launch command used: `claude --settings '{"sandbox":{"enabled":true,"allowUnsandboxedCommands":false,"filesystem":{"allowWrite":["<dirs>"]}}}'` (applies to that session only, writes no file).

### Syntax gotchas (from the docs)
- Rule paths: `//abs/path` is absolute, `~/x` is home, `/x` is relative to the *settings file's source* (a trap), `x` or `./x` is cwd-relative. `**` crosses directories, `*` does not.
- Sandbox paths use ordinary absolute paths (`/mnt/d/...`). **Rule paths and sandbox paths use different conventions.** Test this explicitly.
- Rules for `Write(...)`/`MultiEdit(...)` do nothing; use `Edit(...)`. NotebookEdit needs its own `Edit` deny.
- Project-level `permissions.allow` only takes effect after workspace trust is accepted; project-level `deny`/`ask` apply immediately.
- Permission arrays **merge** across settings files. Never overwrite whole arrays; only add and remove the entries you own.
- Precedence: managed, then command line, then `.claude/settings.local.json`, then `.claude/settings.json`, then `~/.claude/settings.json`.

### UNVERIFIED items to resolve early (Phase 0)
Do not guess these. Check the current docs and test:
1. The sandbox setting (if any) that auto-approves sandboxed Bash commands, its name and default. My tests showed no prompts in auto mode, but that may be auto mode's own judgement; behaviour in default and acceptEdits modes is untested.
2. Whether sandbox `allowWrite` and permission rules are merged (does an `Edit` allow imply sandbox write access, or must both be set?).
3. Whether changing allowed paths mid-session takes effect, or needs a restart.
4. Exact sandbox path semantics for `~`, relative paths and paths with spaces.
5. The exact schema for a hook `onFailure` and any `updatedInput` (only if Phase 4 happens).

---

## 3. Design decisions already made

- **Separate repo**, name `claude-guard`. Not inside the Obsidian tools repo. Python project managed with `uv`, own git repo, tests, `pyproject.toml`. Console script `guard`.
- **Generator, not enforcer.** The tool edits Claude Code settings JSON. Claude Code does the enforcing.
- **Policy lives outside any folder Claude can write to.** The tool's state is `~/.config/claude-guard/` (state file, profile definitions, backups). Claude must not be able to widen its own permissions:
  - The sandbox already protects `~/.claude` and project `.claude` settings from Bash writes, but **the built-in Edit/Write tools are not sandboxed**, so the tool must also generate `Edit` **deny** rules covering the settings files it manages and its own state directory.
  - Grants are made by me running `guard ...` (e.g. via `! guard allow ...`), never by the agent. If the agent needs access, it should tell me the exact command.
- **Reads are unrestricted** except an optional small read-deny list (secrets): `~/.ssh`, `~/.aws`, and `.env` files. Reason: anything read enters model context and transcripts. Offered as a toggleable profile `secrets`, on by default but easy to turn off. Ask me.
- **Settings scope** is a per-grant choice, `--scope user|local`:
  - `user` writes `~/.claude/settings.json` (applies to all projects).
  - `local` writes the project's `.claude/settings.local.json` (personal, uncommitted); default for project-specific grants.
  - Never write `.claude/settings.json` (shared, committed) unless I pass an explicit flag.
- **Tracking removals:** JSON has no comments, so the tool keeps a state file (`~/.config/claude-guard/state.json`) recording exactly which entries each grant added, to which file. `revoke` removes exactly those entries and nothing else. Use reference counting: an entry shared by two grants is only removed when the last grant using it goes.
- **Never clobber.** Preserve unknown keys, key order and formatting where practical; deduplicate; write atomically (temp file then rename); keep a timestamped backup before each write; validate that the result is valid JSON before replacing.
- **No auto-apply:** every mutating command shows a diff and requires confirmation unless `--yes`; `--dry-run` prints the diff and changes nothing.
- **Do not edit my real settings during development.** Tests run against a temporary `HOME` / temporary project dir. The first real use is something I trigger explicitly.

---

## 4. Interface

```
guard allow <folder> [profile ...] [--scope user|local] [--project <dir>] [--protect <glob> ...] [--create] [--dry-run] [--yes]
guard revoke <folder> [profile ...] [--scope ...] [--dry-run] [--yes]
guard status [--project <dir>] [--json]
guard profiles                 # list profiles and what each expands to
guard profile show <name>
guard doctor                   # sanity checks (see below)
guard init-floor [--dry-run]   # optional: add the hard-floor deny rules (Phase 3)
```

Behaviour notes:
- `<folder>` is resolved to an absolute real path (resolve symlinks, reject `/`, `~`, any drive root `/mnt/<letter>` for every mounted drive, and any path above a sane depth unless `--force-broad`). Refuse to grant write access to the filesystem root, the home directory, or a whole `/mnt/<drive>`.
- Extra-strict on the system drive: refuse grants at or above `/mnt/c/Windows`, `/mnt/c/Program Files`, `/mnt/c/Program Files (x86)`, `/mnt/c/ProgramData`, `/mnt/c/Users` and the top level of `/mnt/c/Users/<me>`, with **no `--force-broad` override**. Grants of a specific project folder under the user profile are fine.
- Prefer detecting drive roots from the live mount table over a hard-coded list, and have `guard doctor` report any granted folder that sits on the system drive.
- `--create`: create the folder if it does not exist (the sandbox cannot). Without it, warn clearly, because a nonexistent allowed folder silently fails inside the sandbox.
- `guard allow` with no profile uses the default profile `edit`.
- `guard status` shows, per scope: granted folders, their profiles, protected sub-paths, and the underlying settings entries. Mark entries not managed by the tool as "unmanaged" and never touch them.
- `guard doctor` checks: `bwrap` and `socat` installed; sandbox enabled in effective settings; `allowUnsandboxedCommands` is false; `disableBypassPermissionsMode` set; every granted folder exists; no managed entry was edited by hand; settings JSON valid; state file consistent with settings. Report only.
- `guard explain "<command>"` was proposed earlier. **Treat as a stretch goal and be careful**: without a real parser it can mislead. If built, it should only say which *generated rules and sandbox paths* a literal command string would match, and say loudly that it does not predict what the shell will do. Ask me before building it.

### Profiles
Profiles are defined in TOML, shipped as defaults and extensible by me in `~/.config/claude-guard/profiles.toml`. A profile can `extends` others. Each profile contributes any of:
- `edit_paths`: templates expanded per folder into `Edit(//<folder>/**)` allow rules.
- `sandbox_write`: whether the folder goes into `sandbox.filesystem.allowWrite`.
- `bash_allow`: `Bash(...)` allow patterns (global).
- `bash_deny` / `bash_ask`: `Bash(...)` deny/ask patterns (global).
- `protect`: path globs, relative to the folder, turned into `Edit(...)` deny rules (and `sandbox.filesystem.denyWrite` if that is confirmed to work).

Default profiles to ship (names are suggestions; refine with me):

| Profile | Contents |
|---|---|
| `edit` | Edit allow for the folder, sandbox allowWrite, basic `Bash` allows for `mkdir`, `touch`, `cp`, `mv` |
| `delete` | `Bash(rm *)` allow (non-recursive intent; the sandbox confines it) |
| `delete-recursive` | `Bash(rm -r *)`, `Bash(rm -rf *)` allow; off by default; warn |
| `git-safe` | `Bash(git status *)`, `diff`, `log`, `add`, `commit`, `restore`, `revert`, `show` |
| `git-destructive` | allow nothing; instead **ask** for `reset --hard`, `clean`, `checkout .`, `push --force`, `branch -D`. (Ask, not allow.) |
| `python` | `Bash(uv run *)`, `Bash(uv sync *)`, `Bash(pytest *)`, `Bash(ruff *)` |
| `secrets` | `Read(...)` deny for `~/.ssh/**`, `~/.aws/**`, `//**/.env` (global, not folder-specific) |
| `lock` | `permissions.disableBypassPermissionsMode: "disable"`, sandbox `enabled: true`, `allowUnsandboxedCommands: false` |

Because `Bash` allow rules are global and not path-scoped, **document clearly** that they are safe only because the sandbox bounds where writes can land. `guard doctor` must warn if a profile adds write-capable Bash allows while the sandbox is not enabled.

Always-on protections the tool should add when it first manages a scope (idempotent, tracked like any other entry, removable via `guard revoke --all-floor` or similar):
- `Edit` deny on the Claude settings files it manages and on `~/.config/claude-guard/**` (so the agent cannot rewrite its own policy via the unsandboxed Edit tool).

---

## 5. Build plan

### Phase 0: Foundations
1. Read `claude-permissions.md` and this brief. Check the current Claude Code docs (permissions, settings, sandboxing) for every syntax detail you rely on. **Do not hallucinate tool syntax.**
2. Resolve the UNVERIFIED items in section 2 by reading docs and, where needed, small tests. For sandbox tests use the inline `claude --settings '...'` launch (it writes no files), run in a *separate* session I start, and throwaway folders outside any project. Give me the exact commands and a prompt to paste; I will run them and paste results back. Remember: you cannot see approval prompts, so ask me which steps prompted.
3. Scaffold the repo: `uv init`, `src/claude_guard/`, `tests/`, console script `guard`, `ruff`, `pytest`. Confirm the Python version with me.
4. Confirm open questions in section 7.

**Checkpoint:** stop for my review.

### Phase 1: Core library (no CLI yet)
- Settings model: load a settings JSON file, add/remove entries in `permissions.allow|ask|deny`, `sandbox.*`, `permissions.disableBypassPermissionsMode`, preserving unknown keys.
- Path handling: resolve to absolute, convert to rule form (`//...`) and sandbox form (`/...`), reject unsafe grants, handle spaces and symlinks.
- Profile loader (TOML), `extends` resolution, template expansion.
- State tracking with reference counting; atomic writes; timestamped backups.
- Thorough pytest suite against temp dirs: grant, revoke, idempotence, overlapping grants, shared entries, hand-edited settings, malformed JSON, unknown keys preserved, round-trip (grant then revoke returns the original file byte-identical where possible), refusal of `/`, `~`, `/mnt/d`.

### Phase 2: CLI
`allow`, `revoke`, `status`, `profiles`, `profile show`, `doctor`, with diff output, `--dry-run`, `--yes`, `--json`. Snapshot-test the diffs.

### Phase 3: Hard floor (optional, ask me first)
`guard init-floor`: adds deny/ask rules for obviously catastrophic patterns (`rm -rf /`, `rm -rf ~`, `rm -rf /mnt/*` and each `/mnt/<letter>` including `/mnt/c`, `git reset --hard`, `git clean -fd`, force-push, `dd of=/dev/*`, `mkfs*`), acknowledging these are text-pattern rules and bypassable (`/bin/rm`, `bash -c`); they are belt-and-braces on top of the sandbox. Optionally a small fail-closed PreToolUse hook (`"onFailure": "block"`) that resolves paths and unwraps `bash -c`/`sudo`/`env` for the same floor, with a decision log and a strong test suite. Only build the hook if I ask.

### Phase 4: Plugin packaging (optional)
A Claude Code plugin exposing `/guard status` (read-only display) and instructing the agent to tell me the exact `! guard ...` command when it hits a sandbox denial. Check the current plugin and skill authoring docs first. The slash command must not be able to grant permissions by itself.

### Phase 5: Real-world trial
With my approval, apply the Obsidian vault configuration (section 8), then I use it for a while before we refine profiles. Document findings in `claude-permissions.md`.

---

## 6. Working agreement

- **Start each session** by reading this file and checking `git status` of the repo. Ask before proceeding if there are uncommitted changes.
- **Propose, then do.** For anything touching `~/.claude/settings.json`, any `.claude/settings*.json`, `/etc/claude-code`, or any path outside the new repo: show the exact diff or command first and wait for my OK. Never edit my real settings during development.
- **Never delete** files outside the repo. Cleanup of test artefacts: only paths you created, and tell me what you removed.
- **Commit** to the repo in small descriptive commits, only when I ask. Do not push anywhere; no remote for now.
- **Tests first** for the settings-mutation code. A bug here edits security config.
- **Be honest about limits.** If something cannot be enforced (Bash rule bypasses, unsandboxed Edit tool), say so in the output and in the docs. Do not oversell.
- **Report outcomes faithfully**: failed tests, skipped steps, things you could not verify.
- Keep a `README.md` with a "what this does and does not protect against" section, and an `ARCHITECTURE.md` or docstrings explaining the state file format.

## 7. Open questions to ask me as you reach them
1. Python version (the other repo used 3.15, which I said was fine; confirm for this one).
2. Is the `secrets` read-deny profile on by default?
3. Should `guard allow` run `mkdir` for missing folders by default, or only with `--create`?
4. Do I want `guard explain`, given its limits?
5. Phase 3: rules only, or rules plus a hook?
6. Managed settings (`/etc/claude-code`, needs `sudo`) for the `lock` profile, or user settings only?
7. Default scope for `guard allow`: `local` (my recommendation) or `user`?

## 8. First configuration: the Obsidian Zettelkasten vault

Not for Phase 1 to 4; this is the Phase 5 target and a design driver.
- Vault: `/mnt/d/Obsidian Vaults/Zettelkasten` (Windows-native, synced between my devices by Obsidian Sync, so writes propagate within seconds).
- The vault is a git repo from WSL (not yet initialised); no branching or checkouts, because that rewrites the working tree and Sync would push the churn.
- Grants wanted: `edit`, `git-safe`, probably `delete` (non-recursive); not `delete-recursive`, not `git-destructive`.
- Protected (never writable): `.obsidian/**`, `Kindle/**`. Never delete notes.
- Companion tools repo (separate, already scaffolded): `~/Repositories/Obsidian-Zettelkasten` (Python, `uv`), which gets `python` and `edit` grants as its own project directory.
- The vault contains private material; reads are unrestricted but nothing should be added to a remote.

## 9. Definition of done (v1)
- `guard allow/revoke/status/profiles/doctor` work, tested, with dry-run and diffs.
- Grant then revoke restores the settings file exactly; hand-edits and unknown keys survive.
- A real session launched after `guard allow` shows: writes and deletes succeed in the granted folder with no prompts, writes elsewhere fail, as in my earlier manual test, repeated through the generated settings (not the inline override).
- README states plainly what is and is not protected.
- No UNVERIFIED item from section 2 remains unflagged.
