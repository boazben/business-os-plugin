#!/bin/sh
# business-os — PreToolUse hook for Agent / Task / Bash / Read.
#
# 1. Raises the subagent spawn depth on the first such call of a Cowork
#    session (see raise-depth.sh). A spawned agent's tool list is fixed at
#    spawn time, so an Agent spawn is held (denied with a retry request) while
#    the hook's env still shows depth=1, i.e. the raise has not reached this
#    session yet. Every parallel spawn is held the same way. The hold is
#    bounded by time from the session's first hold (an atomic mkdir marker),
#    not from the settings file, so something that keeps rewriting that file
#    cannot extend it: after RELOAD_WINDOW seconds the call is let through and
#    an anomaly logged rather than breaking the session. The probe saw the
#    reload land in 1-3 s.
# 2. Appends every Agent/Task call to $HOME/.business-os/ledger.md (who called
#    whom, never the prompt text). The "who may call whom" column only records
#    deviations; it never blocks (founder decision, 2026-09-15).

here=$(dirname "$0")
RELOAD_WINDOW=20

input=$(cat)
[ -n "${HOME:-}" ] || exit 0
# A partial plugin update must not turn into "deny everything".
[ -r "$here/raise-depth.sh" ] || exit 0
. "$here/raise-depth.sh"

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
      session=$(printf '%s' "$input" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([A-Za-z0-9_-]*\)".*/\1/p')
      held_since=0
      if [ -n "$session" ]; then
        mark="$HOME/.business-os/hold-$session"
        mkdir -p "$HOME/.business-os" 2>/dev/null
        mkdir "$mark" 2>/dev/null
        held_since=$(stat -c %Y "$mark" 2>/dev/null || echo 0)
      fi
      age=$(( $(date +%s) - held_since ))
      if [ "$held_since" -gt 0 ] && [ "$age" -ge 0 ] && [ "$age" -le "$RELOAD_WINDOW" ]; then
        echo "business-os: subagent spawn depth is being raised to $TARGET_DEPTH so department heads can call their reviewers. Make the exact same Agent call again, unchanged." >&2
        exit 2
      fi
      note="anomaly: depth raise not applied (held ${RELOAD_WINDOW}s or could not hold)"
      ;;
    *)
      note="anomaly: depth=1 and raise not done (${action%%:*})"
      ;;
  esac
fi

if command -v python3 >/dev/null 2>&1; then
  printf '%s' "$input" | BUSINESS_OS_NOTE="$note" python3 "$here/ledger.py" 2>/dev/null
fi

exit 0
