"""plugins/business-os/skills/orient/conditions.py: the legal part of orient, computed from the files.

Run: python3 scripts/tests/test_conditions.py
"""
import importlib.util
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("conditions", ROOT / "plugins/business-os/skills/orient/conditions.py")
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)
failures = []


def check(name, cond):
    if not cond:
        failures.append(name)
    print(("ok   " if cond else "FAIL ") + name)


INDEX = """# יומן
| # | נושא | קובץ | בתוקף |
|---|---|---|---|
| 21 | מכירה | `21 - מכירה.md` | כן |
| 09 | ישן | `09 - ישן.md` | לא — הוחלף |
| 18 | בארכיון | `יזמות-ארכיון/v/משפטי/18 - ארכיון.md` (בארכיון) | כן — רק העוצר |
| 30 | חסר | `30 - אין.md` | כן |
"""
RULING = """# מכירה
**פסיקה:** עובר בתנאי
   פסיקה מלאה על משהו אחר, קובץ 08):** לא שורת פסיקה
**נבדק מול:** טיוטה — `/home/claude/x.html`
**האתר החי:** לא נבדק

## תנאים פתוחים
### א. לפני עלייה לאוויר
1. `21.1` **ראשון** פתוח
2. `21.2` שני ✅ נסגר 2026-09-23 — נבדק ע״י בעז
3. `21.3` שלישי ↪ הועבר ל-22.1
### ג. לפני מכירה
4. `21.4` (ייתכן שנסגר — לבדיקה) רביעי
5. `21.5` חמישי
   ✅ **נסגר 17.9** — מתחת לשורה. נותר
   לוודא דואר רשום
6. `21.6` שישי
7. ✅ **נסגר 17.9 — החלטה בלי מזהה**
8. `21.8` פרטים
    - ✅ כתובת נמסרה
    - ⚠️ תוקן 17.9 — הערת תיקון
    - ⚠️ מייל זמני — הערך הזה עדיין לא נסגר
    - ❌ עדיין ריק: שם מלא
9. בדיקת מאגר בלי מזהה
   - ✅ **נסגר 23.9** (BOS-22)
10. עוד תנאי בלי מזהה ובלי סימון

## נימוק
- ✅ **הטענה אומתה** — הערת אימות, לא תנאי

## עדכון
> **פסיקה:** עוצר → **נסגר חלקית**
"""

with tempfile.TemporaryDirectory() as d:
    docs = pathlib.Path(d) / "Documents"
    legal = docs / "יזמות" / "v" / "משפטי"
    legal.mkdir(parents=True)
    (legal / "00 - יומן.md").write_text(INDEX, encoding="utf-8")
    (legal / "21 - מכירה.md").write_text(RULING, encoding="utf-8")
    arch = docs / "יזמות-ארכיון" / "v" / "משפטי"
    arch.mkdir(parents=True)
    (arch / "18 - ארכיון.md").write_text("# ישן\nאין מזהים כאן\n", encoding="utf-8")
    out = cd.report(docs / "יזמות" / "v")

    check("an in-force ruling is listed", "## משפטי/21 — בתוקף: כן" in out)
    check("a ruling not in force is skipped", "משפטי/09" not in out)
    check("an archived ruling in force is read from the archive", "## משפטי/18" in out and "לא נמצא" not in out.split("## משפטי/18")[1].split("##")[0])
    check("a missing file is reported, not skipped", "## משפטי/30" in out and "לא נקרא" in out)
    check("header lines are verbatim", "נבדק מול: טיוטה — `/home/claude/x.html`" in out)
    check("open item listed with its stage heading", "`21.1` — פתוח — תחת \"א. לפני עלייה לאוויר\"" in out)
    check("closed item is not listed as open", "`21.2` —" not in out and "סגורים: 21.2" in out)
    check("moved item is listed as moved", "`21.3` —" not in out and "הועברו: 21.3" in out)
    check("a may-be-closed item stays, flagged", "`21.4` — פתוח — ייתכן שנסגר" in out)
    check("a ✅ under the numbered line makes it unclear, quoted", "`21.5` — לא ברור" in out and "לקרוא ולהכריע: ✅ **נסגר 17.9** — מתחת לשורה. נותר לוודא דואר רשום" in out)
    check("the next item is not affected by that note", "`21.6` — פתוח — תחת \"ג. לפני מכירה\" — שישי\n- `21.8`" in out)
    check("totals count every ID", "4 פתוחים, 1 לא ברורים מתוך 7 מזהים" in out)
    check("a partly closed item is open and quotes what is still open",
          "`21.8` — פתוח — סגור חלקית" in out and "עדיין פתוח: ❌ עדיין ריק: שם מלא" in out
          and "כתובת נמסרה" not in out and "הערת תיקון" not in out
          and "עדיין פתוח: ⚠️ מייל זמני — הערך הזה עדיין לא נסגר" in out)
    check("a no-ID item closed on a sub-line is shown as unclear, quoted",
          "(בלי מזהה בקובץ) — לא ברור" in out and "✅ **נסגר 23.9** (BOS-22)" in out)
    check("an unmarked no-ID item is counted, not called open", "עוד 1 פריטים ממוספרים בלי מזהה" in out and "עוד תנאי בלי מזהה" not in out)
    check("a ruling in force only in part doesn't list its old items", "בתוקף רק בחלקו" in out or "## משפטי/18" in out)
    check("a verification note outside the conditions is not a condition", "הטענה אומתה" not in out)
    check("a closed item without an ID is shown", "סגור, בלי מזהה בקובץ: ✅ **נסגר 17.9 — החלטה בלי מזהה**" in out)
    check("a later verdict update is quoted", "עדכון בהמשך הקובץ: עוצר → נסגר חלקית" in out)
    check("a line that only starts with the word isn't the verdict", "לא שורת פסיקה" not in out)
    check("an unchecked live site is in the recheck list", "משפטי/21: האתר החי: לא נבדק" in out)
    check("'טיוטת' counts as a draft", cd.UNVERIFIED.search("טיוטת מייל RFI") is not None)
    check("a file without IDs says so", "בלי מזהה בקובץ" in out)
    check("the cloud-path ruling is in the recheck list", "- משפטי/21: נבדק מול: טיוטה" in out.split("צריך בדיקה מחדש")[1])
    check("a ruling with no נבדק-מול line is in the recheck list as unknown",
          "משפטי/18: אין בקובץ שורת" in out.split("צריך בדיקה מחדש")[1])
    check("no index -> None", cd.report(pathlib.Path(d) / "nothing") is None)

print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
