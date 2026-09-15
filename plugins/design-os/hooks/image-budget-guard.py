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

This is a guard against an agent routing around the meter, not a sandbox.
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

MODEL_MSG = ("חסום: יצירת תמונות (Nano Banana / Imagen) נעשית רק דרך ה-skill generate-image, "
             "שסופר את העלות מול תקציב התמונות החודשי.")
SCRIPT_MSG = ("חסום: את סקריפט התמונות מריצים רק בפקודה אחת, בדיוק כמו ב-SKILL.md — python3 ונתיב הסקריפט "
              "ואחריהם הפרמטרים; בלי שרשור פקודות, בלי משתנה סביבה לפני הפקודה, ובלי התווים ; & | < > ` "
              "בטקסט. את הסקריפט עצמו לא מעתיקים ולא משנים; לקריאה — הכלי Read.")
STATE_MSG = ("חסום: תקציב התמונות ויומן ההוצאות (.business-os) משתנים רק על ידי המייסד, מחוץ לשיחה. "
             "חסימת תקציב עולה כחסם בראש הדיווח.")


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


def main():
    try:
        event = json.load(sys.stdin)
    except (ValueError, OSError):
        sys.exit(0)

    tool = event.get("tool_name") or ""
    tool_input = event.get("tool_input") or {}

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
    elif tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if STATE_RE.search(path):
            block(STATE_MSG)
        if os.path.basename(path) == SCRIPT_NAME:
            block(SCRIPT_MSG)
        if os.path.splitext(path)[1].lower() in CODE_EXTS:
            written = json.dumps({k: v for k, v in tool_input.items() if k != "file_path"}, ensure_ascii=False)
            if IMAGE_MODEL_RE.search(written):
                block(MODEL_MSG)
    sys.exit(0)


if __name__ == "__main__":
    main()
