#!/usr/bin/env bash
# SessionStart hook for sessions opened in this repo — locally and in Claude
# Code cloud sessions. Cloud sessions start from a fresh clone, where
# core.hooksPath is unset, so the repo's pre-commit (checks + version bump)
# would silently not run. Setting it here makes every commit go through them.
set -u
cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}" || exit 0
[ -d scripts/git-hooks ] || exit 0
if [ "$(git config --get core.hooksPath 2>/dev/null)" != "scripts/git-hooks" ]; then
  git config core.hooksPath scripts/git-hooks
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "session-start: jq is missing — the pre-commit version bump needs it; install jq before committing plugin changes."
fi
exit 0
