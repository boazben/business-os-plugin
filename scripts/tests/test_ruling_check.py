"""plugins/business-os/skills/orient/ruling_check.py: the structural part of moving conditions between rulings.

Run: python3 scripts/tests/test_ruling_check.py
"""
import importlib.util
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("ruling_check", ROOT / "plugins/business-os/skills/orient/ruling_check.py")
assert spec and spec.loader
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)
failures = []


def check(name, cond):
    if not cond:
        failures.append(name)
    print(("ok   " if cond else "FAIL ") + name)


OLD = """# בדיקת שינוי (2026-10-05)

פסיקה: עובר בתנאי

## תנאים פתוחים לפני ביצוע
1. `49.1` ↪ הועבר ל-`51.1` (2026-10-05). **ה-SHA שממוזג הוא `aaa`.** commit אחר נבדק כשינוי, עם ה-diff ודוח site-review על ה-SHA החדש.
2. `49.2` ↪ הועבר ל-`51.2` (2026-10-05). הצהרת הנגישות מכסה את כל האתר.
3. `49.3` פתוח: הכותרת באוויר תואמת לטיוטה.
4. `45.2` (קובץ 45) — מצוטט כאן, נשאר בתוקף שם.
"""
NEW_SAME = """# בדיקת שינוי הבאה (2026-10-06)

פסיקה: עובר בתנאי

## תנאים פתוחים לפני ביצוע
1. `51.1` **ה-SHA שממוזג הוא `aaa`.** commit אחר נבדק כשינוי, עם ה-diff ודוח site-review על ה-SHA החדש.
2. `51.2` הצהרת הנגישות מכסה את כל האתר.

## מה השתנה בתנאים
- `51.2` — ללא שינוי.
"""

with tempfile.TemporaryDirectory() as d:
    def files(old, new):
        o, n = pathlib.Path(d) / "49 - ישן.md", pathlib.Path(d) / "51 - חדש.md"
        o.write_text(old, encoding="utf-8")
        n.write_text(new, encoding="utf-8")
        return str(o), str(n)

    p, c, notes, rep = rc.check(*files(OLD, NEW_SAME))
    check("moved without a wording change: no problem, no verifier", p == [] and c == [] and len(notes) == 2)
    check("a changelog section repeating an ID is not a duplicate", not any("כפול" in x for x in p))
    check("no 'מחליף את:' header → not a replacement", rep is False)

    # V49-1: the clause about site-review dropped while the condition moved
    dropped = NEW_SAME.replace(", עם ה-diff ודוח site-review על ה-SHA החדש", "")
    p, c, notes, rep = rc.check(*files(OLD, dropped))
    check("a dropped clause is a wording change for the verifier", len(c) == 1 and "site-review" in c[0])

    p, c, notes, rep = rc.check(*files(OLD, NEW_SAME.replace("`51.2`", "`51.9`", 1)))
    check("a move to an ID that doesn't exist is a structural problem", any("51.2" in x and "נעלם" in x for x in p))

    dup = NEW_SAME.replace("2. `51.2`", "2. `51.1`")
    p, c, notes, rep = rc.check(*files(OLD, dup))
    check("a duplicate ID in the conditions list is a problem", any("כפול" in x and "51.1" in x for x in p))

    replacing = NEW_SAME.replace("פסיקה: עובר בתנאי", "מחליף את: `משפטי/49 - ישן.md`\n\nפסיקה: עובר בתנאי", 1)
    p, c, notes, rep = rc.check(*files(OLD, replacing))
    check("a replacing ruling is recognised", rep is True)
    check("an own condition left open in the replaced ruling is a problem", any(x.startswith("49.3") for x in p))
    check("a quoted condition of another ruling (45.2) is not required to move", not any(x.startswith("45.2") for x in p))

    # the reviewer's cases (5.10): a dropped continuation line, a maqaf move to nowhere, an unreadable move,
    # a stage change, and a replacement header that names another ruling
    multi_old = OLD.replace("2. `49.2` ↪ הועבר ל-`51.2` (2026-10-05). הצהרת הנגישות מכסה את כל האתר.",
                            "2. `49.2` ↪ הועבר ל-`51.2` (2026-10-05). הצהרת הנגישות מכסה את כל האתר.\n   גם בדף הניהול.")
    multi_new = NEW_SAME.replace("2. `51.2` הצהרת הנגישות מכסה את כל האתר.",
                                 "2. `51.2` הצהרת הנגישות מכסה את כל האתר.\n   גם בדף הניהול.")
    p, c, notes, rep = rc.check(*files(multi_old, multi_new))
    check("continuation lines that match → no change", c == [] and p == [])
    p, c, notes, rep = rc.check(*files(multi_old, NEW_SAME))
    check("a dropped continuation line is a wording change", any("51.2" in x and "הניהול" in x for x in c))
    maqaf = OLD.replace("↪ הועבר ל-`51.2`", "↪ הועבר ל־`51.7`")
    p, c, notes, rep = rc.check(*files(maqaf, NEW_SAME))
    check("a maqaf move to a missing target is a problem", any("51.7" in x and "נעלם" in x for x in p))
    unread = OLD.replace("↪ הועבר ל-`51.2` (2026-10-05).", "↪ הועבר לפסיקה 51 (51.3).")
    p, c, notes, rep = rc.check(*files(unread, NEW_SAME))
    check("an unreadable move target is a problem, not a skip", any(x.startswith("49.2") and "יעד" in x for x in p))
    staged_old = OLD.replace("## תנאים פתוחים לפני ביצוע", "## תנאים פתוחים\n### א. לפני פרסום")
    staged_new = NEW_SAME.replace("## תנאים פתוחים לפני ביצוע\n1. `51.1`", "## תנאים פתוחים\n### א. לפני פרסום\n1. `51.1`").replace("2. `51.2`", "### ב. לפני מכירה\n2. `51.2`")
    p, c, notes, rep = rc.check(*files(staged_old, staged_new))
    check("same words under a later stage → listed for the verifier", any("51.2" in x and "שלב" in x for x in c))
    other = NEW_SAME.replace("פסיקה: עובר בתנאי", "מחליף את: פסיקה ארבעים ותשע\n\nפסיקה: עובר בתנאי", 1)
    p, c, notes, rep = rc.check(*files(OLD, other))
    check("a 'מחליף את:' that doesn't name the old ruling is reported", rep is False and any("מחליף את" in x for x in p))

    check("exit 3 when only wording changed", rc.main(["x", *files(OLD, dropped)]) == 3)
    check("exit 0 when structure and wording hold", rc.main(["x", *files(OLD, NEW_SAME)]) == 0)

m = rc.conditions.ITEM.match("1. `46א.2` נוסח")
check("conditions.py reads an ID with a Hebrew letter (46א.2)", bool(m) and m.group(1) == "46א.2")
check("…and still the old 25ב form", bool(rc.conditions.ITEM.match("- `25ב.3` x")))

print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
