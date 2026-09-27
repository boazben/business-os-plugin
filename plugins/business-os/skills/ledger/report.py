#!/usr/bin/env python3
"""business-os — what the ledger proves about one session.

  report.py start            marks the start of a board run
  report.py run              the run's numbers since start: lines for the board, then a block that stays off it
  report.py review FILE...   did every reviewer that was given FILE check exactly this version, finish, and rule?

The session is $CLAUDE_CODE_SESSION_ID (Claude Code sets it for every Bash call), or --session ID;
with neither, the session of the ledger's last row, and the output says so.
The ledger is ~/.business-os/ledger.md, written by the business-os hooks (hooks/ledger.py). Only
names, counts, short hashes and verdict lines already in it are printed. A call is tied to its own
finish row by the hash of its brief, which both rows carry; a call with no finish row is "not
verified by the hook" and never counts as a review that ran.
"""
import argparse
import collections
import datetime
import glob
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent.parent
# The hook itself, for its row format and the hash it records. Loaded by path: this folder is
# also called "ledger".
_spec = importlib.util.spec_from_file_location("bos_ledger_hook", PLUGIN / "hooks" / "ledger.py")
assert _spec and _spec.loader, "business-os: hooks/ledger.py is missing"
ledger = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ledger)
ledger._budget[0] = float("inf")  # the hook's read budget is for a hook; this runs on request

# The agents whose finding decides whether something is ready: a brief that named a file makes
# them "given" that file. Short names, so a call without its plugin prefix still counts. Each of
# them carries shared/reviewer-contract.md, and scripts/check.py keeps this set and those files in step.
REVIEWERS = {
    "legal-verifier", "marketing-compliance-auditor", "product-compliance-auditor",
    "data-privacy-auditor", "contract-reviewer", "brand-guardian", "design-critic", "security-lead",
    "code-reviewer", "clean-code-reviewer", "qa-engineer", "build-critic", "team-qa",
}
CLOUD_ROOTS = ("/home/claude", "/mnt/user-data/outputs")
SKIP_PARTS = {"__pycache__", "node_modules", ledger.SESSION_DIR}
NOT_CALLS = {ledger.FINISHED, ledger.MAIN_TOTAL, ledger.RETURNING, ledger.RETURNED, ledger.RETURN_FAILED, "session total"}
MAX_LISTED = 20
MARKER_DAYS = 7
OFF_BOARD = "--- לא ללוח: נתיבים בענן ---"

Row = collections.namedtuple("Row", "time session caller target policy description folder")


def home():
    return Path(os.environ.get("HOME") or os.path.expanduser("~"))


def all_rows():
    path = home() / ".business-os" / "ledger.md"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[2:]
    except OSError:
        return None
    out = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 7:
            out.append(Row(*cells))
    return out


def tags(text):
    """[(name, hash)] from "a.md@1a2b3c4d, b.md@big"; a name may itself contain ", " or "@"."""
    return [(m.group(1), m.group(2)) for m in
            re.finditer(r"(?:^|, )(.+?)@([0-9a-f]{8}|big|unreadable)(?=, |$)", text)]


def saw(row):
    _, sep, listed = row.description.partition(ledger.SAW.strip() + " ")
    return tags(listed) if sep else []


def brief_of(row):
    m = re.search(re.escape(ledger.BRIEF.strip()) + r"([0-9a-f]{8})", row.description)
    return m.group(1) if m else None


def units(description):
    m = re.search(r"([\d.]+)([KM]?) units", description)
    if not m:
        return None
    return float(m.group(1)) * {"": 1, "K": 1e3, "M": 1e6}[m.group(2)]


def is_call(r):
    return r.target not in NOT_CALLS


def short_name(name):
    return name.rpartition(":")[2]


def pair(calls, finished):
    """[(call, finish or None, "brief" | "time" | None)]. A finish is tied to its call by the brief
    hash both rows carry. A finish without one (an older row, or a transcript the hook could not
    read) is used only for a call still unpaired, by agent and time — "time", not certain. Each
    finish is used once."""
    free = list(range(len(finished)))
    got: list = [None] * len(calls)
    # The same brief sent twice to one agent (a retry) can't tell its two finishes apart: "time".
    twice = collections.Counter((c.target, brief_of(c)) for c in calls if brief_of(c))
    for i, c in enumerate(calls):
        b = brief_of(c)
        j = next((j for j in free if b and brief_of(finished[j]) == b
                  and finished[j].caller == c.target and finished[j].time >= c.time), None)
        if j is not None:
            free.remove(j)
            got[i] = (finished[j], "brief" if twice[(c.target, b)] == 1 else "time")
    # A finish without a hash goes to the latest call of that agent before it that is still unpaired.
    for j in free:
        f = finished[j]
        if brief_of(f):
            continue
        i = next((i for i in range(len(calls) - 1, -1, -1) if not got[i]
                  and calls[i].target == f.caller and calls[i].time <= f.time), None)
        if i is not None:
            got[i] = (f, "time")
    return [(c, *(got[i] or (None, None))) for i, c in enumerate(calls)]


# --- versions -------------------------------------------------------------------------------

def version_key(v):
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.-]", v))


def versions():
    """business-os's own version exactly; the other plugins beside it best-effort (the highest
    version found next to it — in Claude Code's plugin cache, or in the repo)."""
    found = {}
    for pattern in (PLUGIN.parent / "*", PLUGIN.parent.parent / "*" / "*"):
        for p in glob.glob(str(pattern / ".claude-plugin" / "plugin.json")):
            try:
                meta = json.loads(Path(p).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            name, ver = meta.get("name"), str(meta.get("version") or "")
            if name and ver and (name not in found or version_key(ver) > version_key(found[name])):
                found[name] = ver
    try:
        own = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        found[own["name"]] = own["version"]
    except (OSError, ValueError, KeyError):
        found["business-os"] = "?"
    first = ["business-os"] + sorted(n for n in found if n != "business-os")
    return " · ".join(f"{n} {found[n]}" for n in first if n in found)


# --- the run's start and what was not returned ------------------------------------------------

def cloud_roots():
    env = os.environ.get("BUSINESS_OS_CLOUD_ROOTS")
    return [r for r in (env.split(":") if env else CLOUD_ROOTS) if r]


def outputs_root():
    return os.environ.get("BUSINESS_OS_OUTPUTS") or "/mnt/user-data/outputs"


def marker(session):
    return home() / ".business-os" / f"run-start-{session}"


def read_marker(session):
    """{"time": UTC row time, "main_units": the main total then, or None, "mtime": epoch} or None."""
    m = marker(session)
    try:
        data = json.loads(m.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("time"), str):
        return None
    data["mtime"] = m.stat().st_mtime
    return data


def main_transcript(session_id):
    """This conversation's own transcript, found by its session id (full, or the 8-character prefix)."""
    if not session_id:
        return None
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or home() / ".claude")
    found = [p for p in glob.glob(str(config / "projects" / "*" / f"{glob.escape(session_id)}*.jsonl"))
             if os.path.isfile(p)]
    return max(found, key=os.path.getmtime) if found else None


def main_units(rs, session_id=None):
    """(units, "now" | "previous turn") of the main conversation. From its transcript when it can
    be found: the ledger's "main total" row is written only when a turn ends, and a board run is
    often one long turn (Cowork, 27.9: no main cost at all). Else from that row."""
    path = main_transcript(session_id)
    if path:
        try:
            return ledger.transcript_stats(path)["units"], "now"
        except Exception:  # noqa: BLE001 - fall back to the row
            pass
    found = [units(r.description) for r in rs if r.target == ledger.MAIN_TOTAL]
    return (found[-1], "previous turn") if found and found[-1] is not None else (None, None)


def new_files(since):
    out = []
    for root in cloud_roots():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_PARTS]
            for name in filenames:
                path = os.path.join(dirpath, name)
                try:
                    if not name.startswith(".") and os.path.getmtime(path) > since:
                        out.append(path)
                except OSError:
                    continue
    return sorted(set(out))


def not_returned(since, rs):
    """(paths not returned, note). Returned = the content was sent by a device_commit_files call
    that the PostToolUse hook confirmed. Only when no row after a return exists at all — neither
    "returned" nor "return failed", so the after-call hooks did not run here — are the calls
    counted as attempts, and the note says so."""
    confirmed = [r for r in rs if r.target == ledger.RETURNED]
    note = ""
    after_rows = any(r.target in (ledger.RETURNED, ledger.RETURN_FAILED) for r in rs)
    if not after_rows and any(r.target == ledger.RETURNING for r in rs):
        confirmed = [r for r in rs if r.target == ledger.RETURNING]
        note = "לא אומת שההחזרות הצליחו: אין רישום אחרי אף החזרה — ייתכן שנכשלו"
    sent = [t for r in confirmed for t in tags(r.description)]
    hashes = {h for _, h in sent if h not in ("big", "unreadable")}
    names = {n for n, _ in sent}
    missing = []
    for path in new_files(since):
        h = ledger.short_hash(path)
        if h in hashes or (h in ("big", None) and os.path.basename(path) in names):
            continue
        missing.append(path)
    return missing, note


# --- commands -------------------------------------------------------------------------------

def cmd_start(session, rs, session_id=None):
    m = marker(session)
    m.parent.mkdir(parents=True, exist_ok=True)
    for old in m.parent.glob("run-start-*"):
        try:
            if time.time() - old.stat().st_mtime > MARKER_DAYS * 86400:
                old.unlink()
        except OSError:
            pass
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    units_then, source = main_units(rs or [], session_id)
    m.write_text(json.dumps({"time": now, "main_units": units_then, "main_source": source}), encoding="utf-8")
    print(f"תחילת ריצה סומנה (שיחה {session}, {now} UTC)")


def cmd_run(session, rs, session_id=None):
    start = read_marker(session)
    if start:
        rs = [r for r in rs if r.time >= start["time"]]
        span = f"מאז תחילת הריצה ({start['time']} UTC)"
    else:
        span = "כל השיחה — לא סומנה תחילת ריצה"
    print(f"ריצה · שיחה {session} · {span}")
    print(versions())

    calls = [r for r in rs if is_call(r)]
    finished = [r for r in rs if r.target == ledger.FINISHED]
    paired = pair(calls, finished)
    open_calls = collections.Counter(c.target for c, f, _ in paired if f is None)
    line = f"הפעלות {len(calls)} · סיימו {len(calls) - sum(open_calls.values())}"
    if open_calls:
        line += " · לא אומת ב-hook: " + ", ".join(
            f"{n}" + (f" ×{k}" if k > 1 else "") for n, k in sorted(open_calls.items()))
    print(line)

    agent_units = [units(r.description) for r in finished]
    known = [u for u in agent_units if u is not None]
    tokens = f"טוקנים (יחידות משוקללות): סוכנים {ledger.short(sum(known))}"
    if len(known) < len(agent_units):
        tokens += f" (בלי {len(agent_units) - len(known)} סיומים שלא נמדדו)"
    now_main, when = main_units(rs, session_id)
    if now_main is not None:
        label = "שיחה ראשית עד עכשיו" if when == "now" else "שיחה ראשית עד התור הקודם"
        # Less what it was at start — only when both numbers come from the same place.
        if start and start.get("main_source") == when and start.get("main_units") is not None:
            main = max(now_main - start["main_units"], 0)
        else:
            main = now_main
            if start:
                label += " (כל השיחה)"
        tokens += f" + {label} {ledger.short(main)} = {ledger.short(main + sum(known))}"
    else:
        tokens += " · שיחה ראשית: עוד לא נרשמה"
    print(tokens)

    if not os.path.isdir(outputs_root()):
        return
    off_board = []
    if start is None:
        print("לא הוחזרו: לא נבדק — לא סומנה תחילת ריצה (report.py start)")
    else:
        missing, note = not_returned(start["mtime"], rs)
        print(f"לא הוחזרו: {len(missing)}" + (f" ({note})" if note else "")
              + (" — " + ", ".join(os.path.basename(p) for p in missing[:MAX_LISTED]) if missing else "")
              + (f" ועוד {len(missing) - MAX_LISTED}" if len(missing) > MAX_LISTED else ""))
        off_board += [f"  {p}" for p in missing]
    failed = [t for r in rs if r.target == ledger.RETURN_FAILED for t in tags(r.description)]
    if failed:
        print("החזרה שנכשלה: " + ", ".join(n for n, _ in failed))
    copy = ledger.session_copy(session, "")
    if copy and os.path.isfile(copy):
        off_board.append(f"היומן של השיחה: {copy} → להחזיר ל-_מערכת/ledger/{os.path.basename(copy)}, "
                         "ב-device_commit_files האחרון של הריצה")
    if off_board:
        print(OFF_BOARD)
        print("\n".join(off_board))


def cmd_review(rs, files):
    calls = sorted((r for r in rs if is_call(r) and short_name(r.target) in REVIEWERS), key=lambda r: r.time)
    paired = pair(calls, [r for r in rs if r.target == ledger.FINISHED])
    for path in files:
        if not os.path.isfile(path):
            print(f"{path}: הקובץ לא נמצא — אין מה לבדוק")
            continue
        h, name = ledger.short_hash(path), os.path.basename(path)
        hashed = h not in ("big", None)
        by_reviewer = collections.OrderedDict()
        for call, fin, how in paired:
            given = [t for t in saw(call) if t[0] == name or (hashed and t[1] == h)]
            if given:
                by_reviewer.setdefault(call.target, []).append((call, fin, how, given))
        print(f"{name} @{h}")
        warnings, lines = [], []
        if not hashed:
            warnings.append("הקובץ גדול או לא נקרא — אין דרך לדעת איזו גרסה הבודקים ראו")
        if not by_reviewer:
            warnings.append("לפי היומן, אף בודק לא קיבל את הקובץ הזה בתדריך")
        for reviewer, items in by_reviewer.items():
            for call, fin, how, given in items:
                seen = ("ראה את הגרסה הזו" if hashed and any(t[1] == h for t in given)
                        else "ראה גרסה אחרת (" + ", ".join(f"@{t[1]}" for t in given) + ")")
                done = "הסיום לא נרשם (לא אומת ב-hook)" if fin is None else f"סיים · {fin.policy}"
                lines.append(f"  {reviewer} — {seen} · {done}" + (" · שויך לפי זמן" if how == "time" else ""))
            if not hashed:
                continue
            # A review, a fix, and a review of the fix is the normal flow: what counts is the
            # reviewer's latest call on this file, and a finished call on this exact version.
            if not any(t[1] == h for t in items[-1][3]):
                warnings.append(f"{reviewer} לא בדק את הגרסה הסופית")
                continue
            on_final = [it for it in items if any(t[1] == h for t in it[3]) and it[1] is not None]
            if not on_final:
                warnings.append(f"{reviewer} הופעל על הגרסה הסופית, הסיום שלו לא נרשם")
                continue
            call, fin, how, _ = on_final[-1]
            if fin.policy == "-":
                warnings.append(f"{reviewer} סיים בלי שורת פסיקה")
            elif fin.policy.endswith(ledger.EARLIER):
                warnings.append(f"{reviewer}: שורת הפסיקה מהודעה קודמת שלו, לא מהתשובה הסופית — לא ודאי שזו ההכרעה")
            elif how == "time":
                warnings.append(f"{reviewer}: הסיום שויך לפי זמן, לא לפי התדריך — לא ודאי שהוא של הבדיקה הזו")
        for w in dict.fromkeys(warnings):
            print(f"  ⚠️ {w}")
        if not warnings:
            print("  ✓ כל בודק שקיבל אותו בדק את הגרסה הזו, סיים וכתב שורת פסיקה")
        for line in lines:
            print(line)


def main():
    p = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    p.add_argument("command", choices=("start", "run", "review"))
    p.add_argument("files", nargs="*")
    p.add_argument("--session")
    a = p.parse_args()
    everything = all_rows()
    session_id = a.session or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    session = session_id[:8]
    if not session and everything:
        session = session_id = everything[-1].session
        print(f"(שיחה {session} לפי השורה האחרונה ביומן — CLAUDE_CODE_SESSION_ID לא מוגדר)")
    if not session:
        sys.exit("אין מזהה שיחה: CLAUDE_CODE_SESSION_ID ריק והיומן ריק. להריץ עם --session <8 התווים מעמודת session ביומן>.")
    rs = [r for r in everything or [] if r.session == session]
    if a.command == "start":
        return cmd_start(session, rs, session_id)
    if everything is None:
        print("אין יומן (~/.business-os/ledger.md) — אין דרך לאמת מה רץ")
        return
    if a.command == "run":
        return cmd_run(session, rs, session_id)
    if not a.files:
        sys.exit("review צריך לפחות קובץ אחד")
    return cmd_review(rs, a.files)


if __name__ == "__main__":
    main()
