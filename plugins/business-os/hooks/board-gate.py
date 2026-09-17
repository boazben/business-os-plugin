"""business-os board gate: records Notion board writes that leave the contract.

Reads a PreToolUse payload for a Notion tool on stdin. It does not block board
work. The founder asked for a gate that warns and keeps a record instead of
standing in the way, so that Claude can do more of the board work and he does
less of it by hand. Three verdicts, all exiting 0:

- "ok"   — silent. Ordinary board work.
- "warn" — a systemMessage for the founder plus a row in
           ~/.business-os/board-log.md, and the call goes through. Everything
           that must trace back to a word from him: the two exits from
           "ממתין לאישור" (מאושר, לסבב נוסף) and his reply column
           "תגובת מייסד" — which Claude now writes for him, on his explicit
           approval in the conversation, so that he never has to open Notion
           (skills/notion-board §6). Also the board's structure, a page
           created without a parent, a page moved or duplicated.
- "ask"  — permissionDecision "ask", so he confirms in one click, plus the
           same row. Only what does not come back on its own: trashing or
           archiving a page, and handing work to Notion's own AI agent, which
           could do anything on the board on Claude's behalf.

What this file no longer enforces is now discipline in skills/notion-board and
skills/run-board. Three things make that an acceptable trade. The founder's own
message is the only thing that creates an approval, and it is quoted verbatim
into the task page, so an invented approval has no message behind it. The
record: every warn and ask lands in board-log.md with the session id and the
warning reaches him as it happens, so a wrong approval is visible rather than
silent, and a status is reversible in seconds. And the release gate is
untouched — what reaches customers goes live only when the founder clicks
Publish at the hosting provider, never because a task says "מאושר".

Two tool families are understood: the Notion connector (notion-update-page,
flat property values) and the Notion REST API as exposed by a local Notion MCP
server (API-patch-page and friends, nested values such as
{"status": {"name": "..."}}). A status set only by option id cannot be read,
so it counts as the founder's.

Why a denylist of the founder's own words rather than an allowlist of statuses:
the hook sees every Notion database he uses, not only the board, and an
allowlist would flag status updates in all of them.

Tests: hooks/tests/test_board_gate.py.
"""

import datetime
import json
import os
import re
import sys
import tempfile
import unicodedata

STATUS_NAMES = ("סטטוס", "status")
# Substrings that mean "approved" inside a status value: מאושר, אושר, אושרה,
# מאושרת all contain אושר; מאשר is the spelling without vav (niqqud form).
APPROVED_PARTS = ("אושר", "מאשר", "approved")
# The other exit the founder owns: sending a task back for another round
# ("לסבב נוסף"). Letters-only, so the space is already gone. Only inside a
# status value — a result line like "סבב 2: קוצר" is ordinary board work.
FOUNDER_STATUS_PARTS = APPROVED_PARTS + ("סבבנוסף", "anotherround")
# Exact values that mean "approved" in any other property.
APPROVED_VALUES = {"מאושר", "מאושרת", "אושר", "אושרה", "מאשר", "approved"}
# The founder's reply column, compared letters-only (so spaces are already
# gone): "תגובת מייסד" / "הערת מייסד" / "founder note". Matched as a substring
# so a renamed or suffixed column still counts.
FOUNDER_NOTE_NAMES = ("תגובתמייסד", "הערתמייסד", "foundernote", "founderreply")

# Tool names are compared lowercased with "_" turned into "-".
NOTION_AI_RE = re.compile(r"(spawn-session|send-message-to-session)$")
SCHEMA_RE = re.compile(
    r"(create-database|update-data-source"
    r"|create-a-database|update-a-database|create-a-data-source|update-a-data-source)$"
)
REORGANIZE_RE = re.compile(r"(move-pages|move-page|duplicate-page)$")
CREATE_PAGE_RE = re.compile(r"(create-pages|post-page|create-a-page)$")
UPDATE_PAGE_RE = re.compile(r"(update-page|patch-page|update-a-page)$")
TRASH_KEYS = ("in_trash", "archived")

CONTRACT = "החוזה: business-os:notion-board."
REASONS = {
    "approval": 'משימה עוברת ל"מאושר" או ל"לסבב נוסף". זה אמור לקרות רק אחרי שאמרת את זה בשיחה — לא ראית את השאלה? זו טעות, והסטטוס חוזר.',
    "founder_note": 'נכתב בעמודה "תגובת מייסד". אמור להיות מה שאתה אמרת, מילה במילה.',
    "schema": "קלוד משנה את מבנה הלוח — מסד, עמודה או אופציה.",
    "orphan": "קלוד יוצר דף בלי parent — הוא לא ייכנס ללוח אלא יישאר דף בודד.",
    "reorganize": "קלוד מזיז או משכפל דף בלוח.",
    "trash": "קלוד מוחק או מעביר לסל דף בלוח.",
    "notion_ai": "קלוד מעביר עבודה ל-Notion AI, שיכול לעשות כל דבר בלוח בשמו.",
}
LOG_PATH = (".business-os", "board-log.md")
LOG_HEADER = (
    "| time (UTC) | session | level | tool | what |\n"
    "|---|---|---|---|---|\n"
)
MAX_BYTES = 200_000


def letters(text):
    """Letters and digits only, casefolded; niqqud, marks, emoji, punctuation,
    spaces and invisible direction characters are all dropped."""
    decomposed = unicodedata.normalize("NFKD", str(text))
    kept = (c for c in decomposed if unicodedata.category(c)[0] in "LN")
    return unicodedata.normalize("NFC", "".join(kept)).casefold()


def strings(value):
    """Every string inside a value, at any depth (REST values are nested)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)


def has_key(value, key):
    if isinstance(value, dict):
        return key in value or any(has_key(v, key) for v in value.values())
    if isinstance(value, list):
        return any(has_key(v, key) for v in value)
    return False


def founder_status(properties):
    """True when the call sets a status only the founder sets: approved, or
    sent back for another round."""
    if isinstance(properties, str):
        try:
            properties = json.loads(properties)
        except ValueError:
            return any(part in letters(properties) for part in FOUNDER_STATUS_PARTS)
    if not isinstance(properties, dict):
        return False
    for name, value in properties.items():
        is_status = any(s in letters(name) for s in STATUS_NAMES)
        # {"status": {"id": "abc"}} names no option — it could be מאושר.
        if is_status and isinstance(value, (dict, list)) and has_key(value, "id") and not has_key(value, "name"):
            return True
        for text in strings(value):
            clean = letters(text)
            if clean in APPROVED_VALUES:
                return True
            if is_status and any(part in clean for part in FOUNDER_STATUS_PARTS):
                return True
    return False


def writes_founder_note(properties):
    """True when the call sets the founder's own reply property."""
    if isinstance(properties, str):
        try:
            properties = json.loads(properties)
        except ValueError:
            clean = letters(properties)
            return any(name in clean for name in FOUNDER_NOTE_NAMES)
    if not isinstance(properties, dict):
        return False
    return any(
        any(n in letters(name) for n in FOUNDER_NOTE_NAMES) for name in properties
    )


def any_properties(node, check):
    """True when `check` holds for any "properties" value anywhere in the call."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "properties" and check(value):
                return True
            if any_properties(value, check):
                return True
    elif isinstance(node, list):
        return any(any_properties(item, check) for item in node)
    return False


def trashes(node):
    if isinstance(node, dict):
        return any(node.get(k) is True for k in TRASH_KEYS) or any(trashes(v) for v in node.values())
    if isinstance(node, list):
        return any(trashes(v) for v in node)
    return False


def verdict(tool, tool_input):
    """("ok" | "warn" | "ask", reason) for this call."""
    if NOTION_AI_RE.search(tool):
        return "ask", REASONS["notion_ai"]
    if SCHEMA_RE.search(tool):
        return "warn", REASONS["schema"]
    if REORGANIZE_RE.search(tool):
        return "warn", REASONS["reorganize"]
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except ValueError:
            return "ok", ""
    if not isinstance(tool_input, dict):
        return "ok", ""
    creating = bool(CREATE_PAGE_RE.search(tool))
    if not creating and not UPDATE_PAGE_RE.search(tool):
        return "ok", ""
    if trashes(tool_input):
        return "ask", REASONS["trash"]
    if any_properties(tool_input, founder_status):
        return "warn", REASONS["approval"]
    if any_properties(tool_input, writes_founder_note):
        return "warn", REASONS["founder_note"]
    if creating and not tool_input.get("parent"):
        return "warn", REASONS["orphan"]
    return "ok", ""


def cell(value, limit=120):
    return " ".join(str(value).split()).replace("|", "/")[:limit]


def record(payload, tool, level, reason):
    """Append one row to the board log. A failure here costs a row, not a call."""
    try:
        home = os.environ.get("HOME")
        if not home:
            return
        path = os.path.join(home, *LOG_PATH)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        row = "| " + " | ".join(cell(v) for v in (
            datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            (payload.get("session_id") or "")[:8],
            level,
            tool,
            reason,
        )) + " |\n"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            if os.fstat(f.fileno()).st_size == 0:
                f.write(LOG_HEADER)
            f.write(row)
        if os.path.getsize(path) > MAX_BYTES:
            trim(path)
    except Exception:  # noqa: BLE001 - the log is a record, never a gate
        return


def trim(path):
    with open(path, encoding="utf-8") as f:
        body = f.readlines()[2:]
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "w") as out:
        out.write(LOG_HEADER)
        out.writelines(body[len(body) // 2:])
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def main():
    payload = json.load(sys.stdin)
    tool = str(payload.get("tool_name", "")).lower().replace("_", "-")
    level, reason = verdict(tool, payload.get("tool_input"))
    if level == "ok":
        return 0

    record(payload, tool, level, reason)
    warning = f"⚠️ business-os — לוח: {reason} {CONTRACT}"
    if level == "ask":
        out = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": f"{reason} זו פעולה שלא חוזרת לבד. {CONTRACT}",
            },
            "systemMessage": warning,
        }
    else:
        out = {"systemMessage": warning + " עבר ונרשם ב-~/.business-os/board-log.md."}
    json.dump(out, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - any failure hands off to the fallback
        sys.exit(3)
