"""business-os board gate: only the founder approves board tasks.

Reads a PreToolUse payload for a Notion write tool on stdin. Exits 2 (block)
when the call would set a task to the approved status, would change the
board's schema around that status (renaming an option to it approves every
task that had the old one), or would trash a data source. Exits 0 otherwise.
Any other exit code means the check itself failed; board-gate.sh then falls
back to a coarser text match rather than letting the call through unchecked.
"""

import json
import re
import sys
import unicodedata

# The option name, and its spelling without the vav (as written with niqqud).
APPROVED_FORMS = ("מאושר", "מאשר")
STATUS_NAMES = ("סטטוס", "status")

# Characters that render as nothing but would defeat a plain comparison.
INVISIBLE = re.compile("[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]")

MESSAGE = (
    "business-os: only the founder approves board tasks. Setting a task to "
    "'מאושר', changing the board's status options around it, or trashing the "
    "board is done by the founder in Notion itself. Leave the task at "
    "'ממתין לאישור' and tell the founder what needs approval."
)


def norm(text):
    decomposed = unicodedata.normalize("NFKD", text)
    # Drop combining marks (Hebrew niqqud included) and invisible characters.
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return INVISIBLE.sub("", unicodedata.normalize("NFC", stripped)).strip()


def mentions_approved(text):
    return any(form in text for form in APPROVED_FORMS)


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
            return mentions_approved(norm(properties))
    if not isinstance(properties, dict):
        return False
    for name, value in properties.items():
        is_status = any(s in norm(str(name)).lower() for s in STATUS_NAMES)
        for text in strings(value):
            clean = norm(text)
            # A status column blocks on any mention ("✅ מאושר"); other columns
            # only on the exact value, so a title like "האם זה מאושר" passes.
            if clean in APPROVED_FORMS or (is_status and mentions_approved(clean)):
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


def main():
    payload = json.load(sys.stdin)
    tool = norm(str(payload.get("tool_name", ""))).lower().replace("_", "-")
    tool_input = payload.get("tool_input") or {}

    if "data-source" in tool:
        dumped = norm(json.dumps(tool_input, ensure_ascii=False))
        blocked = mentions_approved(dumped) or tool_input.get("in_trash") is True
    else:
        blocked = any_properties_approve(tool_input)

    if blocked:
        print(MESSAGE, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
