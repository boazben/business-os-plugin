#!/bin/sh
# business-os — PreToolUse hook for Notion tools. Keeps writes inside the board
# contract (skills/notion-board): only the founder approves, and no session
# changes the board's structure. See board-gate.py for the exact rules.
#
# Fails closed: if python3 is missing or the check fails, a Notion call that
# mentions an approved value, or is a structure tool (create database, update
# data source, move, duplicate, Notion AI), is blocked.

here=$(dirname "$0")
input=$(cat)

if command -v python3 >/dev/null 2>&1 && [ -r "$here/board-gate.py" ]; then
  printf '%s' "$input" | python3 "$here/board-gate.py"
  rc=$?
  [ "$rc" -eq 0 ] && exit 0
  [ "$rc" -eq 2 ] && exit 2
fi

# Approved words raw or JSON-escaped (אושר is אושר).
if printf '%s' "$input" | grep -Eiq 'אושר|מאשר|approved|\\u05d0\\u05d5\\u05e9\\u05e8|\\u05de\\u05d0\\u05e9\\u05e8|"in_trash"[[:space:]]*:[[:space:]]*true|notion[-_](create[-_]database|update[-_]data[-_]source|move[-_]pages|duplicate[-_]page|spawn[-_]session|send[-_]message[-_]to[-_]session)"'; then
  echo "business-os: only the founder approves board tasks, in Notion itself. The board check could not run, so this Notion call is blocked. Leave the task at 'ממתין לאישור'." >&2
  exit 2
fi
exit 0
