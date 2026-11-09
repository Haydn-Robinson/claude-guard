# Phase 0 sandbox tests: step-by-step runbook

## Legend (read this first)

Every step starts with one of two labels:

- 🖥️ **TERMINAL**: a command **you type yourself** in an ordinary terminal (a normal WSL shell, not inside Claude). The code block is `bash`. Press Enter.
- 💬 **CLAUDE**: text **you paste into the test Claude session** as a single message. The code block is `text`. Do **not** type these lines in a terminal and do **not** use the `!` prefix. Claude runs the commands inside them.

Other rules:

- Never use `!` anywhere in this runbook. `!` commands run unsandboxed and would defeat the test.
- "Terminal 1" is where you launch and use the test Claude session. "Terminal 2" is only needed in Session C.
- Quit each test session (`/exit` or Ctrl+C twice) before starting the next.
- For every 💬 step, **write down which Bash calls showed an approval prompt** (Claude can't see prompts) and paste the results back to me.

Replace `WINUSER` with your Windows username in every place it appears (the 🖥️ commands and the 💬 prompts).

---

## Step 0: setup (once)

🖥️ **TERMINAL** (any normal terminal), from the repo:

```bash
cd ~/Repositories/claude-guard
scripts/phase0/setup.sh WINUSER
```

This creates throwaway folders under `/mnt/d/claude-guard-test`, `/mnt/c/Users/WINUSER/claude-guard-test`, `~/cg-test-home` and `~/cg-test-cwd`, plus three settings files `~/cg-test-A.json`, `~/cg-test-B.json`, `~/cg-test-C.json`. Optional: `cat ~/cg-test-A.json` to check the paths look right.

---

## Session A: path forms and /mnt/c

What it tests: which `allowWrite` path spellings work (plain, spaces, `//`, `~`, `/mnt/c`), that siblings stay protected, and whether the Write tool (not sandboxed) prompts.

1. 🖥️ **TERMINAL 1**: launch the test session.

```bash
cd ~/cg-test-cwd && claude --settings ~/cg-test-A.json --permission-mode auto
```

2. 💬 **CLAUDE** (in that session): paste this whole block as one message. Edit `WINUSER` first.

```text
Run each numbered step as its own separate Bash call, using plain commands. Report the exact output or error of each step. Do not retry or work around any failure. Let D=/mnt/d/claude-guard-test and C=/mnt/c/Users/WINUSER/claude-guard-test.
1. echo hi > $D/allowed/a.txt ; rm $D/allowed/a.txt
2. echo hi > "$D/with space/allowed/a.txt" ; rm "$D/with space/allowed/a.txt"
3. echo hi > "$D/with space/sibling/new.txt" ; rm "$D/with space/sibling/victim.txt"
4. echo hi > $D/dslash/a.txt ; rm $D/dslash/a.txt
5. echo hi > $C/allowed/a.txt ; rm $C/allowed/a.txt
6. echo hi > $C/sibling/new.txt ; rm $C/sibling/victim.txt
7. echo hi > ~/cg-test-home/allowed/a.txt ; rm ~/cg-test-home/allowed/a.txt
8. echo hi > ~/cg-test-home/sibling/new.txt ; rm ~/cg-test-home/sibling/victim.txt
9. bash -c "rm -f $C/sibling/victim.txt"
10. Use the Write tool (not Bash) to create /mnt/d/claude-guard-test/sibling/write-tool.txt containing "hi".
```

Expected, so you can sanity-check:

| Step | Expected |
|---|---|
| 1, 2 | succeed |
| 3 | fail (`Read-only file system`) |
| 4 | succeed? (tests the `//` path form) |
| 5 | succeed? (tests `/mnt/c`) |
| 6 | fail |
| 7 | succeed? (tests `~`) |
| 8, 9 | fail |
| 10 | unknown: tell me whether it prompted or just succeeded |

3. 🖥️ **TERMINAL 2** (or after quitting): confirm nothing outside was damaged. Each command should print `orig`.

```bash
cat "/mnt/d/claude-guard-test/with space/sibling/victim.txt" /mnt/c/Users/WINUSER/claude-guard-test/sibling/victim.txt ~/cg-test-home/sibling/victim.txt
```

4. Quit the Claude session.

---

## Session B: do Edit rules reach the sandbox?

What it tests: with **no** `allowWrite`, only `Edit` permission rules, can Bash write to an `Edit`-allowed folder, and does an `Edit` deny block Bash writes to a protected sub-folder?

1. 🖥️ **TERMINAL 1**:

```bash
cd ~/cg-test-cwd && claude --settings ~/cg-test-B.json --permission-mode auto
```

2. 💬 **CLAUDE**: paste as one message.

```text
Run each numbered step as its own separate Bash call unless it says to use a tool. Report the exact output or error of each. Do not retry or work around any failure. Let D=/mnt/d/claude-guard-test.
1. echo hi > $D/edit-only/a.txt
2. echo hi > $D/prot/allowed/ok/a.txt
3. echo hi > $D/prot/allowed/.protected/new.txt
4. rm $D/prot/allowed/.protected/secret.txt
5. bash -c "echo x >> $D/prot/allowed/.protected/secret.txt"
6. Use the Write tool (not Bash) to create $D/prot/allowed/.protected/tool.txt containing "hi".
7. Use the Write tool (not Bash) to create $D/prot/allowed/ok/tool.txt containing "hi".
```

Expected:

| Step | Expected | Meaning |
|---|---|---|
| 1, 2 | succeed? | an `Edit` allow also gives Bash write access |
| 3, 4, 5 | fail? | an `Edit` deny becomes a sandbox write-deny |
| 6 | denied | the Edit deny blocks the tool |
| 7 | succeed | the Edit allow covers the tool |

3. Quit the Claude session.

---

## Session C: do changes apply mid-session?

What it tests: whether granting and revoking `allowWrite` take effect without restarting Claude. This one uses two terminals.

1. 🖥️ **TERMINAL 1**: launch (the file starts with an empty `allowWrite`).

```bash
cd ~/cg-test-cwd && claude --settings ~/cg-test-C.json --permission-mode auto
```

2. 💬 **CLAUDE** (Terminal 1): paste.

```text
Run this as a single Bash call and report the exact result. Do not retry. Command: echo hi > /mnt/d/claude-guard-test/allowed/m.txt
```

Expected: fails.

3. 🖥️ **TERMINAL 2** (a second, separate terminal; leave Claude running in Terminal 1): grant the folder.

```bash
cat > ~/cg-test-C.json <<'JSON'
{"sandbox":{"enabled":true,"allowUnsandboxedCommands":false,"filesystem":{"allowWrite":["/mnt/d/claude-guard-test/allowed"]}}}
JSON
```

4. 💬 **CLAUDE** (Terminal 1, same session, no restart): paste the same prompt again.

```text
Run this as a single Bash call and report the exact result. Do not retry. Command: echo hi > /mnt/d/claude-guard-test/allowed/m.txt
```

Expected: succeeds if live reload works. If it still fails, wait about 10 seconds, paste once more, and tell me.

5. 🖥️ **TERMINAL 2**: revoke it again.

```bash
cat > ~/cg-test-C.json <<'JSON'
{"sandbox":{"enabled":true,"allowUnsandboxedCommands":false,"filesystem":{"allowWrite":[]}}}
JSON
```

6. 💬 **CLAUDE** (Terminal 1): paste this one (different file name, so it is a fresh write).

```text
Run this as a single Bash call and report the exact result. Do not retry. Command: echo hi > /mnt/d/claude-guard-test/allowed/m2.txt
```

Expected: fails if revoking takes effect live.

7. Quit the Claude session.

---

## Session D: other permission modes

What it tests: which steps prompt when not in auto mode (this checks `autoAllowBashIfSandboxed`, which the docs say defaults to true). Do this twice, once per mode.

1. 🖥️ **TERMINAL 1**: first run, `default` mode.

```bash
cd ~/cg-test-cwd && claude --settings ~/cg-test-A.json --permission-mode default
```

2. 💬 **CLAUDE**: paste (edit `WINUSER`).

```text
Run each numbered step as its own separate Bash call. Report the exact output or error of each. Do not retry or work around any failure. Let D=/mnt/d/claude-guard-test and C=/mnt/c/Users/WINUSER/claude-guard-test.
1. echo hi > $D/allowed/a.txt ; rm $D/allowed/a.txt
2. echo hi > "$D/with space/sibling/new.txt" ; rm "$D/with space/sibling/victim.txt"
3. echo hi > $C/allowed/a.txt ; rm $C/allowed/a.txt
4. echo hi > $C/sibling/new.txt ; rm $C/sibling/victim.txt
5. bash -c "rm -f $C/sibling/victim.txt"
```

3. Note which steps prompted. Quit the session.

4. 🖥️ **TERMINAL 1**: second run, `acceptEdits` mode.

```bash
cd ~/cg-test-cwd && claude --settings ~/cg-test-A.json --permission-mode acceptEdits
```

5. 💬 **CLAUDE**: paste the same prompt as in step 2. Note which steps prompted. Quit.

---

## Cleanup

🖥️ **TERMINAL** (normal shell):

```bash
cd ~/Repositories/claude-guard
scripts/phase0/cleanup.sh WINUSER
```

It removes only the paths `setup.sh` created.

---

## What to send me

For each session, which steps succeeded, which failed (with the error text) and which prompted. A rough table is fine:

| Session | Step | Prompted? | Result |
|---|---|---|---|
