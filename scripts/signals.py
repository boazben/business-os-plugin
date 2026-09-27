#!/usr/bin/env python3
"""BOS-49 phase 2: the mechanical signals that say when a deferred component is needed.

Phase 2 of the plan (~/.business-os/research/bos49-2026-09-24/plan_final.json, lean.json) builds
nothing on a hunch: each component waits for evidence. This script computes that evidence at
session start in this repo and prints one line when something lit — with the number that lit it
— so nothing depends on anyone remembering. The founder decides each component separately.

  C20  words of a reviewer, of the legal department or of the persona's gates were removed or
       reworded since the last decision (git; pure additions don't count, but an added negation
       like "לא" does, and so does a deleted reviewer)
       -> a replay set before such changes
  C16  the same legal research ran twice, in different runs, within 30 days (ledgers)
       -> keep verified research notes
  C1   department agents run a lot in Claude Code (local ledger) -> agents without the CEO persona
  C7   the persona changed on more than 2 days within 30 (git) -> each change is a Cowork paste
  C12  files a run set aside in a venture's _מערכת/incoming since the last decision
       -> venture_check merges

Usage:
  signals.py                 one line if anything lit, else nothing (exit 0)
  signals.py --hook          the same as SessionStart JSON: a message for the founder, context for Claude
  signals.py --status        every signal with its number, lit or not
  signals.py --ack ID        the founder decided on ID (built it, or "not now"): count from now on
  signals.py --venture PATH  read this venture folder too (its returned ledgers and _מערכת/incoming)

State (local, not in this public repo): ~/.business-os/signals.json — when each signal was last
decided (time, and for C20 the commit), and the venture folders. Created on first run with every
signal decided "now", so the alarm counts from when it was installed. A machine without
~/.business-os (anyone else who opens this repo) gets nothing. A state file that doesn't parse is
never rewritten: the line says so.
"""
import collections
import datetime
import glob
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(os.environ.get("BUSINESS_OS_SIGNALS_ROOT") or pathlib.Path(__file__).resolve().parent.parent)
PERSONA = "CEO-PERSONA.md"
GATE_HEADINGS = ("### שער האישור", "### שער משפטי")
REPORT = "plugins/business-os/skills/ledger/report.py"
RESEARCHER = "regulatory-researcher"
C1_CALLS = 30          # department-agent calls in Claude Code within 30 days
C7_DAYS = 2            # more than this many days with a persona change within 30 days
SAME_RESEARCH = 0.5    # overlap of two research descriptions' topic words that counts as the same topic
SHARED_WORDS = 2       # ...and at least this many topic words in common
# Words every legal research description has; they say nothing about the topic.
GENERIC = {
    "research", "researcher", "law", "laws", "legal", "israel", "israeli", "rules", "rule", "regulation",
    "regulations", "requirements", "check", "for", "the", "and", "task", "bos",
    "policy", "website", "site", "online",
    "מחקר", "חוק", "חוקי", "דין", "דיני", "ישראל", "תקנות", "הגנת", "בדיקת", "בדיקה", "דרישות", "חובות", "משימה",
    "אתר", "מדיניות",
}
# Words that weaken or invert a rule when added ("יכול" -> "לא יכול", "[חוסם]" -> "[לא חוסם]"):
# adding one counts as a removal.
NEGATIONS = {"לא", "אל", "אין", "בלי", "אלא", "חוץ", "רק", "not", "no", "never", "unless", "except", "without", "only"}
HEBREW_PREFIX = "והבלמשכ"
WINDOW = datetime.timedelta(days=30)
IDS = ("C20", "C16", "C1", "C7", "C12")
NOT_CALLS = {"finished", "main total", "session total", "returning", "returned", "return failed"}


class BadState(Exception):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def stamp(t):
    return t.strftime("%Y-%m-%d %H:%M:%S")


def epoch(s):
    return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp()


def git(*args):
    r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def business_os_dir():
    return pathlib.Path(os.environ.get("HOME") or os.path.expanduser("~")) / ".business-os"


def state_path():
    return business_os_dir() / "signals.json"


def load_state():
    """The state, created with every signal decided now when there is none. A file that doesn't
    parse raises BadState and is left as it is: rebuilding it would wipe the venture list and
    C20's base without anyone noticing."""
    path = state_path()
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise BadState(f"{path} לא תקין ({type(e).__name__}) — לא נקרא ולא שונה") from None
        if not isinstance(state, dict):
            raise BadState(f"{path} לא תקין — לא נקרא ולא שונה")
    else:
        state = {}
    changed = False
    if not isinstance(state.get("ventures"), list):
        state["ventures"], changed = [], True
    if not isinstance(state.get("ack"), dict):
        state["ack"], changed = {}, True
    head = (git("rev-parse", "HEAD") or "").strip()
    for i in IDS:
        if not isinstance(state["ack"].get(i), dict) or not state["ack"][i].get("time"):
            state["ack"][i], changed = {"time": stamp(now()), "commit": head}, True
    if changed:
        save_state(state)
    return state


def save_state(state):
    """Atomically, so two sessions starting at once can't leave half a file."""
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".signals-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def since(state, i):
    """Count from the later of the last decision and 30 days ago."""
    return max(state["ack"][i]["time"], stamp(now() - WINDOW))


# --- the ledgers ------------------------------------------------------------------------------

def ledger_rows(state):
    """Rows of the local ledger and of every returned Cowork ledger, marked with where they ran.
    The same row read from two files counts once; two identical calls in one file stay two."""
    sources = [(business_os_dir() / "ledger.md", "local")]
    for v in state.get("ventures") or []:
        sources += [(pathlib.Path(p), "cowork")
                    for p in sorted(glob.glob(os.path.join(glob.escape(v), "_מערכת", "ledger", "*.md")))]
    first_file, out = {}, []
    for path, where in sources:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[2:]
        except OSError:
            continue
        for line in lines:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) != 7:
                continue
            if first_file.setdefault((tuple(cells), where), path) != path:
                continue
            out.append((where, cells))
    return out


def topic_words(description):
    """The words that say what a research is about: without the row's suffix, generic words and
    numbers; מע"מ read as one word; one Hebrew prefix letter off a longer word (לעוסק -> עוסק)."""
    text = re.sub(r" · (?:brief@|saw ).*$", "", description)
    text = re.sub(r"(?<=\w)[\"״'׳](?=\w)", "", text.lower())
    out = set()
    for w in re.findall(r"\w+", text):
        if w in GENERIC:  # before the prefix comes off: "מחקר" is generic, "חקר" would not be
            continue
        if len(w) > 3 and w[0] in HEBREW_PREFIX and "\u05d0" <= w[1] <= "\u05ea":
            w = w[1:]
        if len(w) > 2 and w not in GENERIC and not w.isdigit():
            out.add(w)
    return out


# --- the signals ------------------------------------------------------------------------------

def reviewer_names(commit):
    m = re.search(r"^REVIEWERS = \{(.*?)\}", git("show", f"{commit}:{REPORT}") or "", re.S | re.M)
    return set(re.findall(r'"([^"]+)"', m.group(1) or "")) if m else set()


PUNCT = ".,;:!?\"'`*()[]—–-"


def bare(words):
    return collections.Counter(w.strip(PUNCT) for w in words if w.strip(PUNCT))


def removed_words(diff):
    """Words one file's `git diff --word-diff=porcelain` removes and doesn't put back — its "-"
    runs less its "+" runs, punctuation aside, so words added before a sentence's period (which
    moves the period) remove nothing and a reworded word does — plus every added negation, which
    weakens a rule without removing a word."""
    lines = diff.splitlines()
    first_hunk = next((i for i, l in enumerate(lines) if l.startswith("@@")), len(lines))
    body = lines[first_hunk:]  # the file headers (--- a/…, +++ b/…) come before it
    gone = bare(w for l in body if l.startswith("-") for w in l[1:].split())
    came = bare(w for l in body if l.startswith("+") for w in l[1:].split())
    return sum((gone - came).values()) + sum(n for w, n in (came - gone).items() if w.lower() in NEGATIONS)


def gate_words(text):
    out, keep = [], False
    for l in (text or "").splitlines():
        if l.startswith("#"):
            keep = l.strip() in GATE_HEADINGS
        elif keep:
            out += l.split()
    return bare(out)


def c20(state):
    """Words removed or reworded since the last decision — a pure addition removes nothing — in
    reviewers (named at the base or now, so a deleted one counts), the legal department's agents,
    the shared sections, and the persona's approval and legal gates."""
    base = state["ack"]["C20"].get("commit")
    if not base or git("cat-file", "-e", base + "^{commit}") is None:
        return None, "אין נקודת בסיס ב-git — לא נבדק"
    names = reviewer_names(base) | reviewer_names("HEAD")
    specs = ["plugins/legal-os/agents/*.md", "plugins/business-os/shared/*.md"] + [f"plugins/*/agents/{n}.md" for n in sorted(names)]
    counts = collections.Counter()
    # --no-color and --no-ext-diff: a colour setting or a diff tool in the user's git config would hide the markers.
    diff = git("diff", "--no-color", "--no-ext-diff", "--word-diff=porcelain", f"{base}..HEAD", "--", *specs) or ""
    for part in re.split(r"(?m)^diff --git ", diff)[1:]:
        n = removed_words(part)
        if n:
            counts[pathlib.Path(part.split("\n", 1)[0].split(" b/")[-1]).stem] += n
    old, new = gate_words(git("show", f"{base}:{PERSONA}")), gate_words(git("show", f"HEAD:{PERSONA}"))
    gates = sum((old - new).values()) + sum(n for w, n in (new - old).items() if w.lower() in NEGATIONS)
    if gates:
        counts["שערי הפרסונה"] = gates
    total = sum(counts.values())
    if not total:
        return False, "אין מחיקות או ניסוח מחדש"
    return True, f"{total} מילים נמחקו או נוסחו מחדש ({', '.join(counts)})"


def c16(state, rows):
    """The same legal research in two different runs within 30 days: two regulatory-researcher
    calls, from different sessions, whose descriptions share enough topic words."""
    t = since(state, "C16")
    calls = [c for _, c in rows if c[0] >= t and c[3].endswith(RESEARCHER)]
    pairs = []
    for i, a in enumerate(calls):
        for b in calls[i + 1:]:
            if a[1] == b[1]:
                continue
            wa, wb = topic_words(a[5]), topic_words(b[5])
            shared = wa & wb
            if len(shared) >= SHARED_WORDS and len(shared) / len(wa | wb) >= SAME_RESEARCH:
                pairs.append((a, b))
    if pairs:
        a, b = pairs[0]
        topic = re.sub(r" · .*$", "", a[5])[:40]
        return True, f"{len(pairs)} זוגות של אותו מחקר בריצות שונות ({a[0][:10]} ו-{b[0][:10]}: \"{topic}\")"
    return False, f"{len(calls)} הפעלות מחקר משפטי, בלי אותו נושא פעמיים"


def c1(state, rows):
    """Department agents called in Claude Code (the local ledger) within 30 days."""
    t = since(state, "C1")
    n = sum(1 for where, c in rows if where == "local" and c[0] >= t and c[3] not in NOT_CALLS and ":" in c[3])
    return n >= C1_CALLS, f"{n} הפעלות של סוכני מחלקה ב-Claude Code ב-30 יום (סף {C1_CALLS})"


def c7(state):
    """Days with a persona change within 30 days since the last decision: each one needs a Cowork paste."""
    t = since(state, "C7")
    days = sorted(set((git("log", f"--since={t} +0000", "--format=%ad", "--date=short", "--", PERSONA) or "").split()))
    return len(days) > C7_DAYS, f"הפרסונה השתנתה ב-{len(days)} ימים ב-30 יום (סף {C7_DAYS}), וכל שינוי הוא הדבקה ב-Cowork"


def c12(state):
    """Files a run set aside in a venture's _מערכת/incoming since the last decision."""
    t = epoch(state["ack"]["C12"]["time"]) + 1  # the decision's whole second: it is stored to the second
    files = []
    for v in state.get("ventures") or []:
        base = pathlib.Path(v) / "_מערכת" / "incoming"
        if base.is_dir():
            files += [p for p in base.rglob("*") if p.is_file() and p.stat().st_mtime >= t]
    return bool(files), f"{len(files)} קבצים חדשים מחכים למיזוג ב-_מערכת/incoming"


def compute(state):
    rows = ledger_rows(state)
    out = []
    for i, fn in (("C20", lambda: c20(state)), ("C16", lambda: c16(state, rows)), ("C1", lambda: c1(state, rows)),
                  ("C7", lambda: c7(state)), ("C12", lambda: c12(state))):
        try:
            lit, text = fn()
        except Exception as e:  # noqa: BLE001 - one broken signal must not hide the others
            lit, text = None, f"לא חושב ({type(e).__name__})"
        out.append((i, lit, text))
    return out


def line(results):
    lit = [f"{i}: {text}" for i, l, text in results if l]
    if not lit:
        return ""
    return ("🔔 BOS-49 שלב 2 — נדלק: " + " · ".join(lit)
            + ". לכל אחד: לבנות, או \"לא עכשיו\" (python3 scripts/signals.py --ack <ID>).")


def say(text, hook):
    if hook:
        print(json.dumps({"systemMessage": text, "hookSpecificOutput": {"hookEventName": "SessionStart",
              "additionalContext": text + " — תגיד לבעז בשורה אחת, לפני כל דבר אחר."}}, ensure_ascii=False))
    else:
        print(text)


def main(argv):
    hook = argv[:1] == ["--hook"]
    known = (len(argv) <= 1 and argv[:1] in ([], ["--hook"], ["--status"])) or (
        len(argv) == 2 and (argv[0] == "--venture" or (argv[0] == "--ack" and argv[1] in IDS)))
    if not known:
        print(__doc__, file=sys.stderr)
        return 2
    if argv[:1] in ([], ["--hook"]) and not business_os_dir().is_dir():
        return 0  # not a business-os machine: someone else opened this public repo
    try:
        state = load_state()
    except BadState as e:
        say(f"⚠️ signals: {e}", hook)
        return 0
    if argv[:1] == ["--ack"]:
        state["ack"][argv[1]] = {"time": stamp(now()), "commit": (git("rev-parse", "HEAD") or "").strip()}
        save_state(state)
        print(f"{argv[1]}: נספר מעכשיו ({state['ack'][argv[1]]['time']} UTC)")
        return 0
    if argv[:1] == ["--venture"]:
        path = os.path.abspath(argv[1])
        if not os.path.isdir(path):
            print(f"אין תיקייה כזו: {path}", file=sys.stderr)
            return 1
        if path not in state["ventures"]:
            state["ventures"].append(path)
            save_state(state)
        print(f"תיקיות מיזם: {len(state['ventures'])}")
        return 0
    results = compute(state)
    if argv[:1] == ["--status"]:
        for i, lit, text in results:
            print(f"{'🔔' if lit else ('?' if lit is None else '·')} {i}: {text}")
        if not state["ventures"]:
            print("(אין תיקיות מיזם — C16 ו-C12 רואים רק את היומן המקומי. להוסיף: --venture <נתיב>)")
        return 0
    text = line(results)
    if text:
        say(text, hook)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:  # noqa: BLE001 - never break a session start
        print(f"signals: {type(e).__name__}", file=sys.stderr)
        sys.exit(0)
