#!/bin/sh
# business-os — PreToolUse hook for Agent / Task / Bash / Read.
#
# 1. Raises the subagent spawn depth on the first such call of a Cowork
#    session (see raise-depth.sh). A spawned agent's tool list is fixed at
#    spawn time, so an Agent spawn is denied while the raise sits in the
#    settings file but has not reached this session yet (the hook's env still
#    shows depth=1). Every parallel spawn is held the same way. The hold is
#    bounded by time, not by a counter: once the settings file is older than
#    RELOAD_WINDOW seconds and the env still shows 1, the reload is not coming
#    (e.g. a Cowork change), so the call is let through and an anomaly logged
#    rather than breaking the session. The probe saw the reload land in 1-3 s.
# 2. Appends every Agent/Task call to $HOME/.business-os/ledger.md (who called
#    whom, never the prompt text). The "who may call whom" column only records
#    deviations; it never blocks (founder decision, 2026-09-15).

here=$(dirname "$0")
. "$here/raise-depth.sh"

RELOAD_WINDOW=20

input=$(cat)
[ -n "${HOME:-}" ] || exit 0

if printf '%s' "$input" | grep -Eq '"tool_name"[[:space:]]*:[[:space:]]*"(Agent|Task)"'; then
  is_agent=1
else
  is_agent=0
fi

raise_depth

[ "$is_agent" = 1 ] || exit 0

note=""
if [ "${CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH:-}" = 1 ]; then
  case "$action" in
    merged:*|"wrote new:"*|"already set:"*)
      now=$(date +%s)
      written=$(stat -c %Y "$settings" 2>/dev/null || echo 0)
      if [ $((now - written)) -le "$RELOAD_WINDOW" ]; then
        echo "business-os: subagent spawn depth is being raised to $TARGET_DEPTH so department heads can call their reviewers. Make the exact same Agent call again, unchanged." >&2
        exit 2
      fi
      note="anomaly: depth is set in settings but not applied after ${RELOAD_WINDOW}s"
      ;;
  esac
fi

if command -v python3 >/dev/null 2>&1; then
  printf '%s' "$input" | BUSINESS_OS_NOTE="$note" python3 "$here/ledger.py" 2>/dev/null
fi

exit 0
