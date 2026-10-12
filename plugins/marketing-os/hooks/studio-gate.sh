#!/bin/sh
# marketing-os — PreToolUse gate for Meta Ads tools, campaign.json and the board server. See studio-gate.py.
#
# Fails closed for money: if python3 is missing or the check itself fails, every Meta Ads tool that is not a plain
# read is denied. Everything else goes through.

here=$(dirname "$0")
input=$(cat)

if command -v python3 >/dev/null 2>&1 && [ -r "$here/studio-gate.py" ]; then
  out=$(printf '%s' "$input" | python3 "$here/studio-gate.py") && { printf '%s' "$out"; exit 0; }
fi

if printf '%s' "$input" | grep -Eq '"tool_name"[[:space:]]*:[[:space:]]*"[^"]*__ads_' &&
   ! printf '%s' "$input" | grep -Eq '"tool_name"[[:space:]]*:[[:space:]]*"[^"]*__ads_(get_|insights_)'; then
  printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"marketing-os — בדיקת השומר לא רצה (אין python3), ולכן פעולות ב-Meta חסומות עד שהיא תרוץ."}}'
fi
exit 0
