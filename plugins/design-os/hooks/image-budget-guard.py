#!/usr/bin/env python3
"""design-os image budget guard — PreToolUse hook.

The generate-image skill counts every paid Nano Banana image against a monthly
budget and refuses to run past 130% of it. This hook keeps agents from routing
around that meter in any folder where design-os is enabled (rnd-os and
marketing-os depend on it, so that is most ventures):

1. The script runs only as one plain command: `python3 <plugin copy of the
   script> <flags>` — no chaining, redirects, substitutions or leading
   VAR=value. Any other command that names the script is blocked.
2. Image-generation model IDs (Nano Banana, Imagen) may not appear in a Bash
   command or in code written with Write/Edit. Text models are untouched, so
   R&D can still build Gemini text features.
3. The budget, spend log and state override (`.business-os`, image-budget.json,
   image-spend.jsonl, BUSINESS_OS_STATE_DIR) are not written from a session.
4. The founder's Gemini API key stays out of the conversation. It lives in the
   environment or in `.business-os/gemini-api-key` (Cowork has no other place
   that survives a session), and only generate_image.py reads it. Blocked:
   - a string shaped like a Google API key in the input of any tool except
     Write/Edit (Firebase and Maps browser keys have the same shape and belong
     in front-end code);
   - Read, Write or Edit on the key file; Grep inside .business-os, on the key
     file, or for the key prefix;
   - Bash commands that name the key file, read the variable's value, dump the
     environment, search for the key prefix, or read .business-os beyond its
     three known files. Naming the variable (`gh secret set GEMINI_API_KEY`)
     is allowed.

This is a guard against an agent routing around the meter or leaking the key
by mistake, not a sandbox: a determined command can still reach the file. The
script's .gitignore keeps the file out of git.
Blocks by exiting 2 with the reason on stderr.
"""
import json
import os
import re
import shlex
import sys

SCRIPT_NAME = "generate_image.py"
SCRIPT_SUFFIX = "/skills/generate-image/scripts/" + SCRIPT_NAME
SCRIPT_TOKEN_RE = re.compile(r"(^|[\s/\"'=])" + re.escape(SCRIPT_NAME) + r"\b")
SHELL_META_RE = re.compile(r"[;&|<>`\n]|\$\(")
IMAGE_MODEL_RE = re.compile(r"\b(gemini-[\w.-]*-image(-preview)?|imagen-[\w.-]+)\b", re.I)
STATE_RE = re.compile(r"\.business-os\b|image-budget\.json|image-spend\.jsonl|BUSINESS_OS_STATE_DIR")
WRITE_VERB_RE = re.compile(
    r"((?<![0-9&])>|\btee\b|\bcp\b|\bmv\b|\brm\b|\bsed\s+-i|\btouch\b|\bln\b|\bdd\b|\btruncate\b|\bchmod\b"
    r"|\b(python3?|node|perl|ruby)\s+-[ce]\b|-delete\b|\bexport\b|BUSINESS_OS_STATE_DIR\s*=)"
)
CODE_EXTS = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".sh", ".bash", ".rb", ".go",
             ".php", ".java", ".kt", ".rs", ".swift", ".json", ".yaml", ".yml", ".toml", ".env"}

KEY_FILE = "gemini-api-key"
KEY_FILE_RE = re.compile(re.escape(KEY_FILE), re.I)
# Google API keys: "AIza" and 35 more characters.
KEY_VALUE_RE = re.compile(r"AIza[0-9A-Za-z_\-]{30,}")
KEY_PREFIX = "AIza"
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
SAFE_STATE_FILE_RE = re.compile(r"\.business-os/(ledger\.md|image-spend\.jsonl|image-budget\.json)")
DIR_READ_RE = re.compile(
    r"\b(grep|egrep|rg|ag|ack|find|tar|zip|7z|rsync|scp|cp|cat|bat|head|tail|less|more|awk|sed|xargs"
    r"|strings|od|xxd|hexdump|base64|python3?|node|perl|ruby|tree|diff)\b|\*"
)

MODEL_MSG = ("חסום: יצירת תמונות (Nano Banana / Imagen) נעשית רק דרך ה-skill generate-image, "
             "שסופר את העלות מול תקציב התמונות החודשי.")
SCRIPT_MSG = ("חסום: את סקריפט התמונות מריצים רק בפקודה אחת, בדיוק כמו ב-SKILL.md — python3 ונתיב הסקריפט "
              "ואחריהם הפרמטרים; בלי שרשור פקודות, בלי משתנה סביבה לפני הפקודה, ובלי התווים ; & | < > ` "
              "בטקסט. את הסקריפט עצמו לא מעתיקים ולא משנים; לקריאה — הכלי Read.")
STATE_MSG = ("חסום: תקציב התמונות ויומן ההוצאות (.business-os) משתנים רק על ידי המייסד, מחוץ לשיחה. "
             "חסימת תקציב עולה כחסם בראש הדיווח.")
KEY_MSG = ("חסום: הפעולה עלולה לחשוף את מפתח ה-API של Gemini (קובץ המפתח ב-.business-os, ערך המשתנה, או "
           "הדפסת משתני הסביבה). רק סקריפט התמונות משתמש במפתח; כדי לדעת אם יש מפתח — generate_image.py "
           "--status. מתיקיית .business-os קוראים רק את ledger.md, image-spend.jsonl ו-image-budget.json, "
           "בשמם המלא (או בכלי Read).")
KEY_VALUE_MSG = ("חסום: הקלט מכיל מחרוזת שנראית כמו מפתח API של Google, והוא לא נשלח לפקודה, לסוכן או לשירות "
                 "חיצוני. מפתח דפדפן של Firebase או Maps, שנועד לקוד האתר — כותבים ישירות לקובץ עם Write. "
                 "אחרת, אם זה מפתח אמיתי — עצור, ודווח למייסד בראש הדיווח שצריך להחליף אותו ב-Google Cloud "
                 "(בלי לצטט אותו).")


def block(reason):
    print(reason, file=sys.stderr)
    sys.exit(2)


def official_invocation(command):
    """True only for `python3 <design-os copy of the script> <flags>` and nothing else."""
    if SHELL_META_RE.search(command):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if len(tokens) < 2 or not re.fullmatch(r"python3?", os.path.basename(tokens[0])):
        return False
    script = os.path.normpath(tokens[1])
    if not script.endswith(SCRIPT_SUFFIX) or ".." in tokens[1].split("/"):
        return False
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if root:
        # Any installed version of design-os: after a mid-session update the hook
        # still carries the old root while the skill already points at the new one.
        versions_dir = os.path.dirname(os.path.normpath(root))
        return script.startswith(versions_dir + os.sep)
    return True


def reaches_key(command):
    """True for a Bash command that could read or reveal the Gemini API key."""
    if (KEY_FILE_RE.search(command) or KEY_VAR_READ_RE.search(command) or KEY_PREFIX in command
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

    tool = event.get("tool_name") or ""
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}

    if tool not in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        if KEY_VALUE_RE.search(json.dumps(tool_input, ensure_ascii=False)):
            block(KEY_VALUE_MSG)

    if tool == "Bash":
        command = str(tool_input.get("command") or "")
        if official_invocation(command):
            sys.exit(0)
        if SCRIPT_TOKEN_RE.search(command):
            block(SCRIPT_MSG)
        if IMAGE_MODEL_RE.search(command):
            block(MODEL_MSG)
        if STATE_RE.search(command) and WRITE_VERB_RE.search(command):
            block(STATE_MSG)
        if reaches_key(command):
            block(KEY_MSG)
    elif tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if is_key_file(path):
            block(KEY_MSG)
        if STATE_RE.search(path):
            block(STATE_MSG)
        if os.path.basename(path) == SCRIPT_NAME:
            block(SCRIPT_MSG)
        if os.path.splitext(path)[1].lower() in CODE_EXTS:
            written = json.dumps({k: v for k, v in tool_input.items() if k != "file_path"}, ensure_ascii=False)
            if IMAGE_MODEL_RE.search(written):
                block(MODEL_MSG)
    elif tool == "Read":
        if is_key_file(tool_input.get("file_path")):
            block(KEY_MSG)
    elif tool == "Grep":
        where = f"{tool_input.get('path') or ''} {tool_input.get('glob') or ''}"
        if ".business-os" in where or KEY_FILE_RE.search(where) or KEY_PREFIX in str(tool_input.get("pattern") or ""):
            block(KEY_MSG)
    sys.exit(0)


if __name__ == "__main__":
    main()
