"""business-os — the delegation ledger at $HOME/.business-os/ledger.md.

Three kinds of row. None ever records prompt or reply text, and none blocks
anything: the hooks run this with stderr discarded and exit 0, so a failure
here costs a missing row, not a broken session.

- A call (PreToolUse on Agent/Task, via pre-tool.sh): when, which session,
  who called whom, and whether that pairing fits the org structure (and a
  flag when a team member's brief lacks the "כללי עבודה:" line). Plus a short
  sha256 of the brief itself ("brief@1a2b3c4d") and, for every file the brief
  names that exists, its name and a short sha256 ("saw name@1a2b3c4d") — which
  version of a file each agent was given.
- A finish (`--finish`, on SubagentStop and Stop): who finished, and from its
  transcript only numbers — model, tool calls, minutes, tokens — plus the newest
  line of its own text that starts with "פסיקה או ממצא:" or "פסיקה:" (cut to 120
  characters), and the hash of the brief it was started with — the first
  message of its transcript, which is the call's prompt — so a finish is tied to
  its own call, not to another call of the same agent. A subagent gets a row each time it finishes; the main
  conversation gets one "main total" row per session — its own tokens only,
  the subagents are in their rows — rewritten and moved to the end on every
  turn. A finish whose transcript cannot be read says why, with the names (not
  the values) of the fields the hook was given.
- A return, on Cowork's device_commit_files: `--commit` (PreToolUse) writes
  "returning" before the call; after it, `--returned` (PostToolUse, which fires
  only on success) writes "returned" — or "return failed" if the response says
  isError — and `--failed` (PostToolUseFailure) writes "return failed". Each lists the name every
  file gets in the project folder and a short sha256 of the content sent, so a
  script can count what was made in the cloud and never returned.

In Cowork (a /mnt/user-data/outputs folder exists) every row is also written
to a file of its own for the session, under outputs/business-os-ledger/, which
the CEO returns to the project folder in one call at the end of a run: the
cloud copy is gone when the session ends.

Department heads follow the `<x>-os:<x>-lead` naming convention, so a new
department needs no change here. platform-lead is the one exception to the
convention. Anything else inside a plugin is a team member — including
rnd-os:security-lead, which only rnd-lead calls.
"""
import collections
import datetime
import fcntl
import glob
import hashlib
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
RETURNING, RETURNED, RETURN_FAILED = "returning", "returned", "return failed"
SAW = " · saw "
BRIEF = " · brief@"
# A brief names a handful of files; a cap keeps a pasted listing from turning one row into a page.
MAX_BRIEF_FILES = 20
# The hook runs before the agent starts, so reading stays small: a file past MAX_HASHED, or past
# HASH_BUDGET read in one call, is named "@big" and not read; at most MAX_CANDIDATES path starts are tried.
MAX_HASHED = 50_000_000
HASH_BUDGET = 200_000_000
MAX_CANDIDATES = 300
# Where a path starts in a brief: after whitespace, a quote (also ״), a bracket, ':', '=', markdown
# emphasis, or a Hebrew prefix letter and hyphen ("ב-/mnt/..."); never "//" (a URL).
PATH_START = re.compile(r"(?:(?<![^\s`'\"(<\[{:=*_\u05f4])|(?<=[\u05d0-\u05ea]-))(?:~/|/(?!/))")
# What may follow a file name in running text; "." "," ";" ":" "!" "?" only before a space or
# the end, so "/v/ad.md" is never read as a file "/v/ad".
PATH_END = set("`'\")]}>*_—\u05f4") | {" ", "\t"}
PATH_PUNCT = set(".,;:!?")
MAX_PATH_CHARS = 1024
SESSION_DIR = "business-os-ledger"
# The line every brief to a team member carries (shared/head-review-rules.md); a call without it is flagged.
WORK_RULES = "כללי עבודה:"
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
# The verdict line however it is dressed: a list mark, a quote, a table cell, backticks (as the
# shared contract shows it), a number or "שורה 2:" before it, invisible direction marks; then a
# parenthesis, ":", a dash or a table bar. It must also carry a ruling word — the contract makes
# the (עובר / עובר בתנאי / עוצר) mapping part of the line — so a cited ruling ("פסיקה: ע"א …") or
# a table header is never taken for the reviewer's verdict.
VERDICT_RE = re.compile(
    r"^[\s>*_#`|\u200e\u200f\"'״-]*(?:\d+[.)]\s*)?(?:[*_]*שורה\s*\d+\s*:?[*_]*\s*)?[`*_]*"
    r"(פסיקה או ממצא|פסיקה)[`*_]*(?:\s*\([^)]*\))?\s*[:—–|-]")
RULING_RE = re.compile(r"עובר|עוצר|נכשל")
EARLIER = "(מהודעה קודמת)"


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


_budget = [HASH_BUDGET]


def short_hash(path):
    """The first 8 hex digits of the file's sha256; "big" past MAX_HASHED or the call's HASH_BUDGET,
    None if unreadable."""
    try:
        size = os.path.getsize(path)
        if size > MAX_HASHED or size > _budget[0]:
            return "big"
        _budget[0] -= size
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:8]
    except OSError:
        return None


def path_at(text, listing):
    """The existing file whose path starts `text` (which starts with "/" or "~/"), or None. Folder
    and file names may contain spaces, so the path is not cut at a space: the folders are walked
    while they exist, and the file is the longest name in the last folder that the rest of the text
    starts with, followed by an end of path. A stat per folder and one listing, however long the line."""
    text = text[:MAX_PATH_CHARS]
    if text.startswith("~/"):
        text = os.path.expanduser("~") + text[1:]
    folder_end = 0
    for i, ch in enumerate(text):
        if ch == "/" and i > 0:
            if os.path.isdir(text[:i]):
                folder_end = i
            else:
                break
    folder, rest = text[:folder_end] or "/", text[folder_end + 1:]
    if folder not in listing:
        try:
            listing[folder] = os.listdir(folder)
        except OSError:
            listing[folder] = []
    best = None
    for name in listing[folder]:
        after = rest[len(name):len(name) + 2]
        ends = not after or after[0] in PATH_END or (after[0] in PATH_PUNCT and (len(after) == 1 or after[1] in PATH_END))
        if rest.startswith(name) and ends:
            if (best is None or len(name) > len(best)) and os.path.isfile(os.path.join(folder, name)):
                best = name
    return os.path.join(folder, best) if best else None


def brief_paths(text, cwd=None):
    """The existing files a brief names, in order, at most MAX_BRIEF_FILES: absolute and ~ paths
    anywhere (path_at), and a relative path inside backticks, resolved against cwd."""
    found, seen, listing = [], set(), {}

    def add(path):
        real = os.path.realpath(path)
        if real not in seen:
            seen.add(real)
            found.append(path)

    tried = 0
    for line in (text or "").splitlines():
        if len(found) >= MAX_BRIEF_FILES or tried >= MAX_CANDIDATES:
            break
        for m in PATH_START.finditer(line):
            tried += 1
            if tried > MAX_CANDIDATES:
                break
            path = path_at(line[m.start():], listing)
            if path:
                add(path)
        if cwd:
            for span in re.findall(r"`([^`]*)`", line):  # backtick pairs, in order
                rel = span.strip()
                if "/" in rel and not rel.startswith(("/", "~")) and os.path.isfile(os.path.join(cwd, rel)):
                    add(os.path.join(cwd, rel))
    return found[:MAX_BRIEF_FILES]


def text_hash(text):
    return hashlib.sha256(str(text).strip().encode("utf-8")).hexdigest()[:8]


def first_prompt(path):
    """The first user message of a transcript: for a subagent, the prompt it was called with."""
    with open(path, encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f):
            if n > 50:
                return None
            if '"user"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("type") == "user":
                c = (d.get("message") or {}).get("content")
                if isinstance(c, list):
                    c = "".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
                return c if isinstance(c, str) else None
    return None


def tagged(name, path):
    return f"{cell(name, 255)}@{short_hash(path) or 'unreadable'}"  # a whole name: review matches on it


def returned_files(tool_input):
    """[(name in the project folder, staged path)] from a device_commit_files call. The files come
    as [{devicePath, stagedPath}]; anything else in the list is skipped."""
    out = []
    for f in tool_input.get("files") or []:
        if not isinstance(f, dict):
            continue
        staged = f.get("stagedPath") or f.get("staged_path") or ""
        device = f.get("devicePath") or f.get("device_path") or staged
        if staged:
            out.append((re.split(r"[\\/]", str(device).rstrip("\\/"))[-1], str(staged)))
    return out


def session_copy(session, today):
    """This session's own ledger file in Cowork's outputs folder, or None outside Cowork."""
    outputs = os.environ.get("BUSINESS_OS_OUTPUTS") or "/mnt/user-data/outputs"
    if not session or not os.path.isdir(outputs):
        return None
    folder = os.path.join(outputs, SESSION_DIR)
    existing = sorted(glob.glob(os.path.join(glob.escape(folder), f"*-{glob.escape(session)}.md")))
    return existing[0] if existing else os.path.join(folder, f"{today}-{session}.md")


def transcript_stats(path):
    """Numbers from one transcript. One API response spans several lines; per message id the line
    with the highest output count is kept, and the content blocks are counted once each."""
    usage, models, blocks, tools = {}, {}, {}, set()
    first = last = None
    texts = collections.deque(maxlen=50)  # the agent's own recent text blocks, newest last: (message, text)
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
                    if (mid, c["text"]) not in texts:
                        texts.append((mid, c["text"]))
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
        "texts": list(texts),
    }


def verdict_line(text):
    for line in (text or "").splitlines():
        if VERDICT_RE.match(line) and RULING_RE.search(line):
            return line.strip().strip("`").strip()
    return ""


def find_verdict(texts, reply):
    """(verdict line, where from). The reviewer's final message first, then the event's reply, and
    only then an earlier message of its own — tagged, since it may be a draft it later reversed."""
    final_mid = texts[-1][0] if texts else None
    final = [t for m, t in texts if m == final_mid]
    for t in reversed(final):
        if verdict_line(t):
            return verdict_line(t), "final"
    if verdict_line(reply):
        return verdict_line(reply), "reply"
    for m, t in reversed(texts):
        if m != final_mid and verdict_line(t):
            return f"{verdict_line(t)} {EARLIER}", "earlier"
    return "", "none"


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
    verdict = ""
    numbers = describe(stats, minutes=subagent)
    if subagent:
        verdict, source = find_verdict(stats["texts"], reply)
        numbers += f" · verdict {source}"  # where the line came from — how a missing one is traced
        try:
            prompt = first_prompt(path)
        except OSError:
            prompt = None
        if prompt:
            numbers += BRIEF + text_hash(prompt)
    return who, target, verdict or "-", numbers


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
    args = sys.argv[1:]
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    caller = event.get("agent_type") or "main"
    if "--finish" in args:
        values = finish_row(event, wait=detach())
        if values[1] == MAIN_TOTAL:
            drop = main_total_of(session)
        limit = 300
    elif "--commit" in args or "--returned" in args or "--failed" in args:
        files = returned_files(tool_input)
        target = RETURNING
        if "--returned" in args:
            response = event.get("tool_response")
            target = RETURN_FAILED if isinstance(response, dict) and response.get("isError") is True else RETURNED
        elif "--failed" in args:
            target = RETURN_FAILED
        values = (caller, target, f"files: {len(files)}", ", ".join(tagged(n, p) for n, p in files) or "-")
        limit = 3000
    else:
        target = tool_input.get("subagent_type") or "general-purpose"
        prompt = str(tool_input.get("prompt") or "")
        verdict = policy(caller, target)
        if split(target)[0] and not is_head(target) and WORK_RULES not in prompt:
            verdict = f"{verdict}; flag: brief without '{WORK_RULES}'"
        note = os.environ.get("BUSINESS_OS_NOTE")
        if note:
            verdict = f"{verdict}; {note}"
        description = cell(tool_input.get("description") or "")
        if prompt.strip():
            description += BRIEF + text_hash(prompt)
        saw = brief_paths(prompt, event.get("cwd"))
        if saw:
            description += SAW + ", ".join(tagged(os.path.basename(p), p) for p in saw)
        values = (caller, target, verdict, description)
        limit = 1500

    utc = datetime.datetime.now(datetime.timezone.utc)
    folder = os.path.basename((event.get("cwd") or "").rstrip("/"))
    limits = (120, 120, 120, 120, 120, limit, 120)
    row = "| " + " | ".join(cell(v, n) for v, n in zip((utc.strftime("%Y-%m-%d %H:%M:%S"), session, *values, folder), limits)) + " |\n"
    write(os.path.join(home, ".business-os", "ledger.md"), row, drop)
    copy = session_copy(session, utc.strftime("%Y-%m-%d"))
    if copy:
        write(copy, row, drop)


if __name__ == "__main__":
    main()
