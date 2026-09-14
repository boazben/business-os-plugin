#!/usr/bin/env bash
# Vendored Mermaid bundle (~5.3MB) is not committed to this repo — a repo-wide
# commit guard caps blob size at 5MB. This script places a local copy at
# assets/mermaid.min.js so present-plan can render fully offline (no CDN).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HERE/../assets/mermaid.min.js"
PERSONAL_COPY="$HOME/.claude/skills/visualize-plan/assets/mermaid.min.js"

if [ -f "$DEST" ]; then
  echo "already present: $DEST"
  exit 0
fi

if [ -f "$PERSONAL_COPY" ]; then
  cp "$PERSONAL_COPY" "$DEST"
  echo "copied from $PERSONAL_COPY -> $DEST"
  exit 0
fi

echo "No local copy found at $PERSONAL_COPY." >&2
echo "Place a mermaid.min.js (UMD/browser build) at $DEST manually," >&2
echo "e.g. from https://www.jsdelivr.com/package/npm/mermaid (pin a version)." >&2
exit 1
