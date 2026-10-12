"""plugins/business-os/skills/tidy/tidy.py — version siblings, legal index, temp cleanup, archive + log.

Run: python3 plugins/business-os/skills/tidy/tests/test_tidy.py
"""
import importlib.util
import os
import pathlib
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("tidy", HERE.parent / "tidy.py")
td = importlib.util.module_from_spec(spec)
spec.loader.exec_module(td)
failures = []


def check(name, cond):
    if not cond:
        failures.append(name)
    print(("ok   " if cond else "FAIL ") + name)


# stem_key / versioned
check("v5.1 and v5.2 of one text share a key",
      td.stem_key("32 - מדיניות פרטיות v5.1 - תיבה שנייה (הצעה).md") != "" and
      td.stem_key("13 - מוקאפ (11.10.2026)") == td.stem_key("13 - מוקאפ v2 (11.10.2026)"))
check("round 1 and round 2 are versions",
      td.stem_key("תמונות - סבב 1 (11.10.2026)") == td.stem_key("תמונות - סבב 2 (11.10.2026)"))
check("a plain name is not versioned", not td.versioned("02 - רקע ומטרות העסק.md"))
check("(טיוטה) is a version marker", td.versioned("25 - מסמך הגדרות מאגר (טיוטה).md"))

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp) / "יזמות" / "venture"
    (root / "שיווק").mkdir(parents=True)
    (root / "שיווק" / "מוקאפ (11.10.2026)").mkdir()
    (root / "שיווק" / "מוקאפ v2 (11.10.2026)").mkdir()
    (root / "שיווק" / "קהלים.md").write_text("x", encoding="utf-8")
    (root / "שיווק" / "פוסטים.md").write_text("x", encoding="utf-8")
    groups = td.version_groups(root)
    check("mockup and mockup v2 side by side are one group",
          len(groups) == 1 and sorted(groups[0][1]) == ["מוקאפ (11.10.2026)", "מוקאפ v2 (11.10.2026)"])

    legal = root / "משפטי"
    legal.mkdir()
    (legal / "00 - יומן.md").write_text("| 01 | x | `01 - נושא.md` | כן |\n", encoding="utf-8")
    (legal / "01 - נושא.md").write_text("x", encoding="utf-8")
    (legal / "02 - פסיקה חדשה.md").write_text("x", encoding="utf-8")
    check("a legal file without an index row is found", td.unindexed_legal(root) == ["02 - פסיקה חדשה.md"])

    (root / "_מערכת" / "transfer").mkdir(parents=True)
    check("an empty transfer folder is fine", td.leftovers(root) == [])
    (root / "_מערכת" / "transfer" / "round.tar").write_text("x", encoding="utf-8")
    check("a non-empty transfer folder is a leftover", td.leftovers(root) == ["_מערכת/transfer"])
    check("_מערכת is not counted", td.file_count(root) == 5)

    out = td.findings([root], budget=4, home=False)
    titles = " ".join(t for t, _ in out)
    check("findings name versions, legal index, budget and leftovers",
          "קבוצות גרסאות" in titles and "בלי שורה באינדקס" in titles and "תקרה 4" in titles and "שאריות" in titles)

    # archive: moves, keeps the relative path, logs one row
    dest = td.archive(root / "שיווק" / "מוקאפ (11.10.2026)", "הוחלף ב-v2", "שיווק/מוקאפ v2 (11.10.2026)",
                      venture=root, today="2026-10-12")
    arch = pathlib.Path(tmp) / "יזמות-ארכיון" / "venture"
    check("archive moves under <date> - הוחלף with the same relative path",
          dest == arch / "2026-10-12 - הוחלף" / "שיווק" / "מוקאפ (11.10.2026)" and dest.is_dir()
          and not (root / "שיווק" / "מוקאפ (11.10.2026)").exists())
    log = (arch / "יומן העברות.md").read_text(encoding="utf-8")
    check("archive logs a row with why and replacement",
          "| 2026-10-12 | שיווק/מוקאפ (11.10.2026) (0 קבצים) |" in log and "הוחלף ב-v2" in log
          and "שיווק/מוקאפ v2 (11.10.2026)" in log)
    check("after archiving, the version group is gone", td.version_groups(root) == [])
    (root / "_מערכת" / "transfer" / "round.tar").unlink()
    (legal / "02 - פסיקה חדשה.md").unlink()
    check("a clean venture has no findings", td.findings([root], home=False) == [])

    # clean_tmp: old run folders go, fresh ones stay
    base = pathlib.Path(tmp) / "tmp"
    (base / "old-run").mkdir(parents=True)
    (base / "old-run" / "f").write_text("x", encoding="utf-8")
    (base / "new-run").mkdir()
    old = time.time() - 5 * 86400
    for p in (base / "old-run" / "f", base / "old-run"):
        os.utime(p, (old, old))
    gone = td.clean_tmp(base=base)
    check("tmp: only the stale run folder is deleted",
          gone == ["old-run"] and (base / "new-run").exists() and not (base / "old-run").exists())

print(f"\n{'all passed' if not failures else str(len(failures)) + ' failed'}")
sys.exit(1 if failures else 0)
