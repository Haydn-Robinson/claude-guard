# Phase 0b: tests T1 to T4 (new primitives)

These settle four open questions before the new profile primitives and the hook are built.

| Test | Question |
|---|---|
| T1 | Can Bash be made read-only in a folder (except `.git`) while the Edit tool still works, using `denyWrite` plus `allowWrite` on a sub-path? |
| T2 | Does an `Edit` **ask** rule prompt for the Write tool, even in auto mode? |
| T3 | Does a PreToolUse hook run for the Write/Edit tools in auto mode, and do `deny`, `ask` and a crash behave as expected (with and without `onFailure: "block"`)? |
| T4 | Does `Bash(cmd *)` also match a bare `cmd`? |

## Legend (read this first)

- 🖥️ **TERMINAL**: a command **you type yourself** in an ordinary terminal (not inside Claude). `bash` block. Never use the `!` prefix anywhere in this runbook.
- 💬 **CLAUDE**: text **you paste into the test Claude session** as ONE message. `text` block. Do not run those lines yourself.
- Quit each Claude session (`/exit`) before starting the next.
- For every 💬 step, write down **which calls showed an approval prompt**, and copy any block or denial message exactly. Claude cannot see prompts.
- All prompts use literal paths on purpose (variables in redirect targets cause unrelated prompts).

---

## Step 0: setup (once)

🖥️ **TERMINAL**:

```bash
cd ~/Repositories/claude-guard
scripts/phase0/setup-b.sh
```

It creates `/mnt/d/claude-guard-test-b/`, `~/cgb-cwd`, `~/cgb-gitrepo`, `~/cgb-hook.py` and the settings files `~/cgb-T1a.json`, `~/cgb-T1b.json`, `~/cgb-T2.json`, `~/cgb-T3.json`, `~/cgb-T3b.json`, `~/cgb-T4.json`. If git prints a "dubious ownership" or similar warning during setup, copy it to me and stop.

---

## T1: read-only repo except `.git`

The settings give the repo an `Edit` allow (which Claude Code merges into `allowWrite`), a `denyWrite` on the repo, and an `allowWrite` on `repo/.git` only. Two variants of the deny form: `T1a` uses the plain folder, `T1b` uses `folder/**`. Run the same steps for each.

### T1a

1. 🖥️ **TERMINAL**:

```bash
cd ~/cgb-cwd && claude --settings ~/cgb-T1a.json --permission-mode auto
```

2. 💬 **CLAUDE**: paste.

```text
Run each numbered step as its own separate call, using the literal paths exactly as written (no shell variables). Report the exact output or error of each step. Do not retry or work around any failure.
1. Bash: echo hi > /mnt/d/claude-guard-test-b/repo/new-bash.txt
2. Bash: rm /mnt/d/claude-guard-test-b/repo/note.txt
3. Bash: echo hi > /mnt/d/claude-guard-test-b/repo/.git/probe.txt
4. Bash: rm /mnt/d/claude-guard-test-b/repo/.git/probe.txt
5. Write tool: create /mnt/d/claude-guard-test-b/repo/new-tool.txt containing "hi"
6. Edit tool: in /mnt/d/claude-guard-test-b/repo/note.txt replace "orig" with "edited"
7. Bash: git -C /mnt/d/claude-guard-test-b/repo status --short
8. Bash: git -C /mnt/d/claude-guard-test-b/repo clean -f
9. Bash: git -C /mnt/d/claude-guard-test-b/repo add -A
10. Bash: git -C /mnt/d/claude-guard-test-b/repo commit -m t1
11. Bash: mv /mnt/d/claude-guard-test-b/repo/note.txt /mnt/d/claude-guard-test-b/repo/note2.txt
```

Hoped-for result (tell me where reality differs): steps 1, 2 and 11 fail with `Read-only file system`. Steps 3, 4, 5, 6, 7 succeed. Step 8 fails (cannot delete the untracked file). Steps 9 and 10 succeed.

3. 🖥️ **TERMINAL** (second terminal or after quitting): what is actually on disk.

```bash
ls -la /mnt/d/claude-guard-test-b/repo; cat /mnt/d/claude-guard-test-b/repo/note.txt; git -C /mnt/d/claude-guard-test-b/repo log --oneline
```

4. Quit the Claude session.

### T1b

First reset the fixture so both variants start equal:

🖥️ **TERMINAL**:

```bash
git -C /mnt/d/claude-guard-test-b/repo reset -q --hard initial && git -C /mnt/d/claude-guard-test-b/repo clean -fdq
```

(This only touches the test repo, using the `initial` tag that setup created. If it prints an error, tell me.) Then repeat T1a steps 1 to 4 above with:

🖥️ **TERMINAL**:

```bash
cd ~/cgb-cwd && claude --settings ~/cgb-T1b.json --permission-mode auto
```

💬 **CLAUDE**: paste the T1a prompt again, but rename the files `new-bash.txt` to `new-bash-b.txt`, `new-tool.txt` to `new-tool-b.txt`, and the commit message to `t1b`. Quit afterwards. Tell me whether the results match T1a.

---

## T2: does `Edit` ask prompt in auto mode?

Settings: `Edit` **ask** on `asked/`, `Edit` allow on `free/`, sandbox on with both folders writable.

1. 🖥️ **TERMINAL**:

```bash
cd ~/cgb-cwd && claude --settings ~/cgb-T2.json --permission-mode auto
```

2. 💬 **CLAUDE**: paste.

```text
Run each numbered step as its own separate call, using the literal paths exactly as written. Report the exact output or error of each. Do not retry or work around any failure.
1. Write tool: create /mnt/d/claude-guard-test-b/asked/a.txt containing "hi"
2. Write tool: create /mnt/d/claude-guard-test-b/free/b.txt containing "hi"
3. Write tool: create /mnt/d/claude-guard-test-b/outside/c.txt containing "hi"
4. Bash: echo hi > /mnt/d/claude-guard-test-b/asked/d.txt
5. Bash: echo hi > /mnt/d/claude-guard-test-b/free/e.txt
```

Tell me for each step: **prompted / ran silently / blocked**. If a prompt appears, choose "No" for step 1 only if offered, so we also see the denied outcome; otherwise approve. Note which you did.

3. Quit, then repeat in acceptEdits mode, steps 1 to 3 only (same prompt text minus steps 4 and 5):

🖥️ **TERMINAL**:

```bash
cd ~/cgb-cwd && claude --settings ~/cgb-T2.json --permission-mode acceptEdits
```

💬 **CLAUDE**: paste the prompt with only steps 1 to 3 and rename the files `a2.txt`, `b2.txt`, `c2.txt`. Report prompts. Quit.

---

## T3: PreToolUse hook on Write/Edit

The hook script `~/cgb-hook.py` logs every call and decides by the path: `/hook-deny/` returns deny, `/hook-ask/` returns ask, `/hook-crash/` crashes, anything else passes silently. `T3.json` registers it with `"onFailure": "block"`.

### T3 (fail-closed)

1. 🖥️ **TERMINAL**:

```bash
cd ~/cgb-cwd && claude --settings ~/cgb-T3.json --permission-mode auto
```

2. 💬 **CLAUDE**: paste.

```text
Run each numbered step as its own separate call, using the literal paths exactly as written. Report the exact output or error of each, including any message that says why something was blocked. Do not retry or work around any failure.
1. Write tool: create /mnt/d/claude-guard-test-b/hook-free/a.txt containing "hi"
2. Write tool: create /mnt/d/claude-guard-test-b/hook-deny/a.txt containing "hi"
3. Write tool: create /mnt/d/claude-guard-test-b/hook-ask/a.txt containing "hi"
4. Write tool: create /mnt/d/claude-guard-test-b/hook-crash/a.txt containing "hi"
5. Edit tool: in /mnt/d/claude-guard-test-b/hook-free/existing.txt replace "orig" with "edited"
6. Bash: echo hi > /mnt/d/claude-guard-test-b/hook-deny/b.txt
```

Tell me for each step: **prompted / ran silently / blocked**, and for step 3 whether a prompt appeared in auto mode and what it said. (Step 6 is a Bash command; the hook is not registered for Bash, so it should be unaffected: we are checking that.)

3. 🖥️ **TERMINAL**: show the hook log, then paste the output to me.

```bash
cat ~/cgb-hook.log
```

4. Quit the Claude session, then clear the log:

🖥️ **TERMINAL**:

```bash
rm -f ~/cgb-hook.log
```

### T3b (no `onFailure`)

1. 🖥️ **TERMINAL**:

```bash
cd ~/cgb-cwd && claude --settings ~/cgb-T3b.json --permission-mode auto
```

2. 💬 **CLAUDE**: paste.

```text
Run each numbered step as its own separate call, using the literal paths exactly as written. Report the exact output or error of each. Do not retry or work around any failure.
1. Write tool: create /mnt/d/claude-guard-test-b/hook-crash/b.txt containing "hi"
2. Write tool: create /mnt/d/claude-guard-test-b/hook-deny/c.txt containing "hi"
```

Expected: step 1 shows whether a crashing hook without `onFailure` lets the write through (fail-open), step 2 should still be denied. Tell me both outcomes. Quit.

---

## T4: does `Bash(cmd *)` match the bare command?

`T4.json` has only deny rules: `Bash(uname *)`, `Bash(hostname *)`, `Bash(whoami)` (exact, no star) and `Bash(git stash list *)`. All the commands are harmless.

1. 🖥️ **TERMINAL**:

```bash
cd ~/cgb-gitrepo && claude --settings ~/cgb-T4.json --permission-mode default
```

2. 💬 **CLAUDE**: paste.

```text
Run each numbered step as its own separate Bash call, exactly as written. Report whether each ran or was blocked by a permission rule, and the exact message. Do not retry or work around any failure.
1. uname
2. uname -s
3. hostname
4. hostname -f
5. whoami
6. whoami --version
7. git stash list
8. git stash list --oneline
```

Approve any normal approval prompts (these are harmless) but tell me which commands prompted and which were **denied by a rule** (a denial is different from a prompt).

3. Quit the Claude session.

---

## Cleanup

Only after all tests are done:

🖥️ **TERMINAL**:

```bash
cd ~/Repositories/claude-guard
scripts/phase0/cleanup-b.sh
```

It removes exactly what `setup-b.sh` created.

---

## What to send me

Paste the Claude outputs and your notes on prompts per session (T1a, T1b, T2 auto, T2 acceptEdits, T3 plus the hook log, T3b, T4), in the same style as before, into `docs/phase0b_results.md` and tell me when it's there.
