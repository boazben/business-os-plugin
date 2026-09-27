"""scripts/signals.py: the phase-2 signals, computed from git, the ledgers and the venture folder.

Run: python3 scripts/tests/test_signals.py
Git signals (C20, C7) run against a scratch repo with dated commits, so the test doesn't depend on
this repo's history or on today's date; ledger and folder signals (C16, C1, C12) against files built
here. The state file lives in a throwaway HOME. When a signal comes out wrong, add the case here first.
"""
import datetime
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "signals.py"
failures = []


def check(name, cond, got=None):
    if not cond:
        failures.append(name)
    print(("ok   " if cond else "FAIL ") + name + ("" if cond or got is None else f"\n     got: {got!r}"))


def utc(days_ago=0):
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def main(tmp):
    home, repo = tmp / "home", tmp / "repo"
    (home / ".business-os").mkdir(parents=True)
    state_file = home / ".business-os" / "signals.json"
    env = dict(os.environ, HOME=str(home), BUSINESS_OS_SIGNALS_ROOT=str(repo))

    def run(*args, **extra):
        p = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=dict(env, **extra))
        return p.returncode, p.stdout, p.stderr

    def status():
        out = run("--status")[1]
        return {l.split(":")[0][2:]: l for l in out.splitlines() if ": " in l and l[:1] in "🔔·?"}

    def lit(i):
        return status().get(i, "").startswith("🔔")

    def git(*args, days_ago=0):
        date = utc(days_ago).replace(" ", "T") + "+0000"
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                       env=dict(os.environ, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date,
                                GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))

    def head():
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

    def write(rel, text):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")

    def commit(msg, days_ago=0):
        git("add", "-A", days_ago=days_ago)
        git("commit", "-q", "-m", msg, days_ago=days_ago)

    def ack(i, commit_sha=None, days_ago=0):
        state = json.loads(state_file.read_text(encoding="utf-8"))
        state["ack"][i] = {"time": utc(days_ago), "commit": commit_sha or head()}
        state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    repo.mkdir()
    git("init", "-q")
    write("plugins/business-os/skills/ledger/report.py", 'REVIEWERS = {\n    "legal-verifier", "brand-guardian",\n}\n')
    write("plugins/legal-os/agents/legal-verifier.md", "# legal-verifier\nמאמת כל ציטוט מול מקור רשמי.\nלא מתקן בעצמו.\n")
    write("plugins/marketing-os/agents/brand-guardian.md", "# brand-guardian\nבודק מדיניות פרסום.\n")
    write("plugins/marketing-os/agents/campaign-copywriter.md", "# copywriter\nכותב קופי.\n")
    write("CEO-PERSONA.md", "# זהות\nאתה המנכ\"ל.\n### שער האישור\nאף מחלקה לא בונה בלי אישור מפורש.\n### שער משפטי\n**עוצר** — מביאים אליי כחסם.\n## טון\nפשוט.\n")
    commit("base", days_ago=40)

    print("== first run: counts from now, says nothing")
    code, out, err = run()
    check("exit 0 and silent", (code, out) == (0, ""), (code, out, err))
    state = json.loads(state_file.read_text(encoding="utf-8"))
    check("the state is created with every signal decided now", set(state["ack"]) == {"C20", "C16", "C1", "C7", "C12"})
    check("...private (0600)", oct(state_file.stat().st_mode & 0o777) == "0o600")

    print("== C7: days with a persona change")
    for d in (12, 8, 3):
        write("CEO-PERSONA.md", (repo / "CEO-PERSONA.md").read_text(encoding="utf-8") + f"שורה {d}\n")
        commit(f"persona {d}", days_ago=d)
    # Dated commits in order, as real history is: the base 40 days ago, the persona 12, 8 and 3 days ago.
    ack("C7", days_ago=35)
    check("C7 lit: 3 days of change within 30", lit("C7") and "3 ימים" in status()["C7"], status().get("C7"))
    ack("C7", days_ago=5)
    check("C7 quiet: 1 day since the last decision", not lit("C7") and "1 ימים" in status()["C7"], status().get("C7"))
    ack("C7", days_ago=60)
    check("C7 counts 30 days back at most: the base commit, 40 days ago, is out", "3 ימים" in status()["C7"], status().get("C7"))


    print("== C20: removed or reworded words, never pure additions")
    cases = [
        ("a new line added to a reviewer", "plugins/legal-os/agents/legal-verifier.md",
         "# legal-verifier\nמאמת כל ציטוט מול מקור רשמי.\nלא מתקן בעצמו.\nשורה חדשה.\n", False),
        ("words added to an existing line", "plugins/legal-os/agents/legal-verifier.md",
         "# legal-verifier\nמאמת כל ציטוט מול מקור רשמי ומעודכן.\nלא מתקן בעצמו.\nשורה חדשה.\n", False),
        ("a word reworded", "plugins/legal-os/agents/legal-verifier.md",
         "# legal-verifier\nמאמת חלק מהציטוטים מול מקור רשמי ומעודכן.\nלא מתקן בעצמו.\nשורה חדשה.\n", True),
        ("a non-reviewer changed", "plugins/marketing-os/agents/campaign-copywriter.md", "# copywriter\nכותב.\n", False),
        ("a persona line outside the gates reworded", "CEO-PERSONA.md",
         "# זהות\nאתה המנכ\"ל שלי.\n### שער האישור\nאף מחלקה לא בונה בלי אישור מפורש.\n### שער משפטי\n**עוצר** — מביאים אליי כחסם.\n## טון\nקצר.\n", False),
        ("a gate reworded", "CEO-PERSONA.md",
         "# זהות\nאתה המנכ\"ל שלי.\n### שער האישור\nאף מחלקה לא בונה בלי אישור.\n### שער משפטי\n**עוצר** — מביאים אליי כחסם.\n## טון\nקצר.\n", True),
        ("a rule inverted by adding 'לא'", "plugins/legal-os/agents/legal-verifier.md",
         "# legal-verifier\nלא מאמת חלק מהציטוטים מול מקור רשמי ומעודכן.\nלא מתקן בעצמו.\nשורה חדשה.\n", True),
        ("a label turned from [חוסם] to [לא חוסם]", "plugins/marketing-os/agents/brand-guardian.md",
         "# brand-guardian\nבודק מדיניות פרסום. `[לא חוסם]`\n", True),
        ("a gate weakened by adding 'רק'", "CEO-PERSONA.md",
         "# זהות\nאתה המנכ\"ל שלי.\n### שער האישור\nאף מחלקה לא בונה בלי אישור.\n### שער משפטי\n**עוצר** — רק מביאים אליי כחסם.\n## טון\nקצר.\n", True),
    ]
    write("plugins/marketing-os/agents/brand-guardian.md", "# brand-guardian\nבודק מדיניות פרסום. `[חוסם]`\n")
    commit("label as blocking")
    for name, rel, text, want in cases:
        before = head()
        write(rel, text)
        commit(name)
        ack("C20", before)
        check(f"C20 {'lit' if want else 'quiet'}: {name}", lit("C20") == want, status().get("C20"))
    before = head()
    (repo / "plugins/marketing-os/agents/brand-guardian.md").unlink()
    write("plugins/business-os/skills/ledger/report.py", 'REVIEWERS = {\n    "legal-verifier",\n}\n')
    commit("drop a reviewer")
    ack("C20", before)
    check("C20 lit: a reviewer deleted, and dropped from the list too", lit("C20") and "brand-guardian" in status()["C20"], status().get("C20"))
    code, out, _ = run("--ack", "C20")
    check("--ack moves C20's base to now, and it is quiet", code == 0 and "C20: נספר מעכשיו" in out and not lit("C20"), out)

    print("== C16, C1 and C12 from ledgers and the venture")
    venture = tmp / "venture"
    (venture / "_מערכת" / "ledger").mkdir(parents=True)
    code, out, _ = run("--venture", str(venture))
    check("--venture adds the folder", code == 0 and json.loads(state_file.read_text(encoding="utf-8"))["ventures"] == [str(venture)], out)
    for i in ("C16", "C1"):
        ack(i, days_ago=40)
    head_row = "| time (UTC) | session | caller | target | policy | description | folder |\n|---|---|---|---|---|---|---|\n"

    def research(session, description, days_ago):
        return f"| {utc(days_ago)} | {session} | legal-os:legal-lead | legal-os:regulatory-researcher | ok | {description} · brief@1a2b3c4d · saw a.md@11111111 | claude |\n"
    ledger = venture / "_מערכת" / "ledger"
    (ledger / "a.md").write_text(head_row + research("aaaa", "Research Israeli privacy law", 10)
                                 + research("aaaa", "Consumer refund rules online sales", 9), encoding="utf-8")
    (ledger / "b.md").write_text(head_row + research("bbbb", "Research Israeli tax law", 5)
                                 + research("bbbb", "מחקר חוק הגנת הצרכן", 4), encoding="utf-8")
    (ledger / "c.md").write_text(head_row + research("cccc", "מחקר חוק הגנת הפרטיות", 3), encoding="utf-8")
    check("C16 quiet: privacy vs tax, consumer vs privacy — different topics", not lit("C16"), status().get("C16"))
    (ledger / "f.md").write_text(head_row + research("ffff", "Privacy policy requirements for website", 3)
                                 + research("ffff2", "Refund policy requirements for website", 3).replace("| ffff2 |", "| gggg |"),
                                 encoding="utf-8")
    check("C16 quiet: two policies for one website are different topics", not lit("C16"), status().get("C16"))
    (ledger / "f.md").unlink()
    (ledger / "g.md").write_text(head_row + research("hhhh", "מע\"מ לעוסק פטור", 3) + research("iiii", "עוסק פטור ומע\"מ על מכירות", 2),
                                 encoding="utf-8")
    check("C16 lit: מע\"מ and a Hebrew prefix read as the same words", lit("C16"), status().get("C16"))
    (ledger / "g.md").unlink()
    (ledger / "d.md").write_text(head_row + research("dddd", "Refund rules for online consumer sales", 2), encoding="utf-8")
    check("C16 lit: the same topic in another run", lit("C16") and "1 זוגות" in status()["C16"], status().get("C16"))
    (ledger / "d.md").write_text(head_row + research("aaaa", "Refund rules for online consumer sales", 2), encoding="utf-8")
    check("C16 quiet: the same topic twice in one run is one research", not lit("C16"), status().get("C16"))
    (ledger / "e.md").write_text((ledger / "a.md").read_text(encoding="utf-8"), encoding="utf-8")
    check("...and a ledger returned twice is read once", not lit("C16"), status().get("C16"))

    call = f"| {utc(1)} | cccc | main | marketing-os:marketing-lead | ok | same brief | v |\n"
    (home / ".business-os" / "ledger.md").write_text(head_row + call * 31, encoding="utf-8")
    check("C1 lit: 31 department calls in Claude Code, identical ones counted each", lit("C1") and "31 הפעלות" in status()["C1"], status().get("C1"))
    (home / ".business-os" / "ledger.md").write_text(head_row + call, encoding="utf-8")
    check("C1 quiet under the threshold", not lit("C1"), status().get("C1"))

    incoming = venture / "_מערכת" / "incoming" / "2026-09-27T04-30Z"
    incoming.mkdir(parents=True)
    old = incoming / "old.md"
    old.write_text("x", encoding="utf-8")
    os.utime(old, (0, 0))
    check("C12 quiet: a file older than the last decision", not lit("C12"), status().get("C12"))
    (incoming / "מודעה.md").write_text("x", encoding="utf-8")
    check("C12 lit: a new file waits", lit("C12") and "1 קבצים" in status()["C12"], status().get("C12"))
    run("--ack", "C12")
    check("--ack C12 silences it until another file arrives", not lit("C12"), status().get("C12"))

    print("== never in the way")
    good = state_file.read_text(encoding="utf-8")
    state_file.write_text(good.rstrip()[:-1] + ",}", encoding="utf-8")  # a hand edit with a stray comma
    broken = state_file.read_text(encoding="utf-8")
    code, out, _ = run("--hook")
    check("a state file that doesn't parse: said so, and left untouched",
          code == 0 and "לא תקין" in json.loads(out)["systemMessage"] and state_file.read_text(encoding="utf-8") == broken, out)
    state_file.write_text(good, encoding="utf-8")
    code, out, _ = run("--ack", "C99")
    check("an unknown id is refused", code == 2)
    stranger = tmp / "stranger"
    stranger.mkdir()
    code, out, _ = run("--hook", HOME=str(stranger))
    check("a machine without ~/.business-os: silent, and nothing created",
          (code, out) == (0, "") and not (stranger / ".business-os").exists(), out)
    r = subprocess.run(["bash", str(ROOT / "scripts" / "session-start.sh")], capture_output=True, text=True,
                       env=dict(env, CLAUDE_PROJECT_DIR=str(ROOT)))
    check("session-start: exit 0, and its output is empty or one JSON object",
          r.returncode == 0 and (not r.stdout.strip() or json.loads(r.stdout)), r.stdout)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        main(pathlib.Path(d))
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)
