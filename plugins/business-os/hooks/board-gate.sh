#!/bin/sh
# business-os — PreToolUse hook for Notion tools. It does not block board work:
# it lets the call through, warns the founder and keeps a record, so Claude can
# do more of the board and he does less of it by hand. See board-gate.py for
# the exact verdicts (ok / warn / ask).
#
# Fails open, loudly: if python3 is missing or the check itself fails, a Notion
# write still goes through with a warning that it was not checked — except
# trashing a page or handing work to Notion AI, which ask for one click first.

here=$(dirname "$0")
input=$(cat)

if command -v python3 >/dev/null 2>&1 && [ -r "$here/board-gate.py" ]; then
  printf '%s' "$input" | python3 "$here/board-gate.py"
  rc=$?
  [ "$rc" -eq 0 ] && exit 0
  [ "$rc" -eq 2 ] && exit 2
fi

# The check could not run. Confirm only what does not come back on its own.
if printf '%s' "$input" | grep -Eq '"(in_trash|archived)"[[:space:]]*:[[:space:]]*true|notion[-_](spawn[-_]session|send[-_]message[-_]to[-_]session)"'; then
  printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"business-os — בדיקת הלוח לא רצה, והקריאה הזו מוחקת דף או מעבירה עבודה ל-Notion AI. זו פעולה שלא חוזרת לבד. לאשר?"},"systemMessage":"⚠️ business-os — בדיקת הלוח לא רצה (אין python3)."}'
  exit 0
fi

# Any other Notion write: through, with a warning that nothing checked it.
if printf '%s' "$input" | grep -Eq 'notion[-_]?(update|create|patch|post|move|duplicate|delete)|API-(patch|post|create|delete)'; then
  printf '%s' '{"systemMessage":"⚠️ business-os — בדיקת הלוח לא רצה (אין python3). הכתיבה ל-Notion עברה בלי בדיקה ובלי רישום ב-board-log.md."}'
fi
exit 0
