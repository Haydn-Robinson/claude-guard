# claude-guard: notes for Claude

- This is a **general** tool. Keep examples, tests and docs generic: no references to any one user's folders or workflows.
- Before proposing an experiment, read `docs/verified-behaviour.md`. It records what is known about Claude Code's behaviour (each fact has an ID `VB-xx`, a status, evidence and a re-check runbook). Do not re-run an experiment for a VERIFIED fact unless behaviour is observed to differ or Claude Code has had a version change. After any new experiment, update that file (and the environment table).
- Cite fact IDs in comments where code depends on a behaviour. `tests/test_docs.py` fails if a cited ID does not exist or an entry is malformed.
- Never edit real Claude settings during development: tests use temp HOME and temp dirs. Commit only when asked; never push.
- Settings-mutation code is written test-first, with mutation checks.
- Run `uv run pytest`, `uv run ruff check .` and `uv run ruff format --check .` before reporting work as done.
