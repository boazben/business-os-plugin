"""plugins/business-os/skills/orient/board.py: the board part of orient, computed from the view JSON.

Run: python3 scripts/tests/test_board.py
"""
import importlib.util
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "plugins/business-os/skills/orient/board.py"
spec = importlib.util.spec_from_file_location("board", SCRIPT)
bd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bd)
failures = []


def check(name, cond):
    if not cond:
        failures.append(name)
    print(("ok   " if cond else "FAIL ") + name)


P = "https://app.notion.com/p/" + "a" * 32
OTHER = "https://app.notion.com/p/" + "b" * 32
u = lambda n: "https://app.notion.com/p/" + f"{n:032d}"
rows = [
    {"url": u(58), "מזהה": "58", "שם": "מכירה ראשונה", "סטטוס": "אבן דרך", "פרויקט": json.dumps([P]),
     "תלוי ב": json.dumps([u(50), u(51), u(99)])},
    {"url": u(50), "מזהה": "50", "שם": "מעקב ספק", "סטטוס": "לביצוע", "מי מבצע": "בעז", "date:יעד:start": "2026-10-08",
     "פרויקט": json.dumps([P]), "תיאור": "ענו — בודקים"},
    {"url": u(51), "מזהה": "51", "שם": "בדיקת יחידה", "סטטוס": "לביצוע", "מי מבצע": "בעז", "date:יעד:start": "2026-10-10",
     "פרויקט": json.dumps([P])},
    {"url": u(42), "מזהה": "42", "שם": "סוללה", "סטטוס": "חסום", "מי מבצע": "בעז", "date:יעד:start": "2026-10-02",
     "פרויקט": json.dumps([P]), "תוצאה": "חסום: אין קופסה. לא חוסם מכירה"},
    {"url": u(26), "מזהה": "26", "שם": "טופס הזמנה", "סטטוס": "לביצוע", "מי מבצע": "Claude Code", "repo": "shahut-site",
     "פרויקט": json.dumps([P]), "תיאור": "טופס אמיתי"},
    {"url": u(7), "מזהה": "7", "שם": "של פרויקט אחר", "סטטוס": "לביצוע", "פרויקט": json.dumps([OTHER])},
]
out = bd.report(rows, False, P)
check("counts rows and project rows", "שורות שהתקבלו: 6 (בפרויקט: 5) — כל הדפים" in out)
check("milestone lists its dependencies with owner", "- BOS-50 מעקב ספק — לביצוע — בעז — 8.10.2026" in out)
check("a dependency not in the view says so", f"{'0'*30}99 — לא בתצוגה" in out)
check("next step skips a blocked task", out.split("## הצעד הבא")[1].split("##")[0].count("BOS-50") == 1
      and "BOS-42" not in out.split("## הצעד הבא")[1].split("##")[0])
check("a blocked task with a due date shows why", "BOS-42 סוללה — חסום" in out and "חסומה: חסום: אין קופסה" in out)
dated = out.split("## כל המשימות עם יעד")[1].split("\n## ")[0]
check("dated tasks are sorted", dated.index("BOS-42 סוללה") < dated.index("BOS-50 מעקב") < dated.index("BOS-51 בדיקת"))
check("a task linked to no milestone is listed, with its 'לא חוסם' mark",
      "BOS-42 סוללה" in out.split("שלא מחוברות")[1] and "כתוב בה 'לא חוסם'" in out)
check("tasks per repo are listed", "## משימות פתוחות ב-repo shahut-site" in out and "BOS-26 טופס הזמנה" in out.split("## משימות פתוחות ב-repo shahut-site")[1])
check("another project's task is left out", "של פרויקט אחר" not in out)
check("a missing page is flagged", "חסר דף" in bd.report(rows, True, P))
two = json.dumps([{"results": rows[:3], "has_more": True}, {"results": rows[3:], "has_more": False}])
check("several pages are joined", bd.load(two) == (rows, False, {}))
check("a blocked task also shows its description", "BOS-42 סוללה" in out and out.count("- תיאור:") >= 2)
check("refs: named ruling", bd.refs("לסגור את התנאים בפסיקות 08 ו-13", "13") and bd.refs("משפטי/01 - רישום", "01"))
check("refs: a date is not a ruling", not bd.refs("היחידה תגיע ב-10.10", "10"))
check("refs: = NN.n and backtick ids count", bd.refs("10.2 (= 11.2): לוגו", "11") and bd.refs("ראו `21.7`", "21"))
wrapped = json.dumps({"view": {"results": rows, "has_more": False}, "milestone_bodies": {"58": "פסיקה 01, תנאי 01.2 — מכוסה ע\"י BOS-46"}})
rw, mw, bw = bd.load(wrapped)
sec = bd.report(rw, mw, P, bw, {"01": [("01.2", "מה נדרש")], "05": [("05.1", "מה נשאר לעשות (לא חוסם)")], "09": [("09.1", "תנאים")]})
check("a ruling covered in the milestone body is found", "פסיקה 01: 1 תנאים לא סגורים — גוף אבן הדרך BOS-58" in sec)
check("a ruling whose conditions are all 'לא חוסם' says so and is still looked up",
      "פסיקה 05: 1 תנאים לא סגורים (כולם תחת כותרת \"לא חוסם\") — לא נמצאה" in sec)
page = {"id": "3e529cc4-3683-8183-b518-dafea351cd0c", "properties": {
    "שם": {"type": "title", "title": [{"plain_text": "מכירה "}, {"plain_text": "ראשונה"}]},
    "סטטוס": {"type": "select", "select": {"name": "אבן דרך"}},
    "מזהה": {"type": "unique_id", "unique_id": {"prefix": "BOS", "number": 58}},
    "יעד": {"type": "date", "date": {"start": "2026-10-10"}},
    "תלוי ב": {"id": "dep", "type": "relation", "relation": [{"id": "3e529cc4-3683-81ca-8ce8-d898c7c826f7"}], "has_more": True},
    "repo": {"type": "select", "select": None}}}
A, B = "3e529cc4-3683-81ca-8ce8-d898c7c826f7", "3e529cc4-3683-81ca-8ce8-d898c7c82600"
FAKE = {  # the Notion API, page by page, for the calls the code makes
    "/pages/3e529cc4-3683-8183-b518-dafea351cd0c/properties/dep?page_size=100":
        {"results": [{"type": "relation", "relation": {"id": A}}], "has_more": True, "next_cursor": "c2"},
    "/pages/3e529cc4-3683-8183-b518-dafea351cd0c/properties/dep?page_size=100&start_cursor=c2":
        {"results": [{"type": "relation", "relation": {"id": B}}], "has_more": False},
    "/blocks/P/children?page_size=100": {"results": [
        {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "החלטות מרכזיות"}]}},
        {"type": "table", "table": {}, "has_children": True, "id": "T"},
        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "מחיר"}]}, "has_children": True, "id": "L1"},
    ], "has_more": True, "next_cursor": "p2"},
    "/blocks/P/children?page_size=100&start_cursor=p2": {"results": [
        {"type": "child_page", "child_page": {"title": "נספח"}}], "has_more": False},
    "/blocks/T/children?page_size=100": {"results": [
        {"type": "table_row", "table_row": {"cells": [[{"plain_text": "מחיר"}], [{"plain_text": "לא נקבע"}]]}}], "has_more": False},
    "/blocks/L1/children?page_size=100": {"results": [
        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "רמה 2"}]}, "has_children": True, "id": "L2"}], "has_more": False},
    "/blocks/L2/children?page_size=100": {"results": [
        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "רמה 3"}]}, "has_children": True, "id": "L3"}], "has_more": False},
}
real_api = bd.api
bd.api = lambda method, path, body=None: FAKE[path]
f = bd.flat(page)
check("REST page -> the same flat row as the view",
      f["שם"] == "מכירה ראשונה" and f["סטטוס"] == "אבן דרך" and f["מזהה"] == "58"
      and f["date:יעד:start"] == "2026-10-10" and "repo" not in f)
check("a relation cut at 25 is read whole, across pages",
      bd.rel(f["תלוי ב"]) == [A.replace("-", ""), B.replace("-", "")])
text = bd.page_text("P")
check("page text: pages followed, tables kept, deeper nesting and sub-pages said, not dropped",
      "## החלטות מרכזיות" in text and "  | מחיר | לא נקבע |" in text and "    - רמה 3" in text
      and "      [תוכן מקונן נוסף — לא נקרא]" in text and "[דף משנה: נספח — לא נקרא]" in text)
bd.api = real_api

# The key: read strictly, never in an error message.
import tempfile  # noqa: E402
fake = "ntn" + "_" + "Zz9" * 14
with tempfile.TemporaryDirectory() as d:
    kf = pathlib.Path(d) / "k"
    kf.write_text("﻿" + fake + "\n", encoding="utf-8")
    bd.TOKEN, bd._KEY = kf, None
    check("a key file with a BOM and a newline is read", bd.key() == fake)
    check("an error message with the key in it is redacted", fake not in bd.redact(f"x Bearer {fake} y"))
    kf.write_text(fake + "\n" + fake + "\n", encoding="utf-8")
    bd._KEY = None
    try:
        bd.key()
        check("a key file with two lines is refused", False)
    except RuntimeError as e:
        check("a key file with two lines is refused, without the value", fake not in str(e))
    bd._KEY = None
r = subprocess.run([sys.executable, str(SCRIPT), "--fetch"], capture_output=True, text=True)
check("--fetch without --folder is a usage error", r.returncode == 2)
check("an unreferenced ruling is reported as not found, with what was read", "פסיקה 09: 1 תנאים לא סגורים — לא נמצאה הפניה" in sec and "ובגוף דף אבן הדרך; גוף הדפים של המשימות לא נבדק" in sec)
r = subprocess.run([sys.executable, str(SCRIPT), P], input="not json", capture_output=True, text=True)
check("bad input exits 1 and says nothing was computed", r.returncode == 1 and "לא חושב" in r.stderr)

print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
