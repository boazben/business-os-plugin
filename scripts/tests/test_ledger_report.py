"""plugins/business-os/skills/ledger/report.py: what the ledger proves about one session.

Run: python3 scripts/tests/test_ledger_report.py
Call and return rows are written by the real hook (hooks/ledger.py) from hook events; finish and
main-total rows are written as the hook writes them, carrying the brief hash the hook takes from
the agent's transcript. The script runs as the model runs it, with the session in
CLAUDE_CODE_SESSION_ID. When a report comes out wrong, add the case here first.
"""
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins/business-os"
REPORT = PLUGIN / "skills/ledger/report.py"
HOOK = PLUGIN / "hooks/ledger.py"
VERSION = json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
spec = importlib.util.spec_from_file_location("board_gate", PLUGIN / "hooks/board-gate.py")
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
failures = []


def check(name, cond, got=None):
    if not cond:
        failures.append(name)
    print(("ok   " if cond else "FAIL ") + name + ("" if cond or got is None else f"\n     got: {got!r}"))


def sha8(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()[:8]


def main(tmp):
    home, out, cloud, v = tmp / "home", tmp / "outputs", tmp / "cloud", tmp / "v"
    for d in (home, out, cloud, v / "שיווק"):
        d.mkdir(parents=True)
    env = dict(os.environ, HOME=str(home), BUSINESS_OS_OUTPUTS=str(out),
               BUSINESS_OS_CLOUD_ROOTS=f"{cloud}:{out}", CLAUDE_CODE_SESSION_ID="sess1234-full-id")
    log = home / ".business-os" / "ledger.md"

    def report(*args, **extra):
        p = subprocess.run([sys.executable, str(REPORT), *args], capture_output=True, text=True,
                           env=dict(env, **extra))
        return p.returncode, p.stdout + p.stderr

    def hook(event, *flags, session="sess1234-full-id"):
        subprocess.run([sys.executable, str(HOOK), *flags], check=True, env=env,
                       input=json.dumps(dict(event, session_id=session), ensure_ascii=False).encode())

    n = [0]

    def called(caller, target, prompt, session="sess1234-full-id"):
        """A real call row; returns the brief hash its finish row will carry."""
        n[0] += 1
        prompt = f"{prompt}\n(קריאה {n[0]})"  # two calls never share a brief by accident
        hook({"cwd": str(v), "tool_name": "Agent", "agent_type": caller,
              "tool_input": {"subagent_type": target, "description": "d", "prompt": prompt}}, session=session)
        return hashlib.sha256(prompt.strip().encode()).hexdigest()[:8]

    def finished(who, verdict, brief=None, units="12K", session="sess1234"):
        time.sleep(1.05)  # rows are compared by time, to the second
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        tail = f" · brief@{brief}" if brief else ""
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"| {now} | {session} | {who} | finished | {verdict} | opus-5-5 · 3 requests · 2 tools"
                    f" · 1m · in 10 · cache write 1K · cache read 5K · out 200 · {units} units{tail} | v |\n")

    BG, LV = "marketing-os:brand-guardian", "legal-os:legal-verifier"
    PASS, STOP = "פסיקה או ממצא: עובר (עובר)", "פסיקה או ממצא: 2 טענות בעייתיות (עוצר)"

    print("== review: review, fix, review again — the normal flow")
    ad = v / "שיווק" / "מודעה 01.md"
    ad.write_text("גרסה 1", encoding="utf-8")
    v1 = sha8(ad)
    called("main", "marketing-os:marketing-lead", f"המודעה: {ad}")
    b = called("marketing-os:marketing-lead", BG, f"תבדוק `{ad}`")
    finished(BG, STOP, b)
    b = called("marketing-os:marketing-lead", "marketing-os:campaign-copywriter", f"תתקן את {ad}")
    finished("marketing-os:campaign-copywriter", "-", b)
    ad.write_text("גרסה 2", encoding="utf-8")
    v2 = sha8(ad)
    code, text = report("review", str(ad))
    check("exit 0", code == 0, text)
    check("the file and its hash first", text.startswith(f"מודעה 01.md @{v2}\n"), text)
    check("fixed after the review: the reviewer did not check the final version",
          f"⚠️ {BG} לא בדק את הגרסה הסופית" in text, text)
    check("...the old review is listed with the version it saw and its verdict",
          f"{BG} — ראה גרסה אחרת (@{v1}) · סיים · {STOP}" in text, text)
    check("the writer and the head are not reviewers", "copywriter" not in text and "marketing-lead" not in text, text)
    b = called("marketing-os:marketing-lead", BG, f"סבב ביקורת 2/3: {ad}")
    finished(BG, PASS, b)
    code, text = report("review", str(ad))
    check("re-reviewed on the final version: no warning, one ✓",
          "⚠️" not in text and "✓ כל בודק שקיבל אותו בדק את הגרסה הזו, סיים וכתב שורת פסיקה" in text, text)
    check("...both rounds listed", f"ראה גרסה אחרת (@{v1})" in text and f"{BG} — ראה את הגרסה הזו · סיים · {PASS}" in text, text)
    copy = out / "final-ad.md"  # the cloud copy about to be returned: same content, another name
    copy.write_text("גרסה 2", encoding="utf-8")
    code, text = report("review", str(copy))
    check("matched by content, not by name", f"{BG} — ראה את הגרסה הזו" in text and "⚠️" not in text, text)

    print("== review: a finish belongs to its own call")
    final, other = v / "final.md", v / "other.md"
    final.write_text("final", encoding="utf-8")
    other.write_text("other", encoding="utf-8")
    called("legal-os:legal-lead", LV, f"{final}")  # never finishes (Esc, error, session closed)
    b = called("legal-os:legal-lead", LV, f"{other}")
    finished(LV, PASS, b)
    code, text = report("review", str(final))
    check("an unfinished review does not borrow a later review's finish",
          f"⚠️ {LV} הופעל על הגרסה הסופית, הסיום שלו לא נרשם" in text and "✓" not in text, text)
    code, text = report("review", str(other))
    check("...and the review that finished keeps its own verdict", f"{LV} — ראה את הגרסה הזו · סיים · {PASS}" in text and "⚠️" not in text, text)
    a_file, b_file = v / "a.md", v / "b.md"
    a_file.write_text("a", encoding="utf-8")
    b_file.write_text("b", encoding="utf-8")
    ba = called("marketing-os:marketing-lead", BG, f"{a_file}")
    bb = called("marketing-os:marketing-lead", BG, f"{b_file}")
    finished(BG, STOP, bb)  # parallel: B finishes first
    finished(BG, PASS, ba)
    _, ta = report("review", str(a_file))
    _, tb = report("review", str(b_file))
    check("parallel reviews: each file gets its own verdict", f"סיים · {PASS}" in ta and f"סיים · {STOP}" in tb, (ta, tb))

    print("== review: what is not a pass")
    quiet = v / "quiet.md"
    quiet.write_text("q", encoding="utf-8")
    b = called("legal-os:legal-lead", LV, f"{quiet}")
    finished(LV, "-", b)
    _, text = report("review", str(quiet))
    check("finished without a verdict line: warned, no ✓", f"⚠️ {LV} סיים בלי שורת פסיקה" in text and "✓" not in text, text)
    old = v / "old-style.md"
    old.write_text("o", encoding="utf-8")
    called("legal-os:legal-lead", LV, f"{old}")
    finished(LV, PASS)  # a finish row with no brief hash (older hook, unreadable transcript)
    _, text = report("review", str(old))
    check("a finish tied only by time is marked uncertain", "שויך לפי זמן" in text and "לא ודאי" in text and "✓" not in text, text)
    locked = v / "locked.md"
    locked.write_text("l", encoding="utf-8")
    b = called("legal-os:legal-lead", LV, f"{locked}")
    finished(LV, PASS, b)
    os.chmod(locked, 0)
    _, text = report("review", str(locked))
    os.chmod(locked, 0o600)
    check("a file that can't be read: no ✓", "⚠️ הקובץ גדול או לא נקרא" in text and "✓" not in text, text)
    lone = v / "lone.md"
    lone.write_text("never reviewed", encoding="utf-8")
    _, text = report("review", str(lone))
    check("no reviewer was given the file: says so", "⚠️ לפי היומן, אף בודק לא קיבל את הקובץ הזה בתדריך" in text, text)
    called("main", BG, f"{lone}", session="other999-session")
    _, text = report("review", str(lone))
    check("another session's call does not count", "אף בודק לא קיבל" in text, text)
    _, text = report("review", str(v / "missing.md"))
    check("a missing file: says so", "הקובץ לא נמצא" in text, text)

    print("== run: only what happened since start")
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"| {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} | sess1234 | main | main total | - | opus · 9 requests · 1.0M units | v |\n")
    _, text = report("run")
    check("no start: the whole session, said so", "כל השיחה — לא סומנה תחילת ריצה" in text, text)
    check("...and 'not returned' is not claimed", "לא הוחזרו: לא נבדק" in text, text)
    time.sleep(1.05)
    before = cloud / "before.md"
    before.write_text("before the run", encoding="utf-8")
    time.sleep(1.05)
    code, text = report("start")
    check("start", code == 0 and "תחילת ריצה סומנה" in text, text)
    time.sleep(1.05)
    b = called("main", "legal-os:legal-lead", "משימה BOS-55")
    b2 = called("legal-os:legal-lead", LV, "ביקורת")
    finished(LV, PASS, b2, units="20K")
    called("legal-os:legal-lead", "legal-os:legal-drafter", "ניסוח")
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"| {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} | sess1234 | main | main total | - | opus · 20 requests · 1.3M units | v |\n")
    (cloud / "draft.md").write_text("draft", encoding="utf-8")
    (cloud / "sub").mkdir()
    (cloud / "sub" / "image.jpg").write_bytes(b"\xff\xd8img")
    (cloud / ".cache").mkdir()
    (cloud / ".cache" / "x").write_text("hidden", encoding="utf-8")
    (cloud / "__pycache__").mkdir()
    (cloud / "__pycache__" / "m.pyc").write_bytes(b"pyc")
    (out / "sent.md").write_text("sent", encoding="utf-8")
    (out / "tried.md").write_text("tried", encoding="utf-8")
    commit = {"cwd": "/home/claude", "tool_name": "mcp__remote-devices__device_commit_files"}
    hook(dict(commit, tool_input={"files": [{"devicePath": "C:\\v\\sent.md", "stagedPath": str(out / "sent.md")}]}), "--commit")
    hook(dict(commit, tool_input={"files": [{"devicePath": "C:\\v\\sent.md", "stagedPath": str(out / "sent.md")}]},
              tool_response={"content": [{"type": "text", "text": "1 file written"}]}), "--returned")
    hook(dict(commit, tool_input={"files": [{"devicePath": "C:\\v\\tried.md", "stagedPath": str(out / "tried.md")}]}), "--commit")
    hook(dict(commit, tool_input={"files": [{"devicePath": "C:\\v\\tried.md", "stagedPath": str(out / "tried.md")}]},
              error="device is offline"), "--failed")
    code, text = report("run")
    board, _, off = text.partition("--- לא ללוח: נתיבים בענן ---")
    lines = board.splitlines()
    check("since start", "מאז תחילת הריצה (" in lines[0], text)
    check("versions, business-os exact", lines[1].startswith(f"business-os {VERSION}"), text)
    check("calls since start only, and which did not finish",
          "הפעלות 3 · סיימו 1 · לא אומת ב-hook: legal-os:legal-drafter, legal-os:legal-lead" in text, text)
    check("tokens: the run's agents, and the main conversation's growth since start",
          "סוכנים 20K + שיחה ראשית עד התור הקודם 300K = 320K" in text, text)
    check("not returned: new since start, content never confirmed sent — by name on the board",
          "לא הוחזרו: 3 — draft.md, image.jpg, tried.md" in text, text)
    check("a failed return is named", "החזרה שנכשלה: tried.md" in text, text)
    check("...the full cloud paths only below the off-board line",
          f"  {cloud / 'draft.md'}" in off and str(cloud) not in board and str(out) not in board, text)
    check("...not: older than start, hidden, __pycache__, the session's ledger copy",
          not any(x in text for x in ("before.md", ".cache", "m.pyc", "business-os-ledger/2026-01")), text)
    check("the session's ledger copy and where it goes, off the board",
          "היומן של השיחה: " in off and "→ להחזיר ל-_מערכת/ledger/" in off and "-sess1234.md" in off, off)
    real = board.replace(str(tmp), "/home/claude")  # as it reads in Cowork, where the roots are real
    check("the board lines pass the board gate", gate.verdict("mcp__x__notion-update-page", {"page_id": "p", "new_str": real})[0] != "deny", real)
    check("...the off-board block would not", gate.verdict("mcp__x__notion-update-page",
          {"page_id": "p", "new_str": off.replace(str(tmp), "/home/claude")})[0] == "deny")
    (cloud / "sub" / "copy-of-sent.md").write_text("sent", encoding="utf-8")
    _, text = report("run")
    check("a cloud file with the same content as a confirmed one counts as returned", "copy-of-sent" not in text, text)
    _, text = report("run", BUSINESS_OS_OUTPUTS=str(tmp / "nowhere"))
    check("not Cowork (no outputs folder): no 'not returned' line, no off-board block",
          "לא הוחזרו" not in text and "לא ללוח" not in text, text)

    print("== run: returns the PostToolUse hook never confirmed")
    s2 = dict(env, CLAUDE_CODE_SESSION_ID="sess5678")
    subprocess.run([sys.executable, str(REPORT), "start"], env=s2, check=True, capture_output=True)
    time.sleep(1.05)
    (out / "attempt.md").write_text("attempt", encoding="utf-8")
    hook(dict(commit, tool_input={"files": [{"devicePath": "C:\\v\\attempt.md", "stagedPath": str(out / "attempt.md")}]}),
         "--commit", session="sess5678")
    text = subprocess.run([sys.executable, str(REPORT), "run"], env=s2, capture_output=True, text=True).stdout
    check("no confirmation at all in the session: attempts count, and the line says it is unverified",
          "attempt.md" not in text.split("לא הוחזרו:")[1].split("\n")[0].split("—")[-1]
          and "לא אומת שההחזרות הצליחו" in text, text)

    print("== run: every return failed")
    s3 = dict(env, CLAUDE_CODE_SESSION_ID="sess9999")
    subprocess.run([sys.executable, str(REPORT), "start"], env=s3, check=True, capture_output=True)
    time.sleep(1.05)
    (out / "never.md").write_text("never arrived", encoding="utf-8")
    ev = dict(commit, tool_input={"files": [{"devicePath": "C:\\v\\never.md", "stagedPath": str(out / "never.md")}]})
    hook(ev, "--commit", session="sess9999")
    hook(dict(ev, error="offline"), "--failed", session="sess9999")
    text = subprocess.run([sys.executable, str(REPORT), "run"], env=s3, capture_output=True, text=True).stdout
    check("only failed returns: the file is not returned, and named as failed",
          "לא הוחזרו: 1 — never.md" in text and "החזרה שנכשלה: never.md" in text, text)

    print("== edges")
    same = v / "same.md"
    same.write_text("v1", encoding="utf-8")
    c1 = called("marketing-os:marketing-lead", BG, f"{same}")
    same.write_text("v2", encoding="utf-8")
    subprocess.run([sys.executable, str(HOOK)], check=True, env=env, input=json.dumps(
        {"session_id": "sess1234-full-id", "cwd": str(v), "tool_name": "Agent", "agent_type": "marketing-os:marketing-lead",
         "tool_input": {"subagent_type": BG, "description": "d", "prompt": f"{same}\n(קריאה {n[0]})"}},
        ensure_ascii=False).encode())  # the very same brief again (a retry), after the file changed
    finished(BG, PASS, c1)
    finished(BG, STOP, c1)
    _, text = report("review", str(same))
    check("the same brief twice: which finish is whose is not known, so no ✓", "✓" not in text and "לא ודאי" in text, text)
    marker = home / ".business-os" / "run-start-sess7777"
    marker.write_text("", encoding="utf-8")  # what the first version of start wrote
    code, text = report("run", CLAUDE_CODE_SESSION_ID="sess7777")
    check("an old empty marker: no crash, the whole session", code == 0 and "לא סומנה תחילת ריצה" in text, text)
    code, text = report("run", CLAUDE_CODE_SESSION_ID="")
    check("no session id: the ledger's last row, said so", "לפי השורה האחרונה ביומן" in text, text)
    code, text = report("run", CLAUDE_CODE_SESSION_ID="", HOME=str(tmp / "empty-home"))
    check("no session id and no ledger: fails with a way out", code != 0 and "--session" in text, text)
    code, text = report("run", "--session", "other999")
    check("--session picks another session", "שיחה other999" in text and "הפעלות 1 · סיימו 0" in text, text)
    code, text = report("run", HOME=str(tmp / "empty-home"))
    check("no ledger: says there is no way to verify", "אין יומן" in text, text)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        main(pathlib.Path(d))
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)
