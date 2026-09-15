#!/bin/sh
# business-os — PreToolUse hook for Notion tools. Only the founder approves
# board tasks, in Notion's own UI, where no hook runs. See board-gate.py for
# what counts as approving.
#
# Fails closed: if python3 is missing or the check fails, a Notion call that
# mentions an approved value, trashes something, duplicates a page or hands
# work to Notion AI is blocked.

here=$(dirname "$0")
input=$(cat)

if command -v python3 >/dev/null 2>&1 && [ -r "$here/board-gate.py" ]; then
  printf '%s' "$input" | python3 "$here/board-gate.py"
  rc=$?
  [ "$rc" -eq 0 ] && exit 0
  [ "$rc" -eq 2 ] && exit 2
fi

# Approved words raw or JSON-escaped (אושר is אושר).
if printf '%s' "$input" | grep -Eiq 'אושר|מאשר|approved|\\u05d0\\u05d5\\u05e9\\u05e8|\\u05de\\u05d0\\u05e9\\u05e8|"in_trash"[[:space:]]*:[[:space:]]*true|notion[-_](duplicate[-_]page|spawn[-_]session|send[-_]message[-_]to[-_]session)"'; then
  echo "business-os: only the founder approves board tasks, in Notion itself. The approval check could not run, so this Notion call is blocked. Leave the task at 'ממתין לאישור'." >&2
  exit 2
fi
exit 0
