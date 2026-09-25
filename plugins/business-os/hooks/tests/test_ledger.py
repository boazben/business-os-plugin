"""The rows the business-os ledger writes: agent calls, and agents that finished.

Run: python3 plugins/business-os/hooks/tests/test_ledger.py
Finish rows are written by the exact SubagentStop / Stop commands in hooks.json, fed a hook event
and a transcript built here. The hook returns at once and a background child writes the row once
the transcript holds still, so these tests wait for the ledger to change. A finish row carries
numbers and one verdict line — never other reply text, never a field's value — and the hook never
blocks or prints. When a row comes out wrong, add the case here before fixing it.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent.parent
HOOKS = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
failures = []


def command(event):
    return HOOKS[event][0]["hooks"][0]["command"]


def run(cmd, event, raw=None, **extra_env):
    """(exit code, stdout) of a hook command, run the way Claude Code runs it."""
    data = raw if raw is not None else json.dumps(event, ensure_ascii=False)
    env = dict(os.environ, HOME=HOME, CLAUDE_PLUGIN_ROOT=str(PLUGIN), **extra_env)
    p = subprocess.run(["sh", "-c", cmd], input=data.encode(), capture_output=True, env=env)
    return p.returncode, p.stdout.decode()


def finish(cmd, event):
    """run(), then wait for the background child's row (the ledger changes)."""
    log = Path(HOME) / ".business-os" / "ledger.md"
    before = log.read_text(encoding="utf-8") if log.exists() else ""
    got = run(cmd, event)
    end = time.monotonic() + 10
    while time.monotonic() < end:
        if log.exists() and log.read_text(encoding="utf-8") != before:
            time.sleep(0.1)
            break
        time.sleep(0.05)
    return got


def rows():
    log = Path(HOME) / ".business-os" / "ledger.md"
    if not log.exists():
        return []
    return [[c.strip() for c in l.strip().strip("|").split("|")] for l in log.read_text(encoding="utf-8").splitlines()[2:]]


def case(name, want, got):
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {got!r}, want {want!r}"))
    if not ok:
        failures.append(name)


def line(msg_id, ts, content, usage, model="claude-opus-5-5"):
    return json.dumps({"type": "assistant", "timestamp": ts, "requestId": "r-" + msg_id,
                       "message": {"id": msg_id, "model": model, "content": content, "usage": usage}},
                      ensure_ascii=False)


def transcript(path, final_text):
    """Two API responses. The first spans two lines (its output count grows to 50), and one of its
    tool calls repeats on both lines; the second reads 20K from cache and ends with final_text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    first = {"input_tokens": 10, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 0,
             "cache_creation": {"ephemeral_5m_input_tokens": 1000, "ephemeral_1h_input_tokens": 0}}
    lines = [
        json.dumps({"type": "user", "timestamp": "2026-09-25T10:00:00.000Z", "message": {"content": "בריף — טקסט פרטי"}}),
        line("m1", "2026-09-25T10:00:05.000Z", [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
             dict(first, output_tokens=1)),
        line("m1", "2026-09-25T10:00:06.000Z", [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}},
                                                {"type": "tool_use", "id": "t2", "name": "Grep", "input": {}}],
             dict(first, output_tokens=50)),
        line("m2", "2026-09-25T10:02:05.000Z", [{"type": "tool_use", "id": "t3", "name": "Bash", "input": {}},
                                                {"type": "text", "text": final_text}],
             {"input_tokens": 5, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 20000,
              "output_tokens": 200}),
        line("m3", "2026-09-25T10:02:06.000Z", [{"type": "text", "text": "No response requested."}],
             {"input_tokens": 0, "output_tokens": 0}, model="<synthetic>"),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    base = Path(HOME) / ".claude" / "projects" / "p"
    main_log = base / "s1.jsonl"
    transcript(main_log, "סיימתי.")
    agent_log = base / "s1" / "subagents" / "agent-a1.jsonl"
    reply = "סיכום פרטי של הממצאים\n**פסיקה או ממצא:** עובר עם 2 תיקונים\nעוד טקסט פרטי"
    transcript(agent_log, reply)
    want_numbers = ("opus-5-5 · 2 requests · 3 tools · 2m · in 15 · cache write 1.0K · cache read 20K"
                    " · out 250 · 4.5K units")
    want_main = want_numbers.replace(" · 2m", "")  # the main span includes idle time: no minutes
    sub = command("SubagentStop")
    stop = command("Stop")

    print("== a subagent finishes")
    event = {"session_id": "s1aaaaaaaaaa", "transcript_path": str(main_log), "cwd": "/root/x/wellness",
             "hook_event_name": "SubagentStop", "stop_hook_active": False, "agent_id": "a1",
             "agent_type": "legal-os:legal-verifier", "agent_transcript_path": str(agent_log)}
    case("exit 0, prints nothing", (0, ""), finish(sub, event))
    r = rows()[-1]
    case("who finished", ["s1aaaaaa", "legal-os:legal-verifier", "finished"], r[1:4])
    case("verdict line from the transcript", "**פסיקה או ממצא:** עובר עם 2 תיקונים", r[4])
    case("numbers: deduped per response, synthetic skipped", want_numbers, r[5])
    case("folder", "wellness", r[6])

    event2 = {k: v for k, v in event.items() if k != "agent_transcript_path"}
    finish(sub, event2)
    case("no agent_transcript_path: found next to the session transcript", want_numbers, rows()[-1][5])

    event3 = dict(event, last_assistant_message="תוצאת כלי:\nפסיקה: עוצר — מתוך קובץ שנקרא\nסוף")
    finish(sub, event3)
    case("the agent's own text wins over last_assistant_message", "**פסיקה או ממצא:** עובר עם 2 תיקונים",
         rows()[-1][4])

    agent_log2 = base / "s1" / "subagents" / "agent-a2.jsonl"
    transcript(agent_log2, "אין כאן שורת פסיקה")
    finish(sub, dict(event, agent_id="a2", agent_transcript_path=str(agent_log2), agent_type="Explore"))
    case("no verdict line -> '-'", ["Explore", "finished", "-"], rows()[-1][2:5])
    finish(sub, dict(event, agent_id="a2", agent_transcript_path=str(agent_log2),
                     last_assistant_message="פסיקה: עוצר — חסר אישור"))
    case("...last_assistant_message when the transcript has none", "פסיקה: עוצר — חסר אישור", rows()[-1][4])

    odd_log = base / "s1" / "subagents" / "agent-a4.jsonl"
    odd_log.write_text(line("m9", "2026-09-25T10:00:00.000Z", [], {"input_tokens": "many"}) + "\n", encoding="utf-8")
    finish(sub, dict(event, agent_id="a4", agent_transcript_path=str(odd_log)))
    case("a transcript that breaks the parser still gets a row", "no usage: error (TypeError)", rows()[-1][5])

    # As Cowork writes it (25.9): each line keeps only the response's opening output count.
    cowork_log = base / "s1" / "subagents" / "agent-a5.jsonl"
    start = {"input_tokens": 3, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    cowork_log.write_text("\n".join([
        line("c1", "2026-09-25T10:00:00.000Z", [{"type": "text", "text": "א" * 300}], dict(start, output_tokens=4)),
        line("c1", "2026-09-25T10:00:01.000Z", [{"type": "tool_use", "id": "t9", "name": "Glob", "input": {}}],
             dict(start, output_tokens=4)),
        line("c2", "2026-09-25T10:00:02.000Z", [{"type": "text", "text": "ב" * 30}], dict(start, output_tokens=1)),
    ]) + "\n", encoding="utf-8")
    finish(sub, dict(event, agent_id="a5", agent_transcript_path=str(cowork_log)))
    case("opening-only output counts are estimated from length, marked ~", True,
         " · out ~111 · " in rows()[-1][5])  # (300 + len("{}")) / 3 = 101, 30 / 3 = 10

    repeat_log = base / "s1" / "subagents" / "agent-a6.jsonl"
    text = [{"type": "text", "text": "ג" * 90}]
    repeat_log.write_text("\n".join([
        line("r1", "2026-09-25T10:00:00.000Z", text, dict(start, output_tokens=2)),
        line("r1", "2026-09-25T10:00:01.000Z", text, dict(start, output_tokens=2)),  # the same block again
        line("r2", "2026-09-25T10:00:02.000Z", [{"type": "text", "text": "ok"}], dict(start, output_tokens=40)),
        line("r2", "2026-09-25T10:00:03.000Z", [{"type": "text", "text": "ok"}], dict(start, output_tokens=7)),
    ]) + "\n", encoding="utf-8")
    finish(sub, dict(event, agent_id="a6", agent_transcript_path=str(repeat_log)))
    case("a block repeated on two lines counts once; the higher output count is kept", True,
         " · out ~70 · " in rows()[-1][5])  # 90 / 3 = 30 estimated, + 40 kept over the later 7

    late_log = base / "s1" / "subagents" / "agent-a3.jsonl"
    transcript(late_log, reply)
    lines = late_log.read_text(encoding="utf-8").splitlines(keepends=True)
    late_log.write_text("".join(lines[:3]), encoding="utf-8")  # the last response is not written yet
    n = len(rows())
    t0 = time.monotonic()
    code = run(sub, dict(event, agent_id="a3", agent_transcript_path=str(late_log)), BUSINESS_OS_SETTLE_QUIET="2")
    took = time.monotonic() - t0
    time.sleep(0.2)
    with open(late_log, "a", encoding="utf-8") as f:
        f.writelines(lines[3:])
    end = time.monotonic() + 10
    while len(rows()) == n and time.monotonic() < end:
        time.sleep(0.05)
    case("the hook returns before the transcript settles", (True, (0, "")), (took < 0.5, code))
    case("a last response written after the hook fired is counted", want_numbers, rows()[-1][5])
    case("...and its verdict line found", "**פסיקה או ממצא:** עובר עם 2 תיקונים", rows()[-1][4])

    missing = dict(event, agent_transcript_path="/nope/agent-x.jsonl", agent_type="design-os:design-critic",
                   last_assistant_message="ערך-סודי-לא-להדפיס")
    finish(sub, missing)
    got = rows()[-1][5]
    case("no transcript: says so, with field names", True,
         got.startswith("no usage: transcript not found (fields: agent_id,agent_transcript_path,agent_type,cwd,"))
    case("...the whole field list, past 120 characters", True, len(got) > 120 and got.endswith("transcript_path)"))

    print("== the main conversation stops")
    case("exit 0, prints nothing", (0, ""),
         finish(stop, {"session_id": "s1aaaaaaaaaa", "transcript_path": str(main_log), "cwd": "/root/x/wellness",
                    "hook_event_name": "Stop", "stop_hook_active": False}))
    finish(sub, event)
    call_ev = {"session_id": "s1aaaaaaaaaa", "cwd": "/root/x/wellness", "tool_name": "Agent",
               "tool_input": {"subagent_type": "legal-os:legal-lead", "description": "קריאה לפני הסיכום"}}
    subprocess.run([sys.executable, str(PLUGIN / "hooks" / "ledger.py")], input=json.dumps(call_ev).encode(),
                   env=dict(os.environ, HOME=HOME), check=True)  # main's own call row, the CEO's proof
    before = rows()
    case("the fixture has main's call row", True, any(r[2] == "main" and r[3] == "legal-os:legal-lead" for r in before))
    finish(stop, {"session_id": "s1aaaaaaaaaa", "transcript_path": str(main_log), "hook_event_name": "Stop"})
    totals = [r for r in rows() if r[3] == "main total"]
    case("one main-total row per session", 1, len(totals))
    case("...and it moved to the end", "main total", rows()[-1][3])
    case("...every other row of the session kept, in order",
         [r for r in before if not (r[1] == "s1aaaaaa" and r[3] == "main total")], rows()[:-1])
    case("...with the main transcript's numbers", ["main", want_main], [totals[0][2], totals[0][5]])
    case("no verdict for the main conversation", "-", totals[0][4])
    finish(stop, {"session_id": "s2bbbbbbbbbb", "transcript_path": str(main_log), "hook_event_name": "Stop"})
    case("another session gets its own row", 2, len([r for r in rows() if r[3] == "main total"]))

    print("== nothing private, nothing blocked")
    text = (Path(HOME) / ".business-os" / "ledger.md").read_text(encoding="utf-8")
    case("reply text is not recorded", False, "טקסט פרטי" in text or "סיכום פרטי" in text or "סיימתי" in text)
    case("a field's value is not recorded", False, "ערך-סודי" in text)
    case("ledger is private (0600)", 0o600, stat.S_IMODE(os.stat(Path(HOME) / ".business-os" / "ledger.md").st_mode))
    n = len(rows())
    case("garbage event: exit 0, prints nothing", (0, ""), run(sub, None, raw="{not json"))
    case("...and writes nothing", n, len(rows()))
    case("a list, not an event: exit 0", (0, ""), run(stop, None, raw="[1, 2]"))

    print("== agent calls (PreToolUse) still logged")
    call = [sys.executable, str(PLUGIN / "hooks" / "ledger.py")]
    ev = {"session_id": "s1aaaaaaaaaa", "cwd": "/root/x/wellness", "tool_name": "Agent",
          "tool_input": {"subagent_type": "legal-os:legal-lead", "description": "בדיקה", "prompt": "פרומפט פרטי"}}
    subprocess.run(call, input=json.dumps(ev).encode(), env=dict(os.environ, HOME=HOME), check=True)
    case("call row", ["main", "legal-os:legal-lead", "ok", "בדיקה"], rows()[-1][2:6])
    long_ev = dict(ev, tool_input=dict(ev["tool_input"], description="ת" * 200))
    subprocess.run(call, input=json.dumps(long_ev).encode(), env=dict(os.environ, HOME=HOME), check=True)
    case("a call row's description stays cut at 120", 120, len(rows()[-1][5]))
    case("prompt is not recorded", False, "פרומפט פרטי" in (Path(HOME) / ".business-os" / "ledger.md").read_text(encoding="utf-8"))

    print("== the ledger stays under 1 MB")
    log = Path(HOME) / ".business-os" / "ledger.md"
    filler = "| 2026-01-01 00:00:00 | old | main | x | ok | " + "פ" * 200 + " | f |\n"
    with open(log, "a", encoding="utf-8") as f:
        f.write(filler * 2600)
    subprocess.run(call, input=json.dumps(ev).encode(), env=dict(os.environ, HOME=HOME), check=True)
    case("an append past 1 MB halves it", True, log.stat().st_size < 1_000_000)
    case("...keeps the header and the new row", (True, "legal-os:legal-lead"),
         (log.read_text(encoding="utf-8").startswith("| time (UTC) |"), rows()[-1][3]))
    with open(log, "a", encoding="utf-8") as f:
        f.write(filler * 2600)
    finish(stop, {"session_id": "s1aaaaaaaaaa", "transcript_path": str(main_log), "hook_event_name": "Stop"})
    case("a main-total update past 1 MB halves it too", True, log.stat().st_size < 1_000_000)
    case("...and the row is last", "main total", rows()[-1][3])


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as home:
        HOME = home
        main()
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)
