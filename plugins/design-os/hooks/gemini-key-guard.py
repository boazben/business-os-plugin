#!/usr/bin/env python3
"""design-os Gemini key guard — PreToolUse hook on every tool.

The founder's Gemini API key lives in the environment or in
`.business-os/gemini-api-key` in a project folder, and only the generate-image
script reads it. This hook keeps it out of the conversation:

- a string shaped like a Google API key in the input of any tool except
  Write/Edit is blocked (Firebase and Maps browser keys have the same shape and
  belong in front-end code);
- Read, Write or Edit on the key file, and Write/Edit anywhere in .business-os
  (its .gitignore keeps the key out of git), are blocked;
- Grep inside .business-os, on the key file, or for the key prefix is blocked;
- shell commands — Bash in the session, and the Cowork device shell
  (`...device_bash`) on the founder's computer — that name the key file, read
  the variable's value, dump the environment, search for the key prefix, or
  read .business-os beyond ledger.md are blocked. Naming the variable
  (`gh secret set GEMINI_API_KEY`) is allowed.

Staging the key file into a Cowork cloud session with device_stage_files is
allowed: it copies the file without showing it, and once staged the rules above
cover it.

This is a guard against leaking the key by mistake, not a sandbox: a determined
command can still reach the file. Blocks by exiting 2 with the reason on stderr.
"""
import json
import os
import re
import sys

KEY_FILE = "gemini-api-key"
KEY_FILE_RE = re.compile(re.escape(KEY_FILE), re.I)
# Google API keys: classic "AIza" + 35 characters, or the newer "AQ." + ~50 characters.
KEY_VALUE_RE = re.compile(r"AIza[0-9A-Za-z_\-]{30,}|AQ\.[0-9A-Za-z_\-]{40,}")
KEY_PREFIXES = ("AIza", "AQ.")
# Reading the variable's value, as opposed to naming it.
KEY_VAR_READ_RE = re.compile(
    r"\$\{?!?GEMINI_API_KEY\b|\bprintenv\s+GEMINI_API_KEY\b|environ\b.{0,40}GEMINI_API_KEY"
    r"|getenv\W{0,5}GEMINI_API_KEY|process\.env\W{0,3}GEMINI_API_KEY"
)
# Dumping the whole environment: a bare env/printenv/set in command position, or printing environ.
ENV_DUMP_RE = re.compile(
    r"(^|[;&|(`]\s*|\bsudo\s+)(env|printenv|set|export\s+-p|declare\s+-\w*[px]\w*|typeset\s+-\w*[px]\w*"
    r"|compgen\s+-[ev])\s*($|[;&|)`>])"
    r"|/environ\b"
    r"|\bprint\w*\s*\(\s*(dict\s*\(\s*)?os\.environ\b"
    r"|\b(console\.\w+|JSON\.stringify)\s*\(\s*process\.env\s*\)"
)
STATE_TOKEN_RE = re.compile(r"\.business-os[^\s'\"|;&)<>]*")
STATE_DIR_TOKEN_RE = re.compile(r"\.business-os/?")
SAFE_STATE_FILE_RE = re.compile(r"\.business-os/ledger\.md")
DIR_READ_RE = re.compile(
    r"\b(grep|egrep|rg|ag|ack|find|tar|zip|7z|rsync|scp|cp|cat|bat|head|tail|less|more|awk|sed|xargs"
    r"|strings|od|xxd|hexdump|base64|python3?|node|perl|ruby|tree|diff)\b|\*"
)
FILE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")

KEY_MSG = ("חסום: הפעולה עלולה לחשוף את מפתח ה-API של Gemini (קובץ המפתח ב-.business-os, ערך המשתנה, או "
           "הדפסת משתני הסביבה). רק סקריפט התמונות משתמש במפתח; כדי לדעת אם יש מפתח — generate_image.py "
           "--status. מתיקיית .business-os קוראים רק את ledger.md, בשמו המלא (או בכלי Read).")
KEY_VALUE_MSG = ("חסום: הקלט מכיל מחרוזת שנראית כמו מפתח API של Google, והוא לא נשלח לפקודה, לסוכן או לשירות "
                 "חיצוני. מפתח דפדפן של Firebase או Maps, שנועד לקוד האתר — כותבים ישירות לקובץ עם Write. "
                 "אחרת, אם זה מפתח אמיתי — עצור, ודווח למייסד בראש הדיווח שצריך להחליף אותו ב-Google Cloud "
                 "(בלי לצטט אותו).")


def block(reason):
    print(reason, file=sys.stderr)
    sys.exit(2)


def reaches_key(command):
    """True for a shell command that could read or reveal the Gemini API key."""
    if (KEY_FILE_RE.search(command) or KEY_VAR_READ_RE.search(command) or any(k in command for k in KEY_PREFIXES)
            or ENV_DUMP_RE.search(command)):
        return True
    for token in STATE_TOKEN_RE.findall(command):
        if STATE_DIR_TOKEN_RE.fullmatch(token):
            # The folder itself: listing or creating it is fine, reading through it is not.
            if DIR_READ_RE.search(command):
                return True
        elif not SAFE_STATE_FILE_RE.fullmatch(token):
            return True
    return False


def is_key_file(path):
    return os.path.basename(str(path or "")).lower() == KEY_FILE


def main():
    try:
        event = json.load(sys.stdin)
    except (ValueError, OSError):
        sys.exit(0)
    if not isinstance(event, dict):
        sys.exit(0)

    tool = str(event.get("tool_name") or "")
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}

    if tool not in FILE_TOOLS and KEY_VALUE_RE.search(json.dumps(tool_input, ensure_ascii=False)):
        block(KEY_VALUE_MSG)

    if tool == "Bash" or tool.endswith("device_bash"):
        if reaches_key(str(tool_input.get("command") or "")):
            block(KEY_MSG)
    elif tool in FILE_TOOLS:
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if is_key_file(path) or "/.business-os/" in path.replace("\\", "/"):
            block(KEY_MSG)
    elif tool == "Read":
        if is_key_file(tool_input.get("file_path")):
            block(KEY_MSG)
    elif tool == "Grep":
        where = f"{tool_input.get('path') or ''} {tool_input.get('glob') or ''}"
        if ".business-os" in where or KEY_FILE_RE.search(where) or any(k in str(tool_input.get("pattern") or "") for k in KEY_PREFIXES):
            block(KEY_MSG)
    sys.exit(0)


if __name__ == "__main__":
    main()
