#!/usr/bin/env bash
# Opens an HTML file in the user's default browser, detecting the environment.
# Usage: ./open_in_browser.sh plan-preview.html
set -u

FILE="${1:-plan-preview.html}"

if [ ! -f "$FILE" ]; then
  echo "לא נמצא הקובץ: $FILE" >&2
  exit 1
fi

ABS="$(cd "$(dirname "$FILE")" && pwd)/$(basename "$FILE")"

open_it() {
  case "$(uname -s)" in
    Darwin)
      open "$ABS" ;;
    Linux)
      # WSL looks like Linux but has no display; it needs a Windows-side opener.
      if grep -qiE "(microsoft|wsl)" /proc/version 2>/dev/null; then
        if command -v wslview >/dev/null 2>&1; then
          wslview "$ABS"
        elif command -v explorer.exe >/dev/null 2>&1; then
          # explorer.exe wants a Windows path and exits non-zero even on success.
          explorer.exe "$(wslpath -w "$ABS")" || true
        else
          return 1
        fi
      elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$ABS" >/dev/null 2>&1
      else
        return 1
      fi ;;
    MINGW*|MSYS*|CYGWIN*)
      start "" "$ABS" ;;
    *)
      return 1 ;;
  esac
}

if open_it; then
  echo "נפתח בדפדפן: $ABS"
else
  # Headless / SSH / container: printing the path is more useful than failing.
  echo "לא ניתן לפתוח דפדפן אוטומטית. פתח ידנית:"
  echo "  file://$ABS"
fi
