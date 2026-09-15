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
