"""plugins/business-os/skills/orient/orient.py: the whole report, with the board fetch replaced by a fixture.

Run: python3 scripts/tests/test_orient.py
"""
import importlib.util
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/business-os/skills/orient"))
spec = importlib.util.spec_from_file_location("orient", ROOT / "plugins/business-os/skills/orient/orient.py")
om = importlib.util.module_from_spec(spec)
spec.loader.exec_module(om)
failures = []


def check(name, cond):
    if not cond:
        failures.append(name)
    print(("ok   " if cond else "FAIL ") + name)


P = "https://app.notion.com/p/" + "a" * 32
u = lambda n: "https://app.notion.com/p/" + f"{n:032d}"
LONG = "ענו — בודקים. לא ענו — הודעה ל-tobysu@, ואם גם היא שותקת מורידים את iDiskk מהרשימה. " * 3
rows = [
    {"url": u(58), "מזהה": "58", "שם": "מכירה ראשונה", "סטטוס": "אבן דרך", "פרויקט": json.dumps([P]),
     "תלוי ב": json.dumps([u(50), u(52), u(53), u(54)])},
    {"url": u(50), "מזהה": "50", "שם": "מעקב ספק", "סטטוס": "חסום", "מי מבצע": "בעז", "date:יעד:start": "2026-10-08",
     "פרויקט": json.dumps([P]), "תיאור": LONG},
    {"url": u(52), "מזהה": "52", "שם": "תנאי פסיקה 21", "סטטוס": "לביצוע", "מי מבצע": "Cowork", "פרויקט": json.dumps([P])},
    {"url": u(53), "מזהה": "53", "שם": "תנאי פסיקה 15", "סטטוס": "לביצוע", "מי מבצע": "בעז", "פרויקט": json.dumps([P]),
     "תלוי ב": json.dumps([u(50)])},
    # read by id because they are not open: a done blocker, and an idea another task waits on
    {"url": u(54), "מזהה": "54", "שם": "עוסק", "סטטוס": "הושלם", "פרויקט": json.dumps([P]), "מחוץ לתצוגה": True},
    {"url": u(70), "מזהה": "70", "שם": "רעיון", "סטטוס": "רעיון", "פרויקט": json.dumps([P]), "מחוץ לתצוגה": True},
    {"url": u(71), "מזהה": "71", "שם": "אחרי הרעיון", "סטטוס": "לביצוע", "date:יעד:start": "2026-12-01",
     "פרויקט": json.dumps([P]), "תלוי ב": json.dumps([u(70)])},
    {"url": u(26), "מזהה": "26", "שם": "טופס", "סטטוס": "לביצוע", "repo": "site", "פרויקט": json.dumps([P]), "תיאור": "טופס אמיתי"},
]
PROJECT = "## מה זה\nשהות\n## החלטות מרכזיות\n- מחיר: לא נקבע.\n- תשלום: Bit בלבד.\n## אחר\n- לא החלטה"
INDEX = "| # | נושא | קובץ | בתוקף |\n|---|---|---|---|\n| 21 | מכירה | `21 - מכירה.md` | כן |\n| 01 | עוסק | `01 - עוסק.md` | כן |\n"
R21 = ("**פסיקה:** עובר בתנאי\n**נבדק מול:** טיוטה — `/home/claude/x.html`\n**האתר החי:** לא נבדק\n"
       "## תנאים\n### א. לפני עלייה\n1. `21.1` א\n2. `21.2` ב ✅ נסגר\n### ג. לפני מכירה\n3. `21.23` ג\n")
R01 = "**פסיקה:** עוצר\n## מה נדרש\n2. `01.2` קבלה על כל תשלום\n"

with tempfile.TemporaryDirectory() as d:
    legal = pathlib.Path(d) / "משפטי"
    legal.mkdir()
    (legal / "00 - יומן.md").write_text(INDEX, encoding="utf-8")
    (legal / "21 - מכירה.md").write_text(R21, encoding="utf-8")
    (legal / "01 - עוסק.md").write_text(R01, encoding="utf-8")
    (pathlib.Path(d) / "מוצרים").mkdir()
    (pathlib.Path(d) / "מוצרים" / "00 - אינדקס.md").write_text("# אינדקס\n| מוצר | סטטוס |\n|---|---|\n| iDiskk | מועמד |\n", encoding="utf-8")
    om.board.fetch = lambda folder: (rows, P, {"58": "פסיקה 01, תנאי 01.2 — מכוסה ע\"י BOS-46"}, PROJECT)
    out = om.report("wellness", d)

check("milestone blockers split: can do now / waiting", "## מה חוסם את \"מכירה ראשונה\" (BOS-58) — 3: 1 אפשר לעשות עכשיו, 2 מחכות" in out
      and "### אפשר לעשות עכשיו — 1" in out and "- BOS-52 תנאי פסיקה 21 — לביצוע — Cowork — אין יעד\n### מחכות — 2" in out)
check("waiting says why: blocked status, or an open dependency", "BOS-50 מעקב ספק — חסום — בעז — 8.10.2026 — מחכה: סטטוס חסום" in out
      and "BOS-53 תנאי פסיקה 15 — לביצוע — בעז — אין יעד — מחכה: תלויה ב-BOS-50 (חסום)" in out)
check("a task lists who waits for it", "  - מחכות לה: BOS-53" in out)
check("dated tasks say whether they block, dates as Boaz writes them",
      "- BOS-50 מעקב ספק — חסום — בעז — 8.10.2026 — חוסמת את \"מכירה ראשונה\"" in out)
check("a dated task's description is whole, not cut", LONG.strip() in out)
check("every open ID, grouped by stage", "- א. לפני עלייה: 21.1" in out and "- ג. לפני מכירה: 21.23" in out and "סגורים: 21.2" in out)
check("where the board refers: task and milestone page", "בלוח: BOS-52" in out and "בלוח: דף אבן הדרך BOS-58" in out)
check("cloud-path ruling is in the recheck list", "- 21: נבדק מול: טיוטה — `/home/claude/x.html`" in out)
check("decisions are the project page section, as is", "- מחיר: לא נקבע.\n- תשלום: Bit בלבד." in out and "לא החלטה" not in out)
check("site tasks by repo", "## משימות פתוחות ב-repo site" in out and "BOS-26 טופס" in out.split("## משימות פתוחות ב-repo site")[1])
check("the product rows are quoted from the index", "## המוצרים" in out and "| iDiskk | מועמד |" in out)
check("the bottom line is computed, before the report", "**שורה תחתונה:**" in out
      and out.index("**שורה תחתונה:**") < out.index("## המוצרים") and "3 משימות חוסמות את \"מכירה ראשונה\" (BOS-58) — 1 אפשר לעשות עכשיו (Cowork: 1), 2 מחכות למשהו" in out
      and "היעד הקרוב בלוח: BOS-50 מעקב ספק (8.10.2026, חסום)." in out)
check("the contents list every section heading", "**בדוח:** המוצרים · מה חוסם את" in out
      and "משימות פתוחות שלא מחוברות לאף אבן דרך" in out.split("**בדוח:**")[1].split("\n")[0])
check("a done blocker is not counted, and is listed apart", "### כבר לא חוסמות (הושלמו או בוטלו) — 1\n- BOS-54 עוסק — הושלם" in out)
check("a task waiting on an idea still waits", "  - מחכה: תלויה ב-BOS-70 (רעיון)" in out)
check("tasks read by id are not listed as open tasks", "BOS-70 רעיון —" not in out and "\n- BOS-54" not in out.split("## כל המשימות")[1])

# An index that changed shape fails loudly instead of an empty legal section.
with tempfile.TemporaryDirectory() as d:
    (pathlib.Path(d) / "משפטי").mkdir()
    (pathlib.Path(d) / "משפטי" / "00 - יומן.md").write_text(INDEX.replace("| בתוקף |", "| בתוקף | הערה |"), encoding="utf-8")
    try:
        om.report("wellness", d)
        check("an index in an unknown format is refused", False)
    except RuntimeError as e:
        check("an index in an unknown format is refused", "לא בפורמט המוכר" in str(e))

# main: an error that carries the key never prints it.
fake = "ntn" + "_" + "Qq7" * 14
with tempfile.TemporaryDirectory() as d:
    kf = pathlib.Path(d) / "k"
    kf.write_text(fake, encoding="utf-8")
    om.board.find_token = lambda venture: kf
    om.board._KEY = None

    def boom(folder):
        om.board.key()
        raise ValueError(f"Invalid header value b'Bearer {fake}'")
    om.board.fetch = boom
    import contextlib, io  # noqa: E401,E402
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = om.main(["--folder", "w", "--venture", d])
    check("a failure exits 1 and the key is not in the message", code == 1 and fake not in err.getvalue() and "***" in err.getvalue())
    om.board._KEY = None

check("a ruling with no נבדק-מול line is listed", "- 01: אין בקובץ שורת \"נבדק מול\"" in out)

print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
