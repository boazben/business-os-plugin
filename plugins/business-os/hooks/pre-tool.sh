#!/bin/sh
# business-os — PreToolUse hook for Agent / Task / Bash / Read.
#
# 1. Raises the subagent spawn depth on the first such call of a Cowork
#    session (see raise-depth.sh). If that first call is itself an Agent
#    spawn, it is denied once with a retry request: a spawned agent's tool
#    list is fixed at spawn time, so it must start after the settings reload.
# 2. Appends every Agent/Task call to a ledger ($HOME/.business-os/ledger.md):
#    who called whom, never the prompt text. The CEO reads it to warn the
#    founder when a department claims a review that never ran. The
#    "who may call whom" column only records deviations; it never blocks —
#    agents keep their own judgement (founder decision, 2026-09-15).

here=$(dirname "$0")
. "$here/raise-depth.sh"

input=$(cat)

case "$input" in
  *'"tool_name":"Agent"'*|*'"tool_name": "Agent"'*|*'"tool_name":"Task"'*|*'"tool_name": "Task"'*) is_agent=1 ;;
  *) is_agent=0 ;;
esac

raise_depth

if [ "$is_agent" = 1 ]; then
  case "$action" in
    merged:*|"wrote new:"*)
      echo "business-os: subagent spawn depth was just raised to $TARGET_DEPTH so department heads can call their reviewers. Make the exact same Agent call again, unchanged." >&2
      exit 2
      ;;
  esac

  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$input" | python3 "$here/ledger.py" 2>/dev/null
  fi
fi

exit 0
