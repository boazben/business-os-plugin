"""What the SessionStart persona sync writes, and when it refuses to.

Run: python3 plugins/business-os/hooks/tests/test_sync_persona.py
Uses a throwaway git repo as the "plugin repo" and throwaway targets; never touches
~/repos or ~/ventures.
"""
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("sync_persona", HERE.parent / "sync-persona.py")
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)
failures = []


def check(name, cond):
    if not cond:
        failures.append(name)
    print(("ok   " if cond else "FAIL ") + name)


RULES = [f"כלל שער מספר {i}" for i in range(1, 11)]
PERSONA = "> הערה טכנית שלא נכנסת לעותק\n> עוד שורה\n\n# זהות\nאתה רשת הביטחון.\nהוא בקשה, לא אישור.\n" + "\n".join(RULES) + "\n"
ANCHORS = "# comment\nאתה רשת הביטחון\nהוא בקשה, לא אישור\n" + "\n".join(RULES) + "\n"

with tempfile.TemporaryDirectory() as d:
    d = pathlib.Path(d)
    (d / "repos").mkdir()
    (d / "ventures").mkdir()
    targets = [d / "repos" / "CLAUDE.md", d / "ventures" / "CLAUDE.md", d / "missing" / "CLAUDE.md"]

    check("strip_note drops the leading '>' block", sp.strip_note(PERSONA).startswith("# זהות"))
    check("strip_note leaves text without a note alone", sp.strip_note("# זהות\n") == "# זהות\n")
    check("anchors match across line breaks", sp.missing_anchors("אתה רשת\nהביטחון. הוא בקשה, לא אישור", "אתה רשת הביטחון\nהוא בקשה, לא אישור\n") == [])

    out = sp.sync_copies(PERSONA, ANCHORS, "a" * 40, targets)
    body = targets[0].read_text(encoding="utf-8")
    check("writes both existing targets", targets[0].exists() and targets[1].exists())
    check("skips a target whose folder does not exist", not targets[2].exists())
    check("copy has the generated header", body.startswith("<!-- נוצר אוטומטית"))
    check("copy ends with the persona version line", body.rstrip().endswith("גרסת פרסונה: aaaaaaa"))
    check("copy has no technical note", "הערה טכנית" not in body)
    check("reports the update once", len(out) == 1 and "aaaaaaa" in out[0])
    check("second run with the same content is silent", sp.sync_copies(PERSONA, ANCHORS, "a" * 40, targets) == [])

    weakened = PERSONA.replace("אתה רשת הביטחון.", "")
    out = sp.sync_copies(weakened, ANCHORS, "b" * 40, targets)
    check("a version missing a gate anchor is refused", len(out) == 1 and "נכשלה" in out[0])
    check("refused version leaves the previous copy in place", targets[0].read_text(encoding="utf-8") == body)
    check("no temp files left behind", not [p for p in (d / "repos").iterdir() if p.name.startswith(".CLAUDE.md.")])

    # anchors can't be dropped together with their rule
    sneaky_persona = PERSONA.replace("כלל שער מספר 3", "")
    sneaky_anchors = ANCHORS.replace("כלל שער מספר 3\n", "")
    out = sp.sync_copies(sneaky_persona, sneaky_anchors, "c" * 40, targets, base_anchors=ANCHORS)
    check("rule + its anchor removed together is refused", len(out) == 1 and "הוסר עוגן" in out[0])
    check("…and the previous copy stays", targets[0].read_text(encoding="utf-8") == body)
    out = sp.sync_copies(sneaky_persona, sneaky_anchors, "d" * 40, targets, base_anchors=ANCHORS,
                         approved="כלל שער מספר 3\n")
    check("removal the founder approved goes through", len(out) == 1 and "ddddddd" in out[0])
    out = sp.sync_copies(PERSONA, "# nothing\n", "e" * 40, targets)
    check("an emptied anchor list is refused", len(out) == 1 and "קצרה" in out[0])

    # a gate phrase that survives only in the dropped '>' note does not count
    note_only = "> הוא בקשה, לא אישור\n\n" + PERSONA.replace("הוא בקשה, לא אישור.", "כן אישור.")
    out = sp.sync_copies(note_only, ANCHORS, "f" * 40, targets)
    check("an anchor only in the stripped note is refused", len(out) == 1 and "נכשלה" in out[0])
    check("a missing anchor is listed once", out[0].count("הוא בקשה, לא אישור") == 1 and "(1 " in out[0])

    # the trusted list is written on success, so a pull can't change what counts as removed
    trusted = d / "trusted.txt"
    out = sp.sync_copies(PERSONA, ANCHORS, "a" * 40, targets, trusted_path=trusted)
    check("success writes the trusted anchor list", sp.parse_anchors(trusted.read_text(encoding="utf-8")) == sp.parse_anchors(ANCHORS))
    out = sp.sync_copies(PERSONA, "\n".join(RULES) + "\n", "a" * 40, targets, trusted.read_text(encoding="utf-8"), "", trusted)
    check("anchor dropped against the trusted list is refused", len(out) == 1 and "הוסר עוגן" in out[0])
    check("…and the trusted list keeps it", "הוא בקשה, לא אישור" in trusted.read_text(encoding="utf-8"))
    check("a BOM does not turn the comment into an anchor", sp.parse_anchors("\ufeff# comment\nx\n") == ["x"])

    # an unreadable (UTF-16) copy is overwritten, not a crash
    targets[0].write_bytes(b"\xff\xfe" + "x".encode("utf-16-le"))
    sp.sync_copies(PERSONA, ANCHORS, "a" * 40, targets)
    check("a corrupt copy is repaired", targets[0].read_text(encoding="utf-8").startswith("<!-- נוצר אוטומטית"))

    # the trusted list: deleted or emptied -> rebuilt from the last generated copy's version, never local main
    repo = d / "repo"
    (repo / "scripts").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "scripts" / "persona-anchors.txt").write_text(ANCHORS, encoding="utf-8")
    g = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(g + ["add", "-A"], check=True)
    subprocess.run(g + ["commit", "-qm", "good"], check=True)
    good = subprocess.run(g + ["rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (repo / "scripts" / "persona-anchors.txt").write_text("\n".join(RULES) + "\n", encoding="utf-8")
    subprocess.run(g + ["commit", "-qam", "drop two anchors on main"], check=True)
    sp.REPO = repo
    tgt = [d / "repos" / "CLAUDE.md"]
    tgt[0].write_text(sp.build_copy(PERSONA, good), encoding="utf-8")
    missing = d / "no-trusted.txt"
    check("a deleted trusted list is rebuilt from the copy's version",
          "הוא בקשה, לא אישור" in sp.trusted_base(missing, tgt))
    missing.write_text("\n", encoding="utf-8")
    check("an emptied trusted list is rebuilt the same way", "הוא בקשה, לא אישור" in sp.trusted_base(missing, tgt))
    tgt[0].write_text(sp.build_copy(PERSONA, "f" * 40), encoding="utf-8")
    try:
        sp.trusted_base(missing, tgt)
        check("an unknown copy version refuses, doesn't fall back to main", False)
    except RuntimeError:
        check("an unknown copy version refuses, doesn't fall back to main", True)
    (repo / "old.txt").write_text("x", encoding="utf-8")
    subprocess.run(g + ["add", "old.txt"], check=True)
    subprocess.run(g + ["rm", "-q", "--cached", "scripts/persona-anchors.txt"], check=True)
    subprocess.run(g + ["commit", "-qm", "a version with no anchors file"], check=True)
    pre = subprocess.run(g + ["rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    subprocess.run(g + ["reset", "-q", "--hard", "HEAD~1"], check=True)
    tgt[0].write_text(sp.build_copy(PERSONA, pre), encoding="utf-8")
    check("a copy from before the anchors file counts as a first run",
          sp.trusted_base(missing, tgt).strip() == "\n".join(RULES))
    tgt[0].write_text("hand-written copy\n", encoding="utf-8")
    check("first run ever (no generated copy) uses local main", sp.trusted_base(missing, tgt).strip() == "\n".join(RULES))

    # an approval is used up once its removal is synced
    appr = d / "approved.txt"
    appr.write_text("# ok\nהוא בקשה, לא אישור\nכלל שער מספר 1\n", encoding="utf-8")
    sp.consume_approvals(appr, "\n".join(RULES) + "\n")
    left = appr.read_text(encoding="utf-8")
    check("a used approval is removed", "הוא בקשה" not in left)
    check("an approval not yet used stays", "כלל שער מספר 1" in left)

    # main() outside Linux or without the repo exits quietly
    os.environ["BOS_PLUGIN_REPO"] = str(d / "no-such-repo")
    r = subprocess.run([sys.executable, str(HERE.parent / "sync-persona.py")], capture_output=True, text=True,
                       env=dict(os.environ, BOS_PLUGIN_REPO=str(d / "no-such-repo")))
    check("no plugin repo -> exit 0, no output", r.returncode == 0 and r.stdout == "")

print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
