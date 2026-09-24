#!/usr/bin/env python3
"""The board part of `business-os:orient`, computed by code instead of by reading.

Input (stdin): the JSON that the Notion view "פתוחות — לסוכנים (לא לשנות)" returned in `view`
mode — one result object, or a JSON list of them when there were several pages. Copy it as is.
Or {"view": <that>, "milestone_bodies": {"<milestone BOS number>": "<page text>"}} to include the
milestone pages' text, where the task that covers each ruling is written.

Usage: board.py <project page URL or id> [--venture <venture folder>]
       board.py --fetch --folder "<project folder name>" [--venture <venture folder>]
--fetch reads the board itself through the Notion API with the key in ~/.business-os/notion-token
(ids from ../notion-board/registry.json): the project (by its "תיקייה" field or name), its open
tasks, the milestone pages' text and the project page's text. Nothing is copied by the model.
With --venture it also lists every ruling in force with open conditions and where the board
refers to it (conditions.py, same folder) — the model doesn't compute that list.
Prints, for that project only:
- a check line: how many rows came in, and whether a page is missing (has_more);
- per milestone (status "אבן דרך"): every row in its "תלוי ב" — id, name, status, owner, due, repo —
  and the ones not in the view (closed, cancelled or an idea: fetch them);
- the next step: the open task that is not "חסום" with the earliest due date; blocked tasks with a
  due date on their own lines, with their "תוצאה" and "תיאור";
- open tasks linked to no milestone, marked when their text says "לא חוסם";
- open tasks per repo (site/code work).
Read-only. Exit 1 on input it can't read.
"""
import json
import pathlib
import re
import sys

UUID = re.compile(r"([0-9a-f]{32})")


def page_id(url):
    m = UUID.search((url or "").replace("-", ""))
    return m.group(1) if m else None


def rel(value):
    if not value:
        return []
    try:
        items = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return []
    return [page_id(u) for u in items if page_id(u)]


def load(text):
    data = json.loads(text)
    if isinstance(data, dict) and "view" in data:
        rows, more, _ = load(json.dumps(data["view"]))
        return rows, more, data.get("milestone_bodies") or {}
    pages = data if isinstance(data, list) else [data]
    rows, more = [], False
    for p in pages:
        rows += p.get("results", [])
        more = bool(p.get("has_more"))  # the last page decides
    return rows, more, {}


def day(iso):
    """2026-10-08 -> 8.10.2026: the way Boaz writes dates, so nobody re-types them (and gets 10.8)."""
    y, m, d = iso[:10].split("-")
    return f"{int(d)}.{int(m)}.{y}"


def fmt(r):
    due = day(r["date:יעד:start"]) if r.get("date:יעד:start") else "אין יעד"
    repo = f" · repo {r['repo']}" if r.get("repo") else ""
    return f"BOS-{r.get('מזהה', '?')} {r.get('שם', '')} — {r.get('סטטוס', '')} — {r.get('מי מבצע') or 'אין מבצע'} — {due}{repo}"


def refs(text, num):
    """Does this text name ruling `num` — "פסיקה 08", "פסיקות 08 ו-13", "משפטי/08", "תנאי 08.3", `08.3`,
    "(= 11.2)"? Bare "08.10" is not enough: it is also how dates are written."""
    n = re.escape(num)
    kw = rf"(?:פסיק\S*|משפטי/|קוב\S*|תנא\S*)\s*(?:\d{{2}}\S*\s*(?:ו-|,|/)\s*)*{n}(?![\d])"
    return bool(re.search(kw, text) or re.search(rf"`{n}\.\d+`|=\s*{n}\.\d+", text))


def rulings_section(mine, stones, linked, bodies, open_by_ruling):
    out = ["\n## פסיקות בתוקף עם תנאים פתוחים — איפה הלוח מפנה אליהן (מחושב, לא לשנות)"]
    fields = ("שם", "תיאור", "מקור", "תוצאה")
    for num, items in open_by_ruling.items():
        note = " (כולם תחת כותרת \"לא חוסם\")" if all("לא חוסם" in h for _, h in items) else ""
        where = []
        for s in stones:
            body = bodies.get(str(s.get("מזהה"))) or ""
            if body and refs(body, num):
                where.append(f"גוף אבן הדרך BOS-{s.get('מזהה')}")
        for r in mine:
            hit = [f for f in fields if refs(str(r.get(f) or ""), num)]
            if hit and r.get("סטטוס") != "אבן דרך":
                tag = "מחוברת לאבן דרך" if page_id(r.get("url")) in linked else "לא מחוברת לאבן דרך"
                where.append(f"BOS-{r.get('מזהה')} ({', '.join(hit)}; {tag})")
        read = "ובגוף דף אבן הדרך" if bodies else "(גוף דף אבן הדרך לא הועבר ולא נבדק)"
        out.append(f"- פסיקה {num}: {len(items)} תנאים לא סגורים{note} — "
                   + ("; ".join(where) if where else
                      f"לא נמצאה הפניה בשדות המשימות הפתוחות {read}; גוף הדפים של המשימות לא נבדק"))
    return out


def report(rows, more, project, bodies=None, open_by_ruling=None):
    bodies = bodies or {}
    pid = page_id(project)
    mine = [r for r in rows if pid in rel(r.get("פרויקט"))]
    by_id = {page_id(r.get("url")): r for r in rows}
    out = [f"שורות שהתקבלו: {len(rows)} (בפרויקט: {len(mine)})"
           + (" — **חסר דף: has_more=true, להמשיך עם next_cursor ולהריץ שוב**" if more else " — כל הדפים")]
    stones = [r for r in mine if r.get("סטטוס") == "אבן דרך"]
    linked = set()
    for s in stones:
        deps = rel(s.get("תלוי ב"))
        linked.update(deps)
        out.append(f"\n## אבן דרך: {s.get('שם')} (BOS-{s.get('מזהה')}) — תלויה ב-{len(deps)}")
        for d in deps:
            r = by_id.get(d)
            out.append(f"- {fmt(r)}" if r else f"- {d} — לא בתצוגה (נסגר, בוטל או רעיון) — fetch לדף לפני שאומרים משהו עליו")
    tasks = [r for r in mine if r.get("סטטוס") != "אבן דרך"]

    dated = sorted((r for r in tasks if r.get("date:יעד:start")), key=lambda r: r["date:יעד:start"])
    free = [r for r in dated if r.get("סטטוס") != "חסום"]
    out.append("\n## הצעד הבא (המשימה הפתוחה שאינה חסומה, עם היעד המוקדם ביותר)")
    out.append(f"- {fmt(free[0])}\n  - תיאור: {free[0].get('תיאור', '')}" if free else "- אין משימה פתוחה עם יעד שאינה חסומה")
    out.append("\n## כל המשימות עם יעד, לפי תאריך")
    for r in dated:
        line = f"- {fmt(r)}"
        if r.get("סטטוס") == "חסום":
            line += f"\n  - חסומה: {r.get('תוצאה') or '(אין תוצאה)'}\n  - תיאור: {r.get('תיאור') or '(אין תיאור)'}"
        out.append(line)

    out.append("\n## משימות פתוחות שלא מחוברות לאף אבן דרך")
    loose = [r for r in tasks if page_id(r.get("url")) not in linked]
    for r in loose:
        text = f"{r.get('תיאור', '')} {r.get('תוצאה', '')}"
        mark = " — כתוב בה 'לא חוסם'" if "לא חוסם" in text else ""
        out.append(f"- {fmt(r)}{mark}")
    if not loose:
        out.append("- אין")

    if open_by_ruling is not None:
        out += rulings_section(mine, stones, linked, bodies, open_by_ruling)

    repos = sorted({r["repo"] for r in tasks if r.get("repo")})
    for repo in repos:
        out.append(f"\n## משימות פתוחות ב-repo {repo}")
        for r in tasks:
            if r.get("repo") == repo:
                where = " — מחוברת לאבן דרך" if page_id(r.get("url")) in linked else ""
                out.append(f"- {fmt(r)}{where}\n  - תיאור: {r.get('תיאור', '')}")
    return "\n".join(out) + "\n"


TOKEN = pathlib.Path.home() / ".business-os" / "notion-token"
API = "https://api.notion.com/v1"


def find_token(venture=None):
    """The Notion key file: ~/.business-os/notion-token on the computer; in a Cowork session the
    project folder's .business-os/notion-token, staged into the cloud like the Gemini key. The key is
    only ever read by this module — never printed."""
    places = [pathlib.Path.home() / ".business-os" / "notion-token"]
    if venture:
        places.append(pathlib.Path(venture) / ".business-os" / "notion-token")
    places += sorted(pathlib.Path("/mnt/user-data/uploads").glob("*/.business-os/notion-token"))
    return next((p for p in places if p.is_file()), None)


_KEY = None


def key():
    """The key, read once and strictly: a file with a BOM, a label line or two keys fails here, with
    no value in the message — before it can reach an HTTP header whose error would print it."""
    global _KEY
    if _KEY is None:
        t = TOKEN.read_text(encoding="utf-8-sig").strip()
        if not re.fullmatch(r"(ntn_|secret_)[A-Za-z0-9]+", t):
            raise RuntimeError(f"{TOKEN.name}: הקובץ לא במבנה של מפתח Notion (הערך לא מוצג)")
        _KEY = t
    return _KEY


def redact(text):
    """Any error text that leaves this module goes through here."""
    text = str(text)
    return text.replace(_KEY, "***") if _KEY else text


def api(method, path, body=None):
    import time
    import urllib.error
    import urllib.request
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + key(),
                                          "Notion-Version": "2025-09-03", "Content-Type": "application/json"})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 1:  # rate limit: wait as told, once
                time.sleep(min(float(e.headers.get("Retry-After") or 1), 10))
                continue
            try:
                err = json.loads(e.read())
                detail = f"{err.get('code')} — {err.get('message')}"
            except ValueError:
                detail = e.reason
            raise RuntimeError(f"Notion {e.code} ב-{method} {path.split('?')[0]}: {detail}") from None


def full_relation(page, prop):
    """A relation cut at 25 ids in the page object, read whole through the property endpoint."""
    ids, cursor = [], None
    while True:
        d = api("GET", f"/pages/{page}/properties/{prop}?page_size=100" + (f"&start_cursor={cursor}" if cursor else ""))
        ids += [x["relation"]["id"] for x in d.get("results", []) if x.get("type") == "relation"]
        if not d.get("has_more"):
            return ids
        cursor = d["next_cursor"]


def plain(prop):
    """A REST property -> the value the view JSON would have shown."""
    t = prop.get("type")
    v = prop.get(t)
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text", "") for x in v or [])
    if t == "select":
        return v.get("name") if v else None
    if t == "unique_id":
        return str(v.get("number")) if v else None
    if t == "relation":
        return json.dumps(["https://app.notion.com/p/" + x["id"].replace("-", "") for x in v or []])
    if t == "date":
        return v
    return None


def flat(page):
    row = {"url": "https://app.notion.com/p/" + page["id"].replace("-", "")}
    for name, prop in page["properties"].items():
        if prop.get("type") == "relation" and prop.get("has_more"):
            prop = {"type": "relation", "relation": [{"id": i} for i in full_relation(page["id"], prop["id"])]}
        val = plain(prop)
        if prop.get("type") == "date":
            if val:
                row[f"date:{name}:start"] = val.get("start")
        elif val is not None:
            row[name] = val
    return row


def query_all(ds, body):
    rows, cursor = [], None
    while True:
        b = dict(body, page_size=100, **({"start_cursor": cursor} if cursor else {}))
        d = api("POST", f"/data_sources/{ds}/query", b)
        rows += d["results"]
        if not d.get("has_more"):
            return rows
        cursor = d["next_cursor"]


def page_text(pid, depth=0):
    lines, cursor = [], None
    while True:
        d = api("GET", f"/blocks/{pid}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else ""))
        for blk in d["results"]:
            t = blk["type"]
            if t == "table_row":
                cells = ["".join(x.get("plain_text", "") for x in c) for c in blk["table_row"].get("cells", [])]
                lines.append("  " * depth + "| " + " | ".join(cells) + " |")
                continue
            rich = (blk.get(t) or {}).get("rich_text") or []
            text = "".join(x.get("plain_text", "") for x in rich)
            prefix = "#" * int(t[-1]) + " " if t.startswith("heading_") else "- " if t.endswith("list_item") else ""
            if text:
                lines.append("  " * depth + prefix + text)
            if t in ("child_page", "child_database"):
                lines.append("  " * depth + f"[דף משנה: {(blk.get(t) or {}).get('title', '')} — לא נקרא]")
            elif blk.get("has_children") and (depth < 2 or t == "table"):
                lines += page_text(blk["id"], depth + 1)
            elif blk.get("has_children"):  # say so rather than drop it
                lines.append("  " * (depth + 1) + "[תוכן מקונן נוסף — לא נקרא]")
        if not d.get("has_more"):
            return lines
        cursor = d["next_cursor"]


def fetch(folder):
    """-> (rows, project url, milestone bodies, project page text). Raises on any API failure."""
    reg = json.loads((pathlib.Path(__file__).resolve().parent.parent / "notion-board" / "registry.json").read_text(encoding="utf-8"))
    projects = [flat(p) for p in query_all(reg["projects"]["data_source_id"], {})]
    match = [p for p in projects if folder in (p.get("תיקייה"), p.get("שם"))]
    if len(match) != 1:
        raise RuntimeError(f"פרויקט עם תיקייה או שם \"{folder}\": נמצאו {len(match)}")
    project = match[0]["url"]
    flt = {"and": [{"property": "סטטוס", "select": {"does_not_equal": v}} for v in ("הושלם", "בוטל", "רעיון")]}
    rows = [flat(p) for p in query_all(reg["tasks"]["data_source_id"], {"filter": flt})]
    # A dependency that is done, cancelled or an idea is not in the query: read it by id, so a blocker
    # marked done stops being counted, and a task waiting on an idea still waits. Marked, so the
    # report never lists it as an open task.
    have = {page_id(r["url"]) for r in rows}
    for d in sorted({d for r in rows for d in rel(r.get("תלוי ב"))} - have):
        rows.append(dict(flat(api("GET", f"/pages/{d}")), **{"מחוץ לתצוגה": True}))
    pid = page_id(project)
    bodies = {r.get("מזהה"): "\n".join(page_text(page_id(r["url"])))
              for r in rows if r.get("סטטוס") == "אבן דרך" and pid in rel(r.get("פרויקט")) and not r.get("מחוץ לתצוגה")}
    return rows, project, bodies, "\n".join(page_text(pid))


def main(argv):
    opts, pos, i = {}, [], 0
    while i < len(argv):
        if argv[i] in ("--venture", "--folder"):
            if i + 1 >= len(argv):
                print(__doc__, file=sys.stderr)
                return 2
            opts[argv[i]] = argv[i + 1]
            i += 2
        elif argv[i] == "--fetch":
            opts["--fetch"] = True
            i += 1
        else:
            pos.append(argv[i])
            i += 1
    venture = opts.get("--venture")
    project_text = None
    if opts.get("--fetch"):
        if not opts.get("--folder") or pos:
            print(__doc__, file=sys.stderr)
            return 2
        global TOKEN
        TOKEN = find_token(opts.get("--venture"))
        if TOKEN is None:
            print("אין מפתח Notion — להשתמש בתצוגה (בלי --fetch).", file=sys.stderr)
            return 1
        try:
            rows, project, bodies, project_text = fetch(opts["--folder"])
        except Exception as exc:  # network, HTTP, a missing project: say so, compute nothing
            print(f"הלוח לא זמין ({type(exc).__name__}: {redact(exc)}) — המצב לא ידוע, לא חושב דבר.", file=sys.stderr)
            return 1
        rows = [r for r in rows if not r.get("מחוץ לתצוגה")]  # this older view report lists open rows only
        more = False
        pos = [project]
    elif len(pos) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    else:
        try:
            rows, more, bodies = load(sys.stdin.read())
        except (ValueError, AttributeError) as exc:
            print(f"הקלט הוא לא ה-JSON של התצוגה ({exc}) — לא חושב דבר.", file=sys.stderr)
            return 1
    argv = pos
    open_by_ruling = None
    if venture:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        import conditions
        open_by_ruling = conditions.open_rulings(venture)
    sys.stdout.write(report(rows, more, argv[0], bodies, open_by_ruling))
    cut = [r for r in rows if any(k.endswith(":חתוך") for k in r)]
    if cut:
        print(f"\n**אזהרה:** ב-{len(cut)} שורות רשימת קשרים חתוכה (יותר מ-25) — לקרוא את הדף עצמו.")
    if project_text is not None:
        sys.stdout.write("\n## דף הפרויקט (גוף הדף, כמו שהוא)\n" + project_text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
