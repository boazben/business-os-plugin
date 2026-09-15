#!/bin/sh
# business-os — PreToolUse hook for Notion writes (create/update page, update
# data source). Only the founder approves board tasks, in Notion's own UI,
# where no hook runs. See board-gate.py for what counts as approving.
#
# Fails closed: if python3 is missing or the check crashes, any mention of the
# approved status in the call blocks it.

here=$(dirname "$0")
input=$(cat)

if command -v python3 >/dev/null 2>&1 && [ -r "$here/board-gate.py" ]; then
  printf '%s' "$input" | python3 "$here/board-gate.py"
  rc=$?
  [ "$rc" -eq 0 ] && exit 0
  [ "$rc" -eq 2 ] && exit 2
fi

# "מאושר" or "מאשר", raw or JSON-escaped (\u05de\u05d0...).
if printf '%s' "$input" | grep -Eiq 'מאושר|מאשר|\\u05de\\u05d0(\\u05d5)?\\u05e9\\u05e8|"in_trash"[[:space:]]*:[[:space:]]*true'; then
  echo "business-os: only the founder approves board tasks (status 'מאושר'), in Notion itself. The approval check could not run, so this call is blocked. Leave the task at 'ממתין לאישור'." >&2
  exit 2
fi
exit 0
