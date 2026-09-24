# Token telemetry

Read-only reports over Claude Code transcripts (`~/.claude/projects/*/*.jsonl`), used to
measure where business-os spends tokens before and after changes (BOS-49).

```bash
python3 scripts/telemetry/token_telemetry.py > report.txt
python3 scripts/telemetry/token_telemetry_extra.py > extra.txt
```

- `TELEMETRY_DIRS` — comma-separated project dir names to scan (default: shahut-site,
  the digital-wellness venture and this repo).
- `TELEMETRY_EXCLUDE_SESSION` — the full id of a session still running: `token_telemetry.py` reports totals with and without it; `token_telemetry_extra.py` leaves it out.

Usage is deduplicated by `message.id` (one API response spans several transcript lines).
Weights are price ratios relative to plain input: cache read 0.1, 5-minute cache write
1.25, 1-hour cache write 2, output 5. Cowork transcripts live in the cloud and are not
covered. Transcripts older than 30 days are deleted by Claude Code, so the 2026-09-24
baseline is kept outside this repo (`יזמות-ארכיון/_מערכת/baselines.md` and
`~/.business-os/baselines/`).
