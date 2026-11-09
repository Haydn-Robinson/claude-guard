# Claude Code permissions and guardrails

Reference notes on the layers of control available in Claude Code, compiled while designing guardrails for the Obsidian Zettelkasten workflow. Sources: the Claude Code docs (permissions, hooks, sandboxing, settings, managed-settings pages at code.claude.com/docs/en/), fetched 2026-11-08, plus design discussion. Installed version when written: Claude Code 2.1.319.

Items marked **UNVERIFIED** were not confirmed in the docs text read, and should be tested before being relied on.

---

## 1. The problem

Claude runs shell commands and edits files as the user. Without extra controls, a Bash command runs with all the permissions of the WSL user, which includes the Windows drives under `/mnt/` (here `/mnt/d` is mounted over 9p/drvfs). The only protection is permission prompts and rules, and rules for Bash match the *text* of a command, which can be written many ways. Reports of an agent deleting a whole filesystem are the failure mode being guarded against.

Goals:
- Reads of any file are fine (see the secrets caveat in section 8).
- Destructive or altering actions are allowed only in explicitly whitelisted folders, and only for agreed commands.
- Few prompts. The earlier "ask about everything outside the project folder" setup was too annoying.

---

## 2. The layers

Ordered from least to most robust against bypass.

| Layer | What it is | Enforced by | Bypassable by rephrasing a command? |
|---|---|---|---|
| Permission modes | Default prompting behaviour | Claude Code | n/a |
| Permission rules | allow / ask / deny patterns | Claude Code | Yes, for Bash. Mostly no for Edit/Read paths |
| Hooks | A script run before/after a tool call | Claude Code runs your code | Partly: depends on how good the script's parsing is |
| Sandbox | OS-level jail for Bash commands | The kernel (bubblewrap) | No, for anything running inside it |
| Managed settings | Root-owned settings that nothing else can override | Claude Code | No (cannot be overridden by user/project settings) |

Defence in depth: use several. No single layer covers everything.

---

## 3. Settings files and precedence

Highest precedence first:

1. Managed settings: `/etc/claude-code/managed-settings.json` (Linux/WSL), plus drop-ins in `/etc/claude-code/managed-settings.d/*.json`, merged alphabetically.
2. Command line (`--settings`).
3. `.claude/settings.local.json` (project, personal, not committed).
4. `.claude/settings.json` (project, shared).
5. `~/.claude/settings.json` (user, all projects).

For permission rules, arrays **merge** across files, and a **deny in any scope blocks** even if a higher-precedence scope allows. A user-level deny beats a project-level allow.

Notes:
- Project `permissions.allow` and `additionalDirectories` only take effect after workspace trust is accepted. Project-level `deny` and `ask` apply immediately.
- `permissions.defaultMode` values `auto` and `bypassPermissions` do not take effect from project or local settings (since v2.1.257). Set them in user or managed settings.
- A managed settings file that fails to parse stops Claude Code from starting.
- On WSL the WSL user can write to `/etc/claude-code`, so managed settings protect against mistakes and against project-level overrides, not against a determined user. The `wslInheritsWindowsSettings` setting relates to Windows-side policy (not investigated).
- Verify the active sources with `/status` and look for `Enterprise managed settings (file)` on the `Setting sources` line.

---

## 4. Permission modes

- **default**: prompts for most things.
- **acceptEdits**: auto-approves file edits.
- **plan**: read-only planning.
- **auto**: model-judged approvals.
- **bypassPermissions**: no checks. Disable with `"permissions": {"disableBypassPermissionsMode": "disable"}` (any settings file, typically managed). `disableAutoMode` is the equivalent for auto mode.

---

## 5. Permission rules

```json
{
  "permissions": {
    "allow": ["Bash(git commit *)"],
    "ask":   ["Bash(git clean *)"],
    "deny":  ["Read(//**/.env)", "Edit(//mnt/d/Obsidian Vaults/Zettelkasten/.obsidian/**)"]
  }
}
```

### Evaluation
- Order: **deny, then ask, then allow**. First match wins. Specificity does not matter (`Bash(aws *)` in deny blocks `Bash(aws s3 ls)` even if allowed).
- If nothing matches, the default mode decides (usually a prompt).
- A bare tool name in `deny` (e.g. `"WebFetch"`) removes the tool from Claude's context entirely.
- A hook returning "allow" cannot override a deny rule.

### Tool specifiers

| Tool | Matches | Example |
|---|---|---|
| `Read(path)` | reading files | `Read(//**/.env)` |
| `Edit(path)` | Edit, Write, MultiEdit | `Edit(//mnt/d/Vault/**)` |
| `Bash(pattern)` | command text, `*` wildcard | `Bash(git commit *)` |
| `WebFetch(domain:x)` | fetches | `WebFetch(domain:github.com)` |
| other | WebSearch, subagents, `mcp__server__tool` | by name |

- `Write(...)` and `MultiEdit(...)` rules do nothing; use `Edit(...)`.
- A Read deny also blocks Edit/Write on that path (including creating the file).
- NotebookEdit is not covered by Edit rules; add an `Edit` deny for notebooks separately.
- `mcp__` rules with parentheses are skipped. Use `--disallowedTools` for MCP parameter matching.

### Path syntax (Read and Edit use gitignore-style patterns)

| Pattern | Meaning |
|---|---|
| `//path` | absolute from filesystem root, e.g. `Edit(//mnt/d/Vault/**)` |
| `~/path` | relative to home |
| `/path` | relative to the *settings source*, NOT the filesystem root (trap) |
| `path` or `./path` | relative to cwd |

- `*` stays within one path segment; `**` crosses directories.
- Allow rules with a single-directory relative pattern (`Edit(src/**)`) match only the anchored location; deny/ask rules with the same pattern match at any depth. Use `Edit(**/src/**)` in allow rules for any depth.
- Sandbox paths use *standard* conventions (`/mnt/d/...` is absolute), which differs from rule paths (`//mnt/d/...`).

### Bash rule behaviour
- `*` matches any text. Put it after the subcommand: `Bash(git log *)` is safe; `Bash(git * main)` is broad. `Bash(ls *)` does not match `lsof`.
- Compound commands (`&&`, `||`, `;`, `|`, `|&`, `&`, newlines) are split, and deny/ask apply inside subshells and `$(...)`.
- Edit/Read deny rules apply to recognised file commands (`cat`, `head`, `tail`, `sed`, `tee`) and to redirection targets (`> file`, `< file`). They do **not** apply to commands that name no file (`grep -r pattern .`) or to scripts/interpreters that open files themselves.

### What a Bash rule does not match

| Rule | Stops | Does not stop |
|---|---|---|
| `Bash(curl *)` | `curl https://example.com` | `/usr/bin/curl ...`, `sh -c 'curl ...'` |
| `Bash(rm *)` | `rm -rf build/` | `/bin/rm -rf build/`, `bash -c 'rm -rf build/'` |
| `Bash(git push *)` | `git push origin main` | `git -C . push origin main`, `git -c push.default=current push origin main`, `git 'push' origin main` |

Also not matched:
- Variables: `URL=http://github.com && curl $URL` defeats URL-restricted allow patterns.
- Wrapper runners (`npx`, `docker exec`, `devbox run`, `mise exec`, `direnv exec`) are not stripped. `Bash(devbox run *)` matches `devbox run rm -rf .`.
- Interpreters (`python -c`, `node -e`) and any script that opens files itself.
- `find ... -exec/-delete` and exec wrappers (`watch`, `flock`) prompt even when the prefix is allowed.
- Allow-rule argument constraints are fragile.

**Key limitation:** Bash rules cannot be scoped to a directory. `Bash(rm *)` is banned or allowed everywhere. Path-scoped enforcement for Bash needs a hook or the sandbox.

### Additional directories
`permissions.additionalDirectories` adds folders to the working set so Claude can read and edit there under the normal rules. This is the built-in version of "whitelist a folder".

---

## 6. Hooks

A hook is a program run at a point in the tool lifecycle (PreToolUse, PostToolUse, session start/end, and others). It receives JSON on stdin and can allow, deny, ask or defer.

### Configuration (three levels: event, matcher group, handler)
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "if": "Bash(rm *)",
            "command": "node",
            "args": ["${CLAUDE_PROJECT_DIR}/.claude/hooks/check-command.js"],
            "onFailure": "block"
          }
        ]
      }
    ]
  }
}
```
- Handler fields: `type` (command, http, mcp_tool, prompt, agent), `command`, `args` (exec form, no shell), `if` (permission-rule syntax, evaluated on tool events), `timeout` (default 600s for command hooks), `statusMessage`, `onFailure`.
- Matcher: `"*"`, `""` or omitted = all tools. Letters/digits/`_`/`-`/spaces/`,`/`\|` = exact name or list (`Edit|Write`). Anything else is an unanchored JavaScript regex. MCP tools: `mcp__memory__.*`.
- Matching hooks run in parallel. Hooks in plan mode cannot approve non-read-only calls.

### Input (stdin JSON)
Common: `session_id`, `prompt_id`, `transcript_path`, `cwd`, `scratchpad_dir`, `permission_mode`, `effort`, `hook_event_name`, and `agent_id`/`agent_type` inside subagents. PreToolUse adds `tool_name`, `tool_input` (varies by tool), `tool_use_id`.

```json
{
  "session_id": "abc123",
  "cwd": "/home/user/my-project",
  "permission_mode": "default",
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": {"command": "npm test", "description": "...", "timeout": 120000, "run_in_background": false},
  "tool_use_id": "toolu_01ABC123..."
}
```
The field list is from the common-field list plus the doc example; the decision-control page was only partly read.

### Output
- **Exit 0**: stdout JSON is applied. No output means no decision and normal permissions continue (silence does not approve).
- **Exit 2**: blocking error. The call is blocked, even if stdout has JSON allow. stderr is sent to Claude as the reason.
- **Other non-zero**: non-blocking error; the call proceeds, and the first stderr line is shown to the user only.
- JSON form (exit 0, JSON only on stdout):
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "Database writes are not allowed"
  }
}
```
Decision values: `allow`, `deny`, `ask`, `defer`. An `updatedInput` rewrite exists but its schema was **not read (UNVERIFIED)**.

Minimal shell example from the docs:
```bash
#!/bin/bash
input=$(cat)
command=$(jq -r '.tool_input.command' <<<"$input")
if [[ "$command" == rm* ]]; then
  echo "Blocked: rm commands are not allowed" >&2
  exit 2
fi
exit 0
```

### Fail-open default
A timeout or crash on a PreToolUse hook does **not** block. For policy hooks set `"onFailure": "block"` (needs v2.1.295 or later; installed 2.1.319 qualifies).

### What hooks can do that rules cannot
1. Resolve real paths (symlinks, `..`, `~`, `$HOME`) before deciding.
2. Parse the whole command: unwrap `bash -c`, `sudo`, `env`, `xargs`; check every command in a pipeline; handle flags in any order.
3. Combine command and location, e.g. `rm` allowed only inside the vault. This is the path-scoped Bash control that rules lack.
4. Apply arbitrary logic: count files, check git state, time of day, permission mode.
5. Fail closed (`onFailure`).
6. Return a reason Claude sees, such as the exact command the user can run to grant access.
7. Log every attempt for audit.
8. Run on other events: after tool calls (auto-format, auto-commit), session start/end (e.g. commit the vault before and after a session).
9. Rewrite tool inputs (UNVERIFIED schema).

### What hooks cannot do
- Override a deny rule.
- Make a command safe. A hook still judges text, so an obfuscated command can slip past a weak parser. Only the sandbox is immune to that.
- Protect themselves if the hook config lives in a file Claude can edit. Register them in managed settings, or protect the settings path.
- Work correctly without tests. A buggy hook silently allows things unless `onFailure` is set, and even then logic bugs permit calls.

---

## 7. The sandbox

### Plain-English summary
Normally Claude's commands run as the user and can touch anything the user can. The sandbox runs each Bash command inside an OS-enforced restricted environment (bubblewrap on Linux/WSL2). Inside it the command can read most things but can write only to listed folders, and network access goes through a proxy with a domain allowlist. A command that tries to write elsewhere gets a permission error from the kernel, regardless of how it was spelled. This does not depend on understanding the command text.

It is **not** a copy of the system and not a place to observe commands safely. Commands really run and really modify the allowed folders. It limits the **blast radius**, not what happens inside the allowed folders. Git commits, protect rules and backups still matter.

It is a **native** Claude Code feature, **off by default**.

### Enabling
```json
{
  "sandbox": {
    "enabled": true,
    "allowUnsandboxedCommands": false,
    "filesystem": {
      "allowWrite": ["/mnt/d/Obsidian Vaults/Zettelkasten"],
      "denyWrite": [],
      "denyRead": [],
      "allowRead": []
    },
    "network": {
      "allowedDomains": ["github.com", "*.npmjs.org"]
    }
  }
}
```
Also `/sandbox`, or `claude --settings '{"sandbox": {"enabled": true, "allowUnsandboxedCommands": false}}'`.

### Requirements
- Linux and WSL2: `bubblewrap` and `socat` (`sudo apt-get install bubblewrap socat`). Installed by the user on 2026-11-08.
- WSL1: not supported. Native Windows: not supported (commands run unsandboxed).
- Ubuntu AppArmor can block bubblewrap. Fix: `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0` (until reboot). The sysctl did not exist on this machine's kernel when checked.

### Semantics
- Default writes: the cwd, a per-user temp dir, and `--add-dir` directories. Default reads: most of the machine, including `~/.ssh` and `~/.aws/credentials`. Use `denyRead` to hide them.
- `allowWrite`, `denyWrite`, `denyRead`, `allowRead`: the narrower path wins.
- Protected even inside writable directories: `.claude` settings and `skills`/`agents`/`commands`/`hooks` dirs, `.mcp.json`, `.bashrc`, `.zshrc`, `.gitconfig` in the working dir, `~/.claude`, `~/.claude.json`.
- Network: no direct route; all traffic through a proxy; the allowlist starts empty. Keys: `network.allowedDomains`, `deniedDomains`, `strictAllowlist`.
- Permissions and sandbox settings are merged into the final sandbox config. The exact mapping (e.g. whether an Edit deny becomes a `denyWrite`) is **UNVERIFIED**.
- A setting to auto-approve Bash commands when sandboxed was recalled but its name and default are **UNVERIFIED**.

### Escape hatch
Claude can retry a command with `dangerouslyDisableSandbox`. It prompts in manual and acceptEdits modes, runs freely in bypassPermissions, and is denied in dontAsk. Turn it off with `"sandbox": {"allowUnsandboxedCommands": false}`.

### Limits
- Built-in Read, Edit, Write, WebFetch, WebSearch, hooks, local MCP servers and status-line commands are **not** sandboxed. `denyRead` does not stop the Read tool. Edit/Write are guarded by path rules and hooks instead.
- `!` shell-mode commands the user types usually run unsandboxed.
- UDP, HTTP/3 and ICMP cannot leave the sandbox.
- On Linux `localhost` is private to the sandboxed command; reaching host services needs `excludedCommands`.
- The proxy does not inspect TLS by default; domain fronting is possible. Broad domains like `github.com` are an exfiltration path.
- `allowNestedUserNamespaces: true` weakens the boundary.
- Behaviour on `/mnt/d` (9p/drvfs) is **verified**: see section 12. `/mnt/c` was not tested but is the same kind of mount.
- Allowed paths must **exist before the session starts**. The sandbox cannot create them, and their parents are read-only inside it (see section 12).

### Overhead
Measured where noted (section 12); the rest are **UNVERIFIED** estimates.
- Operational: a raw `bwrap` launch of a trivial command on this machine took about 25 ms (measured, trivial workload only). `/mnt/d` is already slow over 9p; effect on heavy workloads not measured.
- Breakage: tools writing outside allowed folders (`~/.cache`, `uv` environments, global git config) fail until paths are added. Network tools (`uv add`, `pip`, `git fetch`, `curl`) fail until domains are allowed. Docker needs exclusions. Windows executables called from WSL may not work.
- Cognitive: initial tuning, and sandbox denials look like ordinary "permission denied" errors.
- Tokens: negligible, apart from occasional extra turns when a command fails and is retried.

---

## 8. Reading files and secrets

Reads are allowed everywhere by default and that is the intended stance for most files. The reason to deny reads of `~/.ssh`, cloud credentials (`~/.aws`) and `.env` files is not destruction: anything read enters the model context and so is sent to the API and stored in transcripts. Keys and secrets have no reason to go there. Optional, low cost, user's choice.

---

## 9. What built-in config achieves (without any custom tool)

Settings entries for "vault gets edit and git, with protected paths":

1. Writable folder for file edits: `Edit(//mnt/d/Obsidian Vaults/Zettelkasten/**)` in allow.
2. Protected paths: `Edit(//mnt/d/Obsidian Vaults/Zettelkasten/.obsidian/**)` and `.../Kindle/**` in deny.
3. Commands without prompts: `Bash(git status *)`, `Bash(git commit *)`, `Bash(uv run *)` in allow. These match the command, not the folder.
4. Confine Bash writes: sandbox enabled with `allowWrite` for the vault and the tools repo, `allowUnsandboxedCommands: false`.
5. Catastrophic commands: deny rules for the obvious ones (`git reset --hard`, `git clean`, force-push) plus one small hook for the hard floor (`rm -r` on `/`, `~`, `/mnt/*`), with `onFailure: "block"`, registered in managed settings if tamper resistance is wanted.
6. Lock: `disableBypassPermissionsMode: "disable"`.

Adding a folder means hand-editing one allow rule, one sandbox path and any protect rules. Removing it means finding the same lines. Seeing what is granted means reading the JSON.

### What config alone cannot do
- Scope a Bash command to a directory without the sandbox or a hook.
- Reliably recognise a command that has the same effect as a banned one.
- Group permissions into toggleable bundles.
- Explain in advance why a command would be blocked.

---

## 10. The optional convenience tool (`claude-guard`, separate repo)

Decision so far: if built, it is a thin convenience layer that **generates and removes the built-in settings entries above**. It adds no protection of its own and does no command parsing. It lives in its own repo, not this one.

Sketch of the interface:

| Task | Command |
|---|---|
| Grant a folder with bundles | `guard allow "/mnt/d/Obsidian Vaults/Zettelkasten" vault` |
| Add a bundle later | `guard allow <folder> python` |
| Remove access | `guard revoke <folder>` |
| Show active grants | `guard status` |
| Check a command | `guard explain "rm -r /mnt/d/x"` |

Profiles (named bundles of rules): e.g. `edit`, `move`, `delete`, `git-safe`, `git-destructive` (off by default), `python`, plus per-zone `protect` globs. The tool records which lines it wrote so `revoke` removes exactly those and nothing else. The policy must live outside any directory Claude can write to so the agent cannot widen its own permissions; grants are made by the user running `! guard allow ...`.

What it adds over bare config: one-command grant and revoke, reusable bundles, clean undo, a readable status view, and `explain`. Value grows with how often folders or projects are added.

Plan: do section 9 by hand for the vault first. Build the tool only if that gets repetitive.

---

## 11. Next steps

1. ~~Test the sandbox on `/mnt/d`.~~ Done, see section 12. Still to measure: overhead on real workloads, network behaviour, tool breakage (`uv`, `git`, caches).
2. Design and build `claude-guard` (separate repo) as a generator for the built-in settings, rather than editing settings by hand. Settings are deliberately untouched until then.
3. Write the small floor hook with tests (dangerous and safe commands, symlinks, `../` traversal, `bash -c` wrappers).
4. Decide on managed settings (needs `sudo`) versus user settings.
5. Resolve the remaining **UNVERIFIED** items above (auto-approve setting name, sandbox/permission-rule mapping, hook `updatedInput` schema, behaviour outside auto mode).

---

## 12. Empirical findings (tested 2026-11-08)

Environment: Claude Code 2.1.319, WSL2, `/mnt/d` mounted as 9p (drvfs), bubblewrap 0.6.1, session in **auto permission mode**. The vault was never touched; tests used throwaway folders outside it, all removed afterwards.

### 12.1 Raw bubblewrap works on `/mnt/d`
With `bwrap --ro-bind / / --bind <allowed> <allowed>`:
- Writing inside the allowed folder succeeded.
- Writing to a sibling folder failed with `Read-only file system`.
- Deleting an existing file outside the allowed folder failed for `rm`, `python3 -c "os.remove(...)"` and `bash -c "rm -f ..."`. All three spellings were stopped by the kernel, which is the point of the sandbox.
- Start-up cost was about 25 ms for a trivial command.

### 12.2 When Claude Code asks for approval (auto mode)

| Command shape | Prompted? |
|---|---|
| `rm file` in WSL home, no sandbox (file created in same command) | No |
| `rm file` on `/mnt/d`, no sandbox (file created in same command) | No |
| `rm` of a **pre-existing** file in WSL home | No |
| `rm` of a **pre-existing** file on `/mnt/d` | No |
| `bwrap ... bash -c "... rm ..."` (WSL or `/mnt/d`) | **Yes** |

The prompt text was: `This shell -c script runs rm and could not be checked`, with an automatic block after a timeout (about 2 minutes) to avoid holding up an unattended session.

Conclusions:
- The trigger is **`rm` inside a `-c` script**, which the checker cannot parse. It is not the location, not the sandbox, and not a settings change.
- Plain `rm` is **not gated by location** in auto mode: it ran unprompted on `/mnt/d`, including on a pre-existing file. Auto mode alone is no protection for the Windows drives.
- This is the same gap the docs describe for deny rules (`bash -c 'rm ...'` bypasses pattern matching). In auto mode the checker falls back to asking, which is a useful safety net but not something to rely on.
- The prompts in these tests were caused by a hand-rolled `bwrap ... bash -c` wrapper, not by the native sandbox.

### 12.3 Native sandbox behaviour (inline settings override)
Session launched with:
```
claude --settings '{"sandbox":{"enabled":true,"allowUnsandboxedCommands":false,"filesystem":{"allowWrite":["/mnt/d/claude-rm-test","/home/haydn/claude-rm-test"]}}}'
```
The inline override applies to that session only and writes no settings file.

Results (steps run as separate Bash calls with plain commands):
- Writes, `rm` of existing and new files, and `bash -c "rm ..."` inside the allowed folders all succeeded, on both WSL and `/mnt/d`, with **no approval prompts**.
- `rm` and `>` writes in sibling folders outside the allowed list all failed with `Read-only file system`. The outside files were untouched.
- So the native sandbox enforces the boundary on drvfs, and sandboxed `rm` (including inside `bash -c`) is not interrupted by the "could not be checked" prompt, at least in auto mode.

### 12.4 Practical lessons
1. **Allowed folders must exist before the session starts.** A first attempt failed because the allowed folders did not exist: `mkdir -p` inside the sandbox returned `Read-only file system` and every later step then failed with `No such file or directory`. A grant tool should create missing folders (outside the sandbox) or warn.
2. **Sandbox denials look like ordinary filesystem errors.** The errors read `Read-only file system` and never mention the sandbox. Anyone (human or Claude) debugging an unexpected failure should suspect the sandbox first. Worth a line in `CLAUDE.md`.
3. **A restart may be needed after changing the sandbox's allowed paths.** Settings are read at session start. Whether a mid-session change takes effect was not tested (UNVERIFIED).
4. **Don't build test harnesses around `bwrap` directly when evaluating prompts.** The wrapper changes how the command is checked and produces prompts that the native sandbox does not.
5. **The `/sandbox` slash command cannot be run by Claude itself**; the user runs it to see the mode. Claude can only report the sandbox config it can see.

> **Superseded.** Items below that were untested at the time (modes other than auto, mid-session changes, Edit/Write behaviour) have since been tested. The current, maintained record is `docs/verified-behaviour.md`; section 12.4 item 3 and everything in 12.5 are answered there.

### 12.5 Still untested
- Behaviour outside auto mode (default and acceptEdits). Prompts may differ.
- Whether sandboxed commands are auto-approved by a specific setting rather than by auto mode's own judgement.
- Network restrictions and what breaks (`uv add`, `git fetch`, `pip`).
- Tools that write caches outside allowed folders (`~/.cache`, `uv` environments, global git config).
- Performance on heavy workloads over 9p.
- Edit/Write tool behaviour for paths outside the sandbox list (these tools are not sandboxed; path rules cover them).
- Mid-session changes to allowed paths.
