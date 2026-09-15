"""business-os — append one Agent/Task call to the delegation ledger.

Reads a PreToolUse event on stdin and appends a row to
$HOME/.business-os/ledger.md: when, which session, who called whom, and
whether that pairing fits the org structure. It never records the prompt text
and never blocks anything; pre-tool.sh runs it with stderr discarded, so a
failure here costs a missing row, not a broken session.

The policy column is derived from plugin namespaces, so a new department
needs no table update: `<plugin>:<name>-lead` is a department head, any other
`<plugin>:<name>` is a member of that plugin's team.
"""
import datetime
import json
import os
import sys

MAX_BYTES = 1_000_000
HEADER = (
    "| time (UTC) | session | caller | target | policy | description | cwd |\n"
    "|---|---|---|---|---|---|---|\n"
)


def split(name):
    plugin, _, short = name.rpartition(":")
    return (plugin or None), short


def policy(caller, target):
    caller_plugin, caller_name = split(caller)
    target_plugin, target_name = split(target)
    target_is_lead = target_name.endswith("-lead")

    if caller == "main":
        if target_plugin and not target_is_lead:
            return "deviation: CEO called a team member directly"
        return "ok"
    if caller_plugin is None:
        return "ok"
    if target_plugin is None:
        return "deviation: department agent called a built-in agent"
    if caller_name.endswith("-lead"):
        if target_plugin == caller_plugin or target_is_lead:
            return "ok"
        return "deviation: department head called another department's team member"
    if target_plugin == caller_plugin and not target_is_lead:
        return "ok"
    return "deviation: team member called outside its own team"


def cell(value, limit=120):
    return " ".join(str(value).split()).replace("|", "/")[:limit]


def trim(path):
    with open(path) as f:
        lines = f.readlines()
    body = lines[2:]
    with open(path, "w") as f:
        f.write(HEADER)
        f.writelines(body[len(body) // 2:])


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return

    tool_input = event.get("tool_input") or {}
    caller = event.get("agent_type") or "main"
    target = tool_input.get("subagent_type") or "general-purpose"
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    row = "| " + " | ".join(cell(v) for v in (
        now,
        (event.get("session_id") or "")[:8],
        caller,
        target,
        policy(caller, target),
        tool_input.get("description") or "",
        event.get("cwd") or "",
    )) + " |\n"

    path = os.path.join(os.environ.get("HOME") or ".", ".business-os", "ledger.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    is_new = not os.path.exists(path)
    with open(path, "a") as f:
        if is_new:
            f.write(HEADER)
        f.write(row)
    if os.path.getsize(path) > MAX_BYTES:
        trim(path)


if __name__ == "__main__":
    main()
