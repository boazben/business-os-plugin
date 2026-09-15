"""business-os — append one Agent/Task call to the delegation ledger.

Reads a PreToolUse event on stdin and appends a row to
$HOME/.business-os/ledger.md: when, which session, who called whom, and
whether that pairing fits the org structure. It never records the prompt text
and never blocks anything; pre-tool.sh runs it with stderr discarded, so a
failure here costs a missing row, not a broken session.

Department heads follow the `<x>-os:<x>-lead` naming convention, so a new
department needs no change here. platform-lead is the one exception to the
convention. Anything else inside a plugin is a team member — including
rnd-os:security-lead, which only rnd-lead calls.
"""
import datetime
import fcntl
import json
import os
import sys
import tempfile

MAX_BYTES = 1_000_000
EXTRA_HEADS = {"business-os:platform-lead"}
HEADER = (
    "| time (UTC) | session | caller | target | policy | description | folder |\n"
    "|---|---|---|---|---|---|---|\n"
)


def split(name):
    plugin, _, short = name.rpartition(":")
    return (plugin or None), short


def is_head(name):
    plugin, short = split(name)
    if plugin is None:
        return False
    prefix = plugin[:-3] if plugin.endswith("-os") else plugin
    return name in EXTRA_HEADS or short == prefix + "-lead"


def policy(caller, target):
    caller_plugin, _ = split(caller)
    target_plugin, _ = split(target)

    if caller == "main":
        if target_plugin and not is_head(target):
            return "deviation: CEO called a team member directly"
        return "ok"
    if caller_plugin is None:
        return "ok"
    if target_plugin is None:
        return "deviation: department agent called a built-in agent"
    if is_head(caller):
        if target_plugin == caller_plugin or is_head(target):
            return "ok"
        return "deviation: department head called another department's team member"
    if target_plugin == caller_plugin and not is_head(target):
        return "ok"
    return "deviation: team member called outside its own team"


def cell(value, limit=120):
    return " ".join(str(value).split()).replace("|", "/")[:limit]


def trim(f, path):
    f.seek(0)
    body = f.readlines()[2:]
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "w") as out:
        out.write(HEADER)
        out.writelines(body[len(body) // 2:])
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def main():
    home = os.environ.get("HOME")
    if not home:
        return
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return

    tool_input = event.get("tool_input") or {}
    caller = event.get("agent_type") or "main"
    target = tool_input.get("subagent_type") or "general-purpose"
    verdict = policy(caller, target)
    note = os.environ.get("BUSINESS_OS_NOTE")
    if note:
        verdict = f"{verdict}; {note}"
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    row = "| " + " | ".join(cell(v) for v in (
        now,
        (event.get("session_id") or "")[:8],
        caller,
        target,
        verdict,
        tool_input.get("description") or "",
        os.path.basename((event.get("cwd") or "").rstrip("/")),
    )) + " |\n"

    path = os.path.join(home, ".business-os", "ledger.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        if os.fstat(f.fileno()).st_size == 0:
            f.write(HEADER)
        f.write(row)
        f.flush()
        if os.fstat(f.fileno()).st_size > MAX_BYTES:
            trim(f, path)


if __name__ == "__main__":
    main()
