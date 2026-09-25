"""business-os — the delegation ledger at $HOME/.business-os/ledger.md.

Two kinds of row. Neither ever records prompt or reply text, and neither
blocks anything: the hooks run this with stderr discarded and exit 0, so a
failure here costs a missing row, not a broken session.

- A call (PreToolUse on Agent/Task, via pre-tool.sh): when, which session,
  who called whom, and whether that pairing fits the org structure.
- A finish (`--finish`, on SubagentStop and Stop): who finished, and from its
  transcript only numbers — model, tool calls, minutes, tokens — plus the line
  of its last reply that starts with "פסיקה או ממצא:" or "פסיקה:" (cut to 120
  characters). A subagent gets a row each time it finishes; the main
  conversation gets one "main total" row per session — its own tokens only,
  the subagents are in their rows — rewritten and moved to the end on every
  turn. A finish whose transcript cannot be read says why, with the names (not
  the values) of the fields the hook was given.

Department heads follow the `<x>-os:<x>-lead` naming convention, so a new
department needs no change here. platform-lead is the one exception to the
convention. Anything else inside a plugin is a team member — including
rnd-os:security-lead, which only rnd-lead calls.
"""
import collections
import datetime
import fcntl
import json
import os
import re
import sys
import tempfile
import time

MAX_BYTES = 1_000_000
MAX_TRANSCRIPT = 200_000_000
EXTRA_HEADS = {"business-os:platform-lead"}
# Unprefixed names outside this set are department agents called without their
# plugin prefix, which Claude Code does not resolve (seen live in Cowork).
BUILT_IN_AGENTS = {"general-purpose", "Explore", "Plan", "claude", "claude-code-guide", "statusline-setup"}
HEADER = (
    "| time (UTC) | session | caller | target | policy | description | folder |\n"
    "|---|---|---|---|---|---|---|\n"
)
FINISHED = "finished"
MAIN_TOTAL = "main total"
# Price ratios to plain input, as in scripts/telemetry: cache read 0.1,
# 5-minute cache write 1.25, 1-hour cache write 2, output 5.
WEIGHTS = {"inp": 1, "cw5": 1.25, "cw1h": 2, "cr": 0.1, "out": 5}
# The transcript is still being written when the hook fires (seen live on Claude Code 2.1.274: a
# subagent's last response landed after SubagentStop ran), so the row waits for it to hold still.
SETTLE_LIMIT = 5.0
try:  # the tests widen it, so a slow machine can't make them flaky
    SETTLE_QUIET = float(os.environ.get("BUSINESS_OS_SETTLE_QUIET", 0.5))
except ValueError:
    SETTLE_QUIET = 0.5
# Some transcripts keep only a response's opening output count: seen 25.9.2026 in a Cowork
# Explore subagent ([[4, 4, 4], [1, 1], [1], [4, 4]] per response) and locally in about 1 in 5
# transcripts (Claude Code 2.1.234-2.1.280, every model). A response whose recorded output is below
# one token per MIN_CHARS_PER_TOKEN characters of its content cannot be a final count (no text runs
# at 8 characters a token), so its output is estimated at CHARS_PER_TOKEN — about right across
# Hebrew, English and JSON — and the row shows "out ~N".
MIN_CHARS_PER_TOKEN, CHARS_PER_TOKEN = 8, 3
VERDICT_RE = re.compile(r"^[\s>*_#-]*(פסיקה או ממצא|פסיקה)[*_]*\s*:")


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
        if target in BUILT_IN_AGENTS:
            return "deviation: department agent called a built-in agent"
        return "deviation: agent name without plugin prefix (call likely failed)"
    if is_head(caller):
        if target_plugin == caller_plugin or is_head(target):
            return "ok"
        return "deviation: department head called another department's team member"
    if target_plugin == caller_plugin and not is_head(target):
        return "ok"
    return "deviation: team member called outside its own team"


def cell(value, limit=120):
    return " ".join(str(value).split()).replace("|", "/")[:limit]


def short(n):
    n = round(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1_000:.0f}K"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def transcript_stats(path):
    """Numbers from one transcript. One API response spans several lines; per message id the line
    with the highest output count is kept, and the content blocks are counted once each."""
    usage, models, blocks, tools = {}, {}, {}, set()
    first = last = None
    last_text = ""
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"assistant"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict) or d.get("type") != "assistant":
                continue
            m = d.get("message") or {}
            if not isinstance(m, dict) or m.get("model") == "<synthetic>":  # Claude Code's own filler
                continue
            try:
                ts = datetime.datetime.fromisoformat(str(d.get("timestamp")).replace("Z", "+00:00"))
                first, last = first or ts, ts
            except ValueError:
                pass
            mid = m.get("id") or d.get("requestId") or f"line-{len(usage)}"
            for c in m.get("content") or []:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use":
                    size = len(json.dumps(c.get("input") or {}, ensure_ascii=False))
                else:
                    size = len(str(c.get("text") or c.get("thinking") or ""))
                key = c.get("id") or (c.get("type"), size, str(c.get("text") or c.get("thinking") or "")[:40])
                blocks.setdefault(mid, {})[key] = size
                if c.get("type") == "tool_use" and c.get("id"):
                    tools.add(c["id"])
                elif c.get("type") == "text" and (c.get("text") or "").strip():
                    last_text = c["text"]
            u = m.get("usage")
            if isinstance(u, dict):
                kept = usage.get(mid)
                if kept is None or (u.get("output_tokens") or 0) >= (kept.get("output_tokens") or 0):
                    usage[mid] = u
                models[mid] = m.get("model") or "?"

    t = dict.fromkeys(WEIGHTS, 0)
    estimated = False
    for mid, u in usage.items():
        cw = u.get("cache_creation_input_tokens") or 0
        parts = u.get("cache_creation") or {}
        c5 = parts.get("ephemeral_5m_input_tokens") or 0
        c1 = parts.get("ephemeral_1h_input_tokens") or 0
        if c5 + c1 != cw:  # no split given: count it as a 5-minute write
            c5, c1 = cw, 0
        t["inp"] += u.get("input_tokens") or 0
        t["cw5"] += c5
        t["cw1h"] += c1
        t["cr"] += u.get("cache_read_input_tokens") or 0
        out = u.get("output_tokens") or 0
        chars = sum(blocks.get(mid, {}).values())
        if out * MIN_CHARS_PER_TOKEN < chars:
            out, estimated = round(chars / CHARS_PER_TOKEN), True
        t["out"] += out
    model = collections.Counter(models.values()).most_common(1)
    return {
        "model": re.sub(r"^claude-", "", model[0][0]) if model else "?",
        "requests": len(usage),
        "tools": len(tools),
        "minutes": round((last - first).total_seconds() / 60) if first and last else 0,
        "tokens": t,
        "out_estimated": estimated,
        "units": sum(t[k] * w for k, w in WEIGHTS.items()),
        "last_text": last_text,
    }


def verdict_line(text):
    for line in (text or "").splitlines():
        if VERDICT_RE.match(line):
            return line.strip()
    return ""


def finished_transcript(event):
    if event.get("hook_event_name") != "SubagentStop":
        return event.get("transcript_path")
    if event.get("agent_transcript_path"):
        return event["agent_transcript_path"]
    main, agent = event.get("transcript_path"), event.get("agent_id")
    if main and agent:  # <session>.jsonl -> <session>/subagents/agent-<id>.jsonl
        return os.path.join(re.sub(r"\.jsonl$", "", main), "subagents", f"agent-{agent}.jsonl")
    return None


def describe(stats, minutes=True):
    """minutes=False for the main conversation, whose span includes the founder's idle time."""
    t = stats["tokens"]
    return (
        f"{stats['model']} · {stats['requests']} requests · {stats['tools']} tools"
        + (f" · {stats['minutes']}m" if minutes else "")
        + f" · in {short(t['inp'])} · cache write {short(t['cw5'] + t['cw1h'])}"
        f" · cache read {short(t['cr'])} · out {'~' if stats['out_estimated'] else ''}{short(t['out'])} · {short(stats['units'])} units"
    )


def settle(path):
    """Wait until the file exists and its size has held still for SETTLE_QUIET, at most SETTLE_LIMIT."""
    end = time.monotonic() + SETTLE_LIMIT
    size, since = None, time.monotonic()
    while time.monotonic() < end:
        try:
            now = os.path.getsize(path)
        except OSError:
            now = None
        if now != size:
            size, since = now, time.monotonic()
        elif now is not None and time.monotonic() - since >= SETTLE_QUIET:
            return
        time.sleep(0.1)


def detach():
    """Let the hook return at once and finish the row in a background child, so no turn waits
    for settle(). True in the child. False when fork fails: the row is then written inline, with
    whatever the transcript has, and without waiting. A fork, not the hooks' own "async" flag,
    because it works the same on whatever Claude Code version Cowork runs."""
    try:
        if os.fork():
            os._exit(0)
    except OSError:
        return False
    os.setsid()
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):  # Claude Code waits for the hook's pipes to close
        os.dup2(devnull, fd)
    return True


def finish_row(event, wait=True):
    """(who, target, verdict, description) for a SubagentStop or Stop event. The verdict line is
    taken from the agent's own last text in the transcript; the event's last_assistant_message
    (which may include tool results) only when the transcript has none."""
    subagent = event.get("hook_event_name") == "SubagentStop"
    who = (event.get("agent_type") or "subagent") if subagent else "main"
    target = FINISHED if subagent else MAIN_TOTAL
    reply = event.get("last_assistant_message") if subagent else None
    path = os.path.expanduser(str(finished_transcript(event) or ""))
    if path and wait:
        settle(path)
    fields = ",".join(sorted(str(k) for k in event))
    if not path or not os.path.isfile(path):
        return who, target, verdict_line(reply) or "-", f"no usage: transcript not found (fields: {fields})"
    if os.path.getsize(path) > MAX_TRANSCRIPT:
        return who, target, verdict_line(reply) or "-", "no usage: transcript too large to read"
    try:
        stats = transcript_stats(path)
    except Exception as e:  # a row that says so, not a missing row that reads as "the hook never ran"
        return who, target, verdict_line(reply) or "-", f"no usage: error ({type(e).__name__})"
    verdict = (verdict_line(stats["last_text"]) or verdict_line(reply)) if subagent else ""
    return who, target, verdict or "-", describe(stats, minutes=subagent)


def main_total_of(session):
    """Picks the session's earlier "main total" row, which the new one replaces."""
    def drop(line):
        c = [c.strip() for c in line.strip().strip("|").split("|")]
        return len(c) > 3 and c[1] == session and c[3] == MAIN_TOTAL
    return drop


def rewrite(path, rows):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(HEADER)
        out.writelines(rows)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def write(path, row, drop=None):
    """Append row under an exclusive lock. drop, when given, picks existing rows to remove first,
    so a row that is updated moves to the end instead of repeating."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Every main turn replaces the file, so under load a writer can find it replaced several
    # times in a row: retry for a while, not a fixed number of times.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "r+", encoding="utf-8", errors="replace") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            # A rewrite by another writer may have replaced the file while we
            # waited for the lock; appending to the old inode would lose the row.
            try:
                same_file = os.fstat(f.fileno()).st_ino == os.stat(path).st_ino
            except FileNotFoundError:
                same_file = False
            if not same_file:
                continue
            if drop is None and os.fstat(f.fileno()).st_size + len(row.encode()) <= MAX_BYTES:
                if os.fstat(f.fileno()).st_size == 0:
                    f.write(HEADER)
                f.write(row)
                f.flush()
                return
            f.seek(0)
            rows = [r for r in f.readlines()[2:] if not (drop and drop(r))] + [row]
            if sum(len(r.encode()) for r in rows) + len(HEADER.encode()) > MAX_BYTES:
                rows = rows[len(rows) // 2:]
            rewrite(path, rows)
            return


def main():
    home = os.environ.get("HOME")
    if not home:
        return
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    if not isinstance(event, dict):
        return

    session = (event.get("session_id") or "")[:8]
    drop = None
    finish = "--finish" in sys.argv[1:]
    if finish:
        values = finish_row(event, wait=detach())
        if values[1] == MAIN_TOTAL:
            drop = main_total_of(session)
    else:
        tool_input = event.get("tool_input") or {}
        caller = event.get("agent_type") or "main"
        target = tool_input.get("subagent_type") or "general-purpose"
        verdict = policy(caller, target)
        note = os.environ.get("BUSINESS_OS_NOTE")
        if note:
            verdict = f"{verdict}; {note}"
        values = (caller, target, verdict, tool_input.get("description") or "")

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    folder = os.path.basename((event.get("cwd") or "").rstrip("/"))
    limits = (120, 120, 120, 120, 120, 300 if finish else 120, 120)
    row = "| " + " | ".join(cell(v, n) for v, n in zip((now, session, *values, folder), limits)) + " |\n"
    write(os.path.join(home, ".business-os", "ledger.md"), row, drop)


if __name__ == "__main__":
    main()
