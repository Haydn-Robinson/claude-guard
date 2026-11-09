# Phase 0 follow-up tests (E and F)

Same legend as `phase0-sandbox-tests.md`:

- 🖥️ **TERMINAL**: you type it in an ordinary terminal (`bash` block). Never use `!`.
- 💬 **CLAUDE**: you paste it as ONE message into the test Claude session (`text` block).

Quit each Claude session before starting the next. Your Windows user is `haydn`, so no substitution is needed here. Do not run cleanup until both tests are done.

For every 💬 step, note which calls showed an approval prompt.

---

## Step 0: setup

🖥️ **TERMINAL**:

```bash
cd ~/Repositories/claude-guard
scripts/phase0/setup.sh haydn
scripts/phase0/setup-followup.sh
```

The first command recreates the folders and settings files from the first runbook (safe to run again; it is needed if you already ran cleanup). The second creates `~/cg-test-proj/.claude/settings.local.json` with the sandbox on and an empty `allowWrite`.

---

## Test E: does a real settings file reload mid-session?

Question: Session C failed using `--settings <file>`. Does the same change work in a real project settings file (`.claude/settings.local.json`), which is what `guard` will edit? This uses two terminals.

1. 🖥️ **TERMINAL 1**: launch. There is no `--settings` flag this time. If Claude asks whether you trust the folder, accept it.

```bash
cd ~/cg-test-proj && claude --permission-mode auto
```

2. 💬 **CLAUDE** (Terminal 1): paste.

```text
Run this as a single Bash call and report the exact result. Do not retry. Command: echo hi > /mnt/d/claude-guard-test/allowed/e1.txt
```

Expected: fails with `Read-only file system`. If it succeeds, tell me, because the sandbox is not applied from this file.

3. 🖥️ **TERMINAL 2** (a second terminal; leave Claude running in Terminal 1): grant the folder.

```bash
cat > ~/cg-test-proj/.claude/settings.local.json <<'JSON'
{"sandbox":{"enabled":true,"allowUnsandboxedCommands":false,"filesystem":{"allowWrite":["/mnt/d/claude-guard-test/allowed"]}}}
JSON
```

4. 💬 **CLAUDE** (Terminal 1, same session, no restart): paste.

```text
Run this as a single Bash call and report the exact result. Do not retry. Command: echo hi > /mnt/d/claude-guard-test/allowed/e2.txt
```

Result A: succeeds, so live grants work. Result B: fails. Wait about 10 seconds and paste it once more (e3.txt). If it still fails, go to step 5.

5. Only if step 4 failed: in Terminal 1 quit Claude, then resume in the same folder.

🖥️ **TERMINAL 1**:

```bash
cd ~/cg-test-proj && claude --permission-mode auto --continue
```

💬 **CLAUDE**: paste.

```text
Run this as a single Bash call and report the exact result. Do not retry. Command: echo hi > /mnt/d/claude-guard-test/allowed/e4.txt
```

Expected: succeeds, which would confirm that a restart applies it.

6. 🖥️ **TERMINAL 2**: now test revoking. First put the grant back if you restarted, then remove it.

```bash
cat > ~/cg-test-proj/.claude/settings.local.json <<'JSON'
{"sandbox":{"enabled":true,"allowUnsandboxedCommands":false,"filesystem":{"allowWrite":[]}}}
JSON
```

7. 💬 **CLAUDE** (Terminal 1, same session): paste.

```text
Run this as a single Bash call and report the exact result. Do not retry. Command: echo hi > /mnt/d/claude-guard-test/allowed/e5.txt
```

Expected: fails if revoking applies live. If it succeeds, wait 10 seconds and paste once more (e6.txt).

8. Quit the Claude session.

---

## Test F: default mode with literal paths

Question: the earlier prompts in default and acceptEdits came from `$VAR` in redirect targets ("Redirect target concatenation contains `$`"). With literal paths, do sandboxed Bash commands run with no prompts? Also: what does the Write tool do outside the grants in default mode? Run F once in `default` mode, then once in `acceptEdits`.

1. 🖥️ **TERMINAL 1**: launch in default mode.

```bash
cd ~/cg-test-cwd && claude --settings ~/cg-test-A.json --permission-mode default
```

2. 💬 **CLAUDE**: paste.

```text
Run each numbered step as its own separate call. Use literal paths exactly as written, no shell variables. Report the exact output or error of each step. Do not retry or work around any failure.
1. Bash: echo hi > /mnt/d/claude-guard-test/allowed/f1.txt
2. Bash: rm /mnt/d/claude-guard-test/allowed/f1.txt
3. Bash: echo hi > "/mnt/d/claude-guard-test/with space/allowed/f2.txt" && rm "/mnt/d/claude-guard-test/with space/allowed/f2.txt"
4. Bash: bash -c "echo hi > /mnt/c/Users/haydn/claude-guard-test/allowed/f3.txt; rm /mnt/c/Users/haydn/claude-guard-test/allowed/f3.txt"
5. Bash: rm /mnt/c/Users/haydn/claude-guard-test/sibling/victim.txt
6. Bash: bash -c "rm -f /mnt/d/claude-guard-test/sibling/victim.txt"
7. Write tool: create /mnt/d/claude-guard-test/allowed/f4.txt containing "hi"
8. Write tool: create /mnt/d/claude-guard-test/sibling/f5.txt containing "hi"
```

Expected sandbox results: 1 to 4 succeed, 5 and 6 fail with `Read-only file system`. Steps 7 and 8 are the interesting ones: tell me whether each prompted, succeeded or was denied. Also note which of steps 1 to 6 prompted (hoped: none).

3. Quit the session.

4. 🖥️ **TERMINAL 1**: repeat in acceptEdits mode.

```bash
cd ~/cg-test-cwd && claude --settings ~/cg-test-A.json --permission-mode acceptEdits
```

5. 💬 **CLAUDE**: paste the same prompt as in step 2, but change the file names to `g1.txt`, `g2.txt`, `g3.txt`, `g4.txt` and `g5.txt` so they are fresh. Note which steps prompted. Quit.

---

## Cleanup

Only after both tests are done:

🖥️ **TERMINAL**:

```bash
cd ~/Repositories/claude-guard
scripts/phase0/cleanup.sh haydn
```

This now also removes `~/cg-test-proj`.

---

## What to send me

For E: which of steps 2, 4, 5 (restart), 7 succeeded or failed. For F: per step, success/fail/prompt, for both modes.
