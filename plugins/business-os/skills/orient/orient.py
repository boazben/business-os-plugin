#!/usr/bin/env python3
"""The whole "where are we" report of `business-os:orient`, in one command, computed by code.

Usage: orient.py --folder "<project folder name>" --venture "<project folder path>"

Reads the board through the Notion API (board.py --fetch: the key in ~/.business-os/notion-token,
or <venture>/.business-os/notion-token when the folder was staged into a Cowork session) and the
ruling files (conditions.py), and prints one short report whose every section is complete:
what blocks each milestone, the next step and every dated task (with its description), every open
legal condition by ID and stage with where the board refers to it, the rulings not checked against
the real thing, the founder's standing decisions, the site tasks and the tasks linked to no
milestone — under a computed bottom line (what can be done now and what waits) and list of sections. This is the model's source: the
answer to "where are we" is a few lines of conclusions drawn from it, not the report itself.
Read-only. Exit 1 if the board or the rulings can't be read — nothing is guessed.
"""
import datetime
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
CLOSED = ("הושלם", "בוטל")
sys.path.insert(0, str(HERE))
import board  # noqa: E402
import conditions  # noqa: E402


def cut(text, n=140):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def section(lines, title):
    """The lines under a '## title' heading of the project page, up to the next '## '."""
    out, inside = [], False
    for line in lines:
        if re.match(r"#{1,2} ", line):  # the section ends at the next heading of its level or above
            inside = line.lstrip("#").strip() == title and line.startswith("## ")
            continue
        if inside:
            out.append(line)
    return out


def legal(venture, rows, project_url, bodies):
    legal_dir = pathlib.Path(venture) / "משפטי"
    index = conditions.index_rows(legal_dir)
    if index is None:
        raise RuntimeError(f"אין יומן פסיקות ב-{legal_dir}")
    pid = board.page_id(project_url)
    mine = [r for r in rows if pid in board.rel(r.get("פרויקט"))]
    stones = [r for r in mine if r.get("סטטוס") == "אבן דרך"]
    out, unchecked = [], []
    for num, file_cell, in_force in index:
        state = in_force.strip("*✅ ")
        if not state.startswith(("כן", "לא", "—", "-")):
            out.append(f"- פסיקה {num} — **לא ברור אם בתוקף** (\"{cut(in_force, 60)}\") — לא נקרא")
            continue
        if not state.startswith("כן"):
            continue
        path = conditions.resolve(legal_dir, file_cell)
        if path is None or not path.exists():
            out.append(f"- פסיקה {num} — בתוקף — **הקובץ לא נמצא, לא נקרא** ({cut(file_cell, 80)})")
            continue
        headers, items = conditions.parse_ruling(path.read_text(encoding="utf-8"))
        verdict = (headers.get("פסיקה") or ["(אין שורת פסיקה בקובץ)"])[-1]
        checked = (headers.get("נבדק מול") or [""])[-1]
        live = (headers.get("האתר החי") or [""])[-1]
        if not checked:
            unchecked.append(f"- {num}: אין בקובץ שורת \"נבדק מול\"")
        elif conditions.UNVERIFIED.search(checked):
            unchecked.append(f"- {num}: נבדק מול: {checked}")
        if live.startswith("לא נבדק"):
            unchecked.append(f"- {num}: האתר החי: {live}")
        ided = [i for i in items if i[0]]
        still = [i for i in ided if not i[1].startswith(("✅", "↪"))]
        where = []
        for s in stones:
            if board.refs(bodies.get(str(s.get("מזהה"))) or "", num):
                where.append(f"דף אבן הדרך BOS-{s.get('מזהה')}")
        where += [f"BOS-{r.get('מזהה')}" for r in mine if r.get("סטטוס") != "אבן דרך"
                  and any(board.refs(str(r.get(f) or ""), num) for f in ("שם", "תיאור", "מקור", "תוצאה"))]
        partial = bool(re.search(r"(^|\s)רק\s", in_force))
        head = (f"- **פסיקה {num}** — בתוקף: {in_force} — פסיקה: {verdict} — "
                f"{len(still)} לא סגורים מתוך {len(ided)} מזהים — בלוח: {', '.join(where) or 'לא נמצאה הפניה בשדות ובדף אבן הדרך'}")
        out.append(head)
        by_heading = {}
        for cid, status, heading, _, notes in still:
            tag = ""
            if status.startswith("פתוח — ייתכן"):
                tag = " (ייתכן שנסגר)"
            elif status.startswith("פתוח — סגור חלקית"):
                tag = f" (סגור חלקית — עוד פתוח: {' / '.join(notes)})"
            elif status.startswith("לא ברור"):
                tag = f" (לא ברור — סומן מתחת לשורה: {notes[0] if notes else ''})"
            by_heading.setdefault(heading, []).append(cid + tag)
        for heading, ids in by_heading.items():
            out.append(f"  - {cut(heading, 60)}: {', '.join(ids)}")
        closed = [i[0] for i in ided if i[1].startswith("✅")]
        moved = [i[0] for i in ided if i[1].startswith("↪")]
        if closed:
            out.append(f"  - סגורים: {', '.join(closed)}")
        if moved:
            out.append(f"  - הועברו: {', '.join(moved)}")
        noid = [i for i in items if not i[0]]
        if partial and noid:
            out.append("  - בתוקף רק בחלקו — הפריטים הישנים בקובץ לא נמנים; מה שבתוקף כתוב בעמודת \"בתוקף\"")
        elif noid:
            unclear = [i for i in noid if i[1].startswith("לא ברור")]
            closed_noid = [i for i in noid if i[1].startswith("✅")]
            rest = len(noid) - len(unclear) - len(closed_noid)
            for i in unclear:
                out.append(f"  - בלי מזהה, לא ברור: {cut(i[3], 90)} — סומן מתחת: {i[4][0] if i[4] else ''}")
            if closed_noid:
                out.append(f"  - {len(closed_noid)} סגורים בלי מזהה")
            if rest:
                out.append(f"  - {rest} פריטים בלי מזהה ובלי סימון — לא נספרו; לקרוא את הקובץ")
        if not items:
            out.append("  - אין בקובץ תנאים ממוספרים — לקרוא את הקובץ")
    return out, unchecked


def report(folder, venture):
    every, project, bodies, project_text = board.fetch(folder)
    rows = [r for r in every if not r.get("מחוץ לתצוגה")]  # the open tasks; the rest are dependencies read by id
    pid = board.page_id(project)
    mine = [r for r in rows if pid in board.rel(r.get("פרויקט"))]
    by_id = {board.page_id(r.get("url")): r for r in every}
    stones = [r for r in mine if r.get("סטטוס") == "אבן דרך"]
    tasks = [r for r in mine if r.get("סטטוס") != "אבן דרך"]
    linked = set()
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = [f"# מצב {folder} — נכון ל-{now}",
           f"(מחושב בקוד: {len(mine)} שורות פתוחות בלוח, קובצי הפסיקה, דף הפרויקט)"]

    idx = next(iter(sorted((pathlib.Path(venture) / "מוצרים").glob("00 - *.md"))), None)
    out.append("\n## המוצרים — השורות מאינדקס המוצרים, כמו שהן")
    if idx is None:
        out.append("- אין אינדקס מוצרים — לא נקרא")
    else:
        table = [l for l in idx.read_text(encoding="utf-8").splitlines() if l.startswith("|")]
        out += table or ["- אין טבלה באינדקס — לקרוא את הקובץ"]

    def waits_for(r):
        """Why an open task can't be worked on now: it is חסום, or it depends on a task not done yet
        (an idea counts: nothing starts before what it depends on is done)."""
        why = ["סטטוס חסום"] if r.get("סטטוס") == "חסום" else []
        why += [f"תלויה ב-BOS-{by_id[d].get('מזהה')} ({by_id[d].get('סטטוס')})"
                for d in board.rel(r.get("תלוי ב"))
                if d in by_id and by_id[d].get("סטטוס") not in CLOSED + ("אבן דרך",)]
        why += [f"תלויה בדף שלא נמצא ({d})" for d in board.rel(r.get("תלוי ב")) if d not in by_id]
        return why

    waiters = {}
    for r in tasks:
        for d in board.rel(r.get("תלוי ב")):
            waiters.setdefault(d, []).append(f"BOS-{r.get('מזהה')}")

    def details(r):
        """Under a task: who waits for it, why it is blocked, and its whole description — the model
        retold rules and dependencies wrongly when they weren't printed where it was reading."""
        lines = []
        if waiters.get(board.page_id(r.get("url"))):
            lines.append(f"  - מחכות לה: {', '.join(waiters[board.page_id(r.get('url'))])}")
        if r.get("סטטוס") == "חסום" and r.get("תוצאה"):
            lines.append(f"  - חסומה: {cut(r['תוצאה'], 10_000)}")
        lines.append(f"  - תיאור: {cut(r.get('תיאור') or '(אין תיאור)', 10_000)}")
        return lines

    summary = []
    for s in stones:
        deps = board.rel(s.get("תלוי ב"))
        linked.update(deps)
        open_deps = [by_id[d] for d in deps if d in by_id and by_id[d].get("סטטוס") not in CLOSED]
        done = [by_id[d] for d in deps if d in by_id and by_id[d].get("סטטוס") in CLOSED]
        now_ = [r for r in open_deps if not waits_for(r)]
        wait = [r for r in open_deps if waits_for(r)]
        gone = [d for d in deps if d not in by_id]
        owners = {}
        for r in now_:
            owners[r.get("מי מבצע") or "אין מבצע"] = owners.get(r.get("מי מבצע") or "אין מבצע", 0) + 1
        split = ", ".join(f"{k}: {v}" for k, v in owners.items())
        summary.append(f"{len(open_deps)} משימות חוסמות את \"{s.get('שם')}\" (BOS-{s.get('מזהה')}) — "
                       f"{len(now_)} אפשר לעשות עכשיו ({split}), {len(wait)} מחכות למשהו")
        out.append(f"\n## מה חוסם את \"{s.get('שם')}\" (BOS-{s.get('מזהה')}) — {len(open_deps)}: "
                   f"{len(now_)} אפשר לעשות עכשיו, {len(wait)} מחכות")
        out.append(f"### אפשר לעשות עכשיו — {len(now_)} (לא חסומות ולא תלויות במשימה פתוחה)")
        out += [f"- {board.fmt(r)}" for r in now_] or ["- אין"]
        out.append(f"### מחכות — {len(wait)}")
        for r in wait:
            out.append(f"- {board.fmt(r)} — מחכה: {'; '.join(waits_for(r))}")
            out += details(r)
        out += [] if wait else ["- אין"]
        if done:
            out.append(f"### כבר לא חוסמות (הושלמו או בוטלו) — {len(done)}")
            out += [f"- {board.fmt(r)}" for r in done]
        for d in gone:
            out.append(f"- {d} — הדף לא נמצא — לבדוק בלוח")

    dated = sorted((r for r in tasks if r.get("date:יעד:start")), key=lambda r: r["date:יעד:start"])
    out.append("\n## כל המשימות עם יעד, לפי תאריך")
    for r in dated:
        blocks = [f"חוסמת את \"{s.get('שם')}\"" for s in stones
                  if board.page_id(r.get("url")) in board.rel(s.get("תלוי ב"))]
        out.append(f"- {board.fmt(r)} — {', '.join(blocks) or 'לא חוסמת אבן דרך'}")
        if waits_for(r):
            out.append(f"  - מחכה: {'; '.join(waits_for(r))}")
        out += details(r)

    lines, unchecked = legal(venture, rows, project, bodies)
    lines = lines or ["- אין פסיקות בתוקף"]
    out.append("\n## תנאים משפטיים לא סגורים — כל מזהה, לפי הכותרת (השלב) בקובץ הפסיקה")
    out += lines
    out.append("\n## פסיקות שלא נבדקו מול הדבר האמיתי — צריך בדיקה מחדש לפני שסומכים עליהן")
    out += unchecked or ["- אין"]

    decisions = section(project_text.splitlines(), "החלטות מרכזיות")
    out.append("\n## החלטות עומדות — מדף הפרויקט, כמו שהן")
    out += decisions or ["- אין בדף הפרויקט סעיף \"החלטות מרכזיות\""]

    for repo in sorted({r["repo"] for r in tasks if r.get("repo")}):
        out.append(f"\n## משימות פתוחות ב-repo {repo}")
        for r in tasks:
            if r.get("repo") == repo:
                where = " — חוסמת אבן דרך" if board.page_id(r.get("url")) in linked else ""
                out.append(f"- {board.fmt(r)}{where}")
                out.append(f"  - תיאור: {cut(r.get('תיאור'), 200)}")

    out.append("\n## משימות פתוחות שלא מחוברות לאף אבן דרך")
    loose = [r for r in tasks if board.page_id(r.get("url")) not in linked]
    for r in loose:
        text = f"{r.get('תיאור', '')} {r.get('תוצאה', '')}"
        out.append(f"- {board.fmt(r)}" + (" — כתוב בה 'לא חוסם'" if "לא חוסם" in text else ""))
    out += [] if loose else ["- אין"]
    # The bottom line and the contents are computed too: every line a model wrote above the
    # report in the tests was where the mistakes were.
    bottom = summary[:] or ["אין אבן דרך פתוחה בפרויקט"]
    if dated:
        r = dated[0]
        bottom.append(f"היעד הקרוב בלוח: BOS-{r.get('מזהה')} {r.get('שם')} ({board.day(r['date:יעד:start'])}, {r.get('סטטוס')})")
    heads = [l.lstrip("\n")[3:].split(" — ")[0] for l in out if l.lstrip("\n").startswith("## ")]
    top = [f"**שורה תחתונה:** {'; '.join(bottom)}.",
           "**בדוח:** " + " · ".join(heads)]
    return "\n".join(out[:2] + [""] + top + out[2:]) + "\n"


def main(argv):
    opts = dict(zip(argv[::2], argv[1::2])) if len(argv) % 2 == 0 else {}
    if set(opts) != {"--folder", "--venture"}:
        print(__doc__, file=sys.stderr)
        return 2
    board.TOKEN = board.find_token(opts["--venture"])
    if board.TOKEN is None:
        print("אין מפתח Notion (~/.business-os/notion-token, או .business-os/notion-token בתיקיית הפרויקט) — "
              "לא חושב דבר. להשתמש בדרך הידנית של ה-skill.", file=sys.stderr)
        return 1
    try:
        sys.stdout.write(report(opts["--folder"], opts["--venture"]))
    except Exception as exc:  # network, HTTP, no project, no register: say so, compute nothing
        print(f"לא חושב דבר — {type(exc).__name__}: {board.redact(exc)}. המצב לא ידוע.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
