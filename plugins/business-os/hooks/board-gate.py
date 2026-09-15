"""business-os board gate: only the founder approves board tasks.

Reads a PreToolUse payload for a Notion tool on stdin and exits 2 (block) when
the call would:
- set a status-like property to an approved value, or any property to exactly
  an approved value;
- change a data source's schema around an approved value (renaming an option
  to it approves every task that had the old one), or trash a data source;
- duplicate a page (a copy of an approved task is an approved task), or hand
  work to Notion's own AI agent, which could set the status on our behalf.
Exits 0 otherwise. Any other exit code means the check itself failed;
board-gate.sh then falls back to a coarser text match instead of allowing.

Why a denylist of approved words rather than an allowlist of statuses: the hook
sees every Notion database the founder uses, not only the board, and an
allowlist would block status updates in all of them.
"""

import json
import sys
import unicodedata

STATUS_NAMES = ("סטטוס", "status")
# Substrings that mean "approved" inside a status value: מאושר, אושר, אושרה,
# מאושרת all contain אושר; מאשר is the spelling without vav (niqqud form).
APPROVED_PARTS = ("אושר", "מאשר", "approved")
# Exact values that mean "approved" in any other property.
APPROVED_VALUES = {"מאושר", "מאושרת", "אושר", "אושרה", "מאשר", "approved"}
BLOCKED_TOOLS = ("duplicate-page", "spawn-session", "send-message-to-session")

MESSAGE = (
    "business-os: only the founder approves board tasks, in Notion itself. "
    "Claude does not set a status to 'מאושר' (approved), change status options "
    "around it, trash a database, duplicate pages, or ask Notion AI to edit on "
    "its behalf. Leave the task at 'ממתין לאישור' and tell the founder what "
    "needs approval."
)


def letters(text):
    """Letters and digits only, casefolded; niqqud, marks, emoji, punctuation,
    spaces and invisible direction characters are all dropped."""
    decomposed = unicodedata.normalize("NFKD", str(text))
    kept = (c for c in decomposed if unicodedata.category(c)[0] in "LN")
    return unicodedata.normalize("NFC", "".join(kept)).casefold()


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


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


def blocked(tool, tool_input):
    if tool.endswith(BLOCKED_TOOLS):
        return True
    if not isinstance(tool_input, dict):
        return False
    if tool.endswith("update-data-source"):
        dumped = letters(json.dumps(tool_input, ensure_ascii=False))
        return (tool_input.get("in_trash") is True
                or any(part in dumped for part in APPROVED_PARTS))
    if tool.endswith(("create-pages", "update-page")):
        return any_properties_approve(tool_input)
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
