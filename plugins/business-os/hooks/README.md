# business-os hooks

## What `pre-tool.sh` does

It runs before every `Agent`, `Task`, `Bash` and `Read` call.

1. **Raises the subagent depth in Cowork, only with consent.**
   - **Why:** Cowork starts every session with `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=1`. At that depth, department heads cannot call their reviewers (brand, legal, security, code review), and they silently do the review work themselves.
   - **What it changes:** the hook writes `env.CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=3` into the sandbox's Claude settings file. It changes nothing else.
   - **Retry on first spawn:** the first spawn after the write is denied once, with a retry request. A spawned agent's tools are fixed at spawn time, so the retry is what picks up the new depth.
   - Verified live on 2026-09-15; see branch `experiment/cowork-probe`, `plugins/cowork-probe/RESULTS.md`.
2. **Keeps a delegation ledger** at `~/.business-os/ledger.md`.
   - Each row: who called whom, a policy note, and the working-folder name. Prompt text is never logged.
   - It never blocks anything.

## When it will not write

The hook writes nothing unless **all** of these hold:

- `consent.json` next to this folder contains `"raise_subagent_depth": true`.
- The hook's own environment shows depth `1`. That is Cowork's cap; a local Claude Code install defaults to 3.
- `HOME` and the config folder are not a real machine's home (`/home`, `/mnt`, `/Users`, `/c/...`), and neither is a symlink.

Without consent, the ledger marks each capped session with `anomaly: depth=1 and raise not done (not consented)`.

## Consent

Consent is a file in this plugin, not a per-session setting. The plugin is the only thing Cowork re-syncs into every fresh session. A file in `HOME`, or a plugin `userConfig` value, lives in `~/.claude/settings.json` and is wiped with each container.

- **To give consent:** the founder asks for `consent.json` to be committed. Its git history is the record of who approved it and when.
- **To revoke:** delete the file, or set the value to `false`, and commit.

## What `board-gate.sh` does

It runs before every Notion MCP tool call (any tool name starting with `mcp__` that contains `notion`). It keeps Claude inside the board contract in `skills/notion-board`.

- **Only the founder decides what happens to a task waiting on him.** A task at `ממתין לאישור` has exactly two exits, and both are his, in Notion's own UI where no hook runs: `מאושר` (approved) or `לסבב נוסף` (back for another round).
- **Only the founder writes `תגובת מייסד`.** That column holds what he said when he approved a task or sent it back, and a run treats it as binding. A run reads it and copies it into the page body under `## סבב <n>`; writing the column itself would let a round rewrite his words, so the hook blocks it.
- **No session changes the board's structure.** The structure lives in `skills/notion-board/registry.json` and its SKILL.md; ad-hoc boards are what the contract replaced.
- **Two tool families.** The Notion connector (`notion-update-page`, flat values) and the Notion REST API exposed by a local Notion MCP server (`API-patch-page`, `API-post-page`, nested values such as `{"status": {"name": "..."}}`). The local server must be registered under a name containing `notion`, or the `hooks.json` matcher does not see it.
- **It blocks** a call that would:
  - set a status-like property (name contains `סטטוס` or `status`) to a value containing `אושר`, `מאשר`, `approved` or `סבב נוסף`, or any other property to exactly one of the approved words, at any depth of the value. Values are compared letters-only: niqqud, emoji, punctuation, spaces and invisible characters are dropped first. `סבב נוסף` counts only inside a status, so a result line like `סבב 2: קוצר` stays writable;
  - set a property whose name contains `תגובת מייסד`, `הערת מייסד` or `founder note` — the founder's own reply on a task;
  - set a status-like property by option `id` only (the option could be `מאושר`);
  - create a database (`create-database`, `create-a-database`), change a database or data source's columns, options or trash state (`update-data-source`, `update-a-database`, `update-a-data-source`);
  - create a page without a `parent` (a loose page instead of a board row);
  - move, duplicate or trash pages (`move-pages`, `move-page`, `duplicate-page`, `in_trash`/`archived` true; a copy of an approved task is approved);
  - hand work to Notion AI (`spawn-session`, `send-message-to-session`), which could do any of the above for Claude.
- **Tests:** `hooks/tests/test_board_gate.py` — run with `python3`. Add a case for every new bypass.
- **It allows** everything else: reads, comments, creating rows under a parent, updating statuses other than the founder's two, and page content and result text that mention any of these words — including the `## סבב <n>` block in which a run records what the founder said.
- **Why a denylist, not an allowlist of statuses:** the hook sees every Notion database the founder uses. An allowlist would block status updates in all of them.
- **Fails closed:** if `python3` is missing or the check fails, a Notion call that mentions those words (approval words, `סבב נוסף`, `תגובת מייסד`), or is one of the structure tools, is blocked. In that path a run cannot record the founder's reply in the page body either — which is the intended trade: no `python3`, no board writes. A hook timeout still lets the call through (Claude Code behaviour), so the check stays local and fast.
- **Changing the structure** is done by the founder in Notion, or in a development session where this plugin is not loaded, followed by an update to `registry.json` and SKILL.md.
- **Known limits:**
  - A Notion token reachable from Bash (for example `curl api.notion.com`) is not covered — the hook only sees Notion tool calls. An unattended runner must keep its Notion credential out of the agent's shell, and "מאושר" is never the gate for anything that reaches customers: going live is the founder's click at the hosting provider.
  - The connector acts as the founder's own account, so Notion's "last edited by" cannot tell a Claude edit from the founder's. The hook is the control, not Notion's history.
