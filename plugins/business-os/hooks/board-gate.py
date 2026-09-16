"""business-os board gate: keeps the Notion board inside its contract.

Reads a PreToolUse payload for a Notion tool on stdin and exits 2 (block) when
the call would:
- set a status-like property to an approved value, or any property to exactly
  an approved value (only the founder approves, in Notion's own UI);
- create a database, change any data source's schema, or trash one (the
  schema is the contract in skills/notion-board; renaming an option to
  "approved" would approve every task that had the old one);
- create a page without a parent (a loose page instead of a board row);
- move, duplicate or trash pages (a copy of an approved task is an approved
  task; the board keeps its history);
- hand work to Notion's own AI agent, which could do any of this for Claude.
Exits 0 otherwise. Any other exit code means the check itself failed;
board-gate.sh then falls back to a coarser text match instead of allowing.

Two tool families are understood: the Notion connector (notion-update-page,
flat property values) and the Notion REST API as exposed by a local Notion MCP
server (API-patch-page and friends, nested values such as
{"status": {"name": "..."}}). A status set only by option id cannot be read,
so it is blocked.

Why a denylist of approved words rather than an allowlist of statuses: the hook
sees every Notion database the founder uses, not only the board, and an
allowlist would block status updates in all of them.

Tests: hooks/tests/test_board_gate.py.
"""

import json
import re
import sys
import unicodedata

STATUS_NAMES = ("סטטוס", "status")
# Substrings that mean "approved" inside a status value: מאושר, אושר, אושרה,
# מאושרת all contain אושר; מאשר is the spelling without vav (niqqud form).
APPROVED_PARTS = ("אושר", "מאשר", "approved")
# Exact values that mean "approved" in any other property.
APPROVED_VALUES = {"מאושר", "מאושרת", "אושר", "אושרה", "מאשר", "approved"}

# Tool names are compared lowercased with "_" turned into "-".
STRUCTURE_RE = re.compile(
    r"(create-database|update-data-source|move-pages|duplicate-page|spawn-session|send-message-to-session"
    r"|create-a-database|update-a-database|create-a-data-source|update-a-data-source|move-page)$"
)
CREATE_PAGE_RE = re.compile(r"(create-pages|post-page|create-a-page)$")
UPDATE_PAGE_RE = re.compile(r"(update-page|patch-page|update-a-page)$")
TRASH_KEYS = ("in_trash", "archived")

MESSAGE = (
    "business-os: this Notion call is outside the board contract "
    "(skill business-os:notion-board). Claude does not set a task to 'מאושר' "
    "(only the founder approves, in Notion itself), create databases, change "
    "a database's columns or options, create pages without a parent, move, "
    "duplicate or trash pages, or hand work to Notion AI. Leave approvals at "
    "'ממתין לאישור' and tell the founder what change is needed."
)


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


def approves(properties):
    if isinstance(properties, str):
        try:
            properties = json.loads(properties)
        except ValueError:
            return any(part in letters(properties) for part in APPROVED_PARTS)
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
            if is_status and any(part in clean for part in APPROVED_PARTS):
                return True
    return False


def any_properties_approve(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "properties" and approves(value):
                return True
            if any_properties_approve(value):
                return True
    elif isinstance(node, list):
        return any(any_properties_approve(item) for item in node)
    return False


def trashes(node):
    if isinstance(node, dict):
        return any(node.get(k) is True for k in TRASH_KEYS) or any(trashes(v) for v in node.values())
    if isinstance(node, list):
        return any(trashes(v) for v in node)
    return False


def blocked(tool, tool_input):
    if STRUCTURE_RE.search(tool):
        return True
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except ValueError:
            return False
    if not isinstance(tool_input, dict):
        return False
    creating = bool(CREATE_PAGE_RE.search(tool))
    if creating and not tool_input.get("parent"):
        return True
    if creating or UPDATE_PAGE_RE.search(tool):
        return trashes(tool_input) or any_properties_approve(tool_input)
    return False


def main():
    payload = json.load(sys.stdin)
    tool = str(payload.get("tool_name", "")).lower().replace("_", "-")
    if blocked(tool, payload.get("tool_input")):
        print(MESSAGE, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - any failure hands off to the fallback
        sys.exit(3)
