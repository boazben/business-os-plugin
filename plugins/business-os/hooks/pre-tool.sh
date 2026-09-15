#!/bin/sh
# business-os — PreToolUse hook for Agent / Task / Bash / Read.
#
# 1. Raises the subagent spawn depth on the first such call of a Cowork
#    session (see raise-depth.sh). An Agent spawn is denied while the raise
#    has not reached this session yet — because this call just wrote it, or
#    because a parallel call did and the hook still sees depth=1 — since a
#    spawned agent's tool list is fixed at spawn time. At most MAX_DENIES
#    per session: if the reload never lands (a Cowork change), the call is
#    let through and the anomaly is logged, rather than breaking the session.
# 2. Appends every Agent/Task call to $HOME/.business-os/ledger.md (who called
#    whom, never the prompt text). The "who may call whom" column only records
#    deviations; it never blocks (founder decision, 2026-09-15).

here=$(dirname "$0")
. "$here/raise-depth.sh"

MAX_DENIES=2

[ -n "${HOME:-}" ] || exit 0

input=$(cat)

if printf '%s' "$input" | grep -Eq '"tool_name"[[:space:]]*:[[:space:]]*"(Agent|Task)"'; then
  is_agent=1
else
  is_agent=0
fi

raise_depth

[ "$is_agent" = 1 ] || exit 0

pending=0
case "$action" in
  merged:*|"wrote new:"*) pending=1 ;;
  "already set:"*) [ "${CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH:-}" = 1 ] && pending=1 ;;
esac

note=""
if [ "$pending" = 1 ]; then
  session=$(printf '%s' "$input" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([A-Za-z0-9_-]*\)".*/\1/p')
  marker="$HOME/.business-os/denies-${session:-unknown}"
  mkdir -p "$HOME/.business-os" 2>/dev/null
  denies=$(wc -l < "$marker" 2>/dev/null || echo 0)
  if [ "$denies" -lt "$MAX_DENIES" ]; then
    echo deny >> "$marker" 2>/dev/null
    echo "business-os: subagent spawn depth is being raised to $TARGET_DEPTH so department heads can call their reviewers. Make the exact same Agent call again, unchanged." >&2
    exit 2
  fi
  note="anomaly: depth raise did not take effect after $MAX_DENIES retries"
fi

if command -v python3 >/dev/null 2>&1; then
  printf '%s' "$input" | BUSINESS_OS_NOTE="$note" python3 "$here/ledger.py" 2>/dev/null
fi

exit 0
