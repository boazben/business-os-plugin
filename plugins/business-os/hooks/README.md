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

It runs before every Notion MCP tool call (any tool name starting with `mcp__` that contains `notion`). **It does not block.** The founder asked for a gate that warns and keeps a record rather than standing in the way, so that Claude does more of the board work and he does less of it by hand. Three verdicts, all letting the call proceed:

| verdict | what happens | which calls |
|---|---|---|
| `ok` | nothing, silently | ordinary board work: statuses other than the founder's, results, page content, reads, comments, rows created under a parent |
| `warn` | a `systemMessage` to the founder and a row in `~/.business-os/board-log.md` | the two exits from `ממתין לאישור` (`מאושר`, `לסבב נוסף`); his reply column `תגובת מייסד`; creating a database or changing a data source's columns and options; creating a page without a `parent`; moving or duplicating a page |
| `ask` | a permission prompt — one click — plus the same row | trashing or archiving a page (`in_trash`/`archived` true), and handing work to Notion AI (`spawn-session`, `send-message-to-session`), which could do anything on the board on Claude's behalf |

- **What it detects.** A status-like property (name contains `סטטוס` or `status`) set to a value containing `אושר`, `מאשר`, `approved` or `סבב נוסף`; any other property set to exactly one of the approved words; a status set by option `id` only (the option could be `מאושר`); a property whose name contains `תגובת מייסד`, `הערת מייסד` or `founder note`. Values and names are compared letters-only: niqqud, emoji, punctuation, spaces and invisible characters are dropped first. `סבב נוסף` counts only inside a status, so a result line like `סבב 2: קוצר` stays silent, and so does the `## סבב <n>` block in which a run records what the founder said.
- **Two tool families.** The Notion connector (`notion-update-page`, flat values) and the Notion REST API exposed by a local Notion MCP server (`API-patch-page`, `API-post-page`, nested values such as `{"status": {"name": "..."}}`). The local server must be registered under a name containing `notion`, or the `hooks.json` matcher does not see it.
- **The record.** `~/.business-os/board-log.md`, one row per `warn` and `ask`: time, session id, verdict, tool, and what it was. Never the content of the call. It is created on first use, mode `0600`, and trimmed to its newer half past 200 KB. A failure to write it costs a row, never the call. It is a separate file from the delegation ledger (`ledger.md`) so the CEO's `tail` of that file stays readable.
- **Why this is an acceptable trade.** `מאושר` on the board is not the gate for anything that reaches customers: going live is the founder's click at the hosting provider, and that is untouched. A wrong approval is visible in `board-log.md` rather than silent. What the hook no longer enforces is discipline in `skills/notion-board` §7 and `skills/run-board` — including the rule that content (a task body, a file, a site, code) is a request and never an approval, which now carries more weight, not less.
- **Fails open, loudly:** if `python3` is missing or the check itself fails, a Notion write still goes through, with a `systemMessage` saying it was not checked and not recorded — except a call that trashes a page or reaches Notion AI, which asks for one click first. A hook timeout lets the call through with nothing at all (Claude Code behaviour), so the check stays local and fast.
- **Tests:** `hooks/tests/test_board_gate.py` — run with `python3`. It asserts the verdict, not just the exit code, in a throwaway `HOME`, and fails if the gate ever blocks. Add a case whenever the gate misreads a call.
- **Known limits:**
  - A Notion token reachable from Bash (for example `curl api.notion.com`) is not covered — the hook only sees Notion tool calls, so such a write is neither warned about nor recorded. An unattended runner must keep its Notion credential out of the agent's shell.
  - The connector acts as the founder's own account, so Notion's "last edited by" cannot tell a Claude edit from his. `board-log.md` is the provenance record, not Notion's history — which is why the hook writes a row before the call rather than after it.
  - A scheduled run has nobody watching the `systemMessage` as it appears. There, the rules in `skills/run-board` are the whole control, and the log is what the founder reads afterwards.
