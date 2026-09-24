# business-os-plugin — working in this repo

- **CEO-PERSONA.md is the source.** The copies in `~/repos/CLAUDE.md` and `~/ventures/CLAUDE.md`
  are generated at session start by `plugins/business-os/hooks/sync-persona.py` — never edit them.
  Cowork gets the persona by a manual paste into project instructions; say so when the persona changes.
- **Gate rules are pinned.** `scripts/persona-anchors.txt` lists a phrase for every approval/legal
  gate rule. Checks use the anchors of HEAD and of `~/.business-os/trusted-anchors.txt` (written only by
  sync-persona after a version passes, so a pull or merge can't change it) as well as the new ones,
  so dropping a rule together with its anchor still fails. Removing a gate rule needs the founder's explicit OK in chat,
  recorded as a line in `~/.business-os/approved-anchor-removals.txt` — never add that line without it,
  and never delete or empty `trusted-anchors.txt` to get past a refusal. An approval is used up once synced.
- **Every commit runs `scripts/check.py`** (via `scripts/git-hooks/pre-commit`, which also bumps
  plugin versions). Cloud sessions get the hooks from `.claude/settings.json` → `scripts/session-start.sh`.
  A commit here that bumps a plugin version is also pushed (post-commit).
- **Tests:** `python3 scripts/tests/test_check.py`, `python3 plugins/business-os/hooks/tests/test_sync_persona.py`,
  `python3 plugins/business-os/hooks/tests/test_board_gate.py`, `python3 plugins/rnd-os/hooks/tests/test_security_gate.py`,
  `python3 scripts/tests/test_orient.py`, `test_board.py`, `test_conditions.py` (same folder).
- **No status files.** "Where are we" is computed at read time from the Notion board, the legal rulings
  and the project page. Don't add a file that copies state from another owner. `business-os:orient` does it:
  code (`orient.py`, `board.py`, `conditions.py`) computes the full report, and the model answers with a few
  lines of conclusions from it — Boaz's call (24.9), accepting a rare slip over a report he won't read. Facts
  the model got wrong in the tests (what blocks, what waits and why, dates) belong in the code, not in rules.
- **Context hygiene:** one topic per session. After a break of more than an hour in a long session,
  prefer a fresh session with a short handoff over resuming (the whole context is re-cached at 2×).
  Pass paths to subagents, not file contents.
- Token baseline and measurement scripts: `scripts/telemetry/` (BOS-49).
