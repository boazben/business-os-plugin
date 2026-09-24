#!/usr/bin/env python3
"""The legal part of `business-os:orient`, computed by code instead of by reading.

Reads `משפטי/00 - יומן פסיקות…` (the index), and for every ruling that is in force (בתוקף
starts with "כן") prints, from the ruling file itself:
- its פסיקה / נבדק מול / האתר החי lines, verbatim;
- every condition item `NN.n` — open, ✅ closed or ↪ moved, read from the numbered line (that
  is where the closure and move marks go); a mark written only under the numbered line (older
  files) makes it "unclear", quoted — with the heading it sits under (the stage) and the first
  line of the item, verbatim; and closed items that have no ID;
- whether the ruling was checked against something other than the real thing (cloud path,
  draft, web sources only, or not checked).

Usage: conditions.py <venture folder>
Read-only. Prints Markdown. Exit 1 if the index is missing.
"""
import pathlib
import re
import sys

ITEM = re.compile(r"^\s*(?:\d+\.|[-*])\s+`(\d{2}ב?\.\d+)`\s*(.*)$")
HEADER = re.compile(r"^(?:>\s*)?\*{0,2}(פסיקה|נבדק מול|האתר החי)\s*:\s*\*{0,2}\s*(.*)$")
UNVERIFIED = re.compile(r"/home/claude|/mnt/user-data|טיוט|מקורות ברשת|לא נבדק")


def index_rows(legal_dir):
    """(number, file cell, in-force cell) for each row of the register table."""
    idx = next(iter(sorted(legal_dir.glob("00 - *.md"))), None)
    if idx is None:
        return None
    rows, header = [], None
    for line in idx.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            header = cells
            continue
        if set("".join(cells)) <= {"-", ":", " "}:
            continue
        if len(cells) == 4:
            rows.append((cells[0], cells[2], cells[3]))
    # A changed table must fail loudly: an empty legal section reads as "nothing open".
    if header != ["#", "נושא", "קובץ", "בתוקף"] or not rows:
        raise RuntimeError(f"יומן הפסיקות ({idx.name}) לא בפורמט המוכר (# | נושא | קובץ | בתוקף) — לא נקרא")
    return rows


def resolve(legal_dir, file_cell):
    m = re.search(r"`([^`]+\.md)`", file_cell)
    if not m:
        return None
    rel = m.group(1)
    if rel.startswith("יזמות-ארכיון/"):  # archive paths are relative to the folder above יזמות
        return legal_dir.parent.parent.parent / rel
    return legal_dir / rel


def is_condition_heading(h):
    return bool(re.search(r"תנאי|נדרש|נשאר לעשות", h)) or bool(re.match(r"^[אבג]\.\s", h))


def still_open(mark):
    """A ❌ line, or a ⚠️ line that says it isn't closed yet — the part of a condition still open."""
    return mark.startswith("❌") or (mark.startswith("⚠️") and "לא נסגר" in mark)


def status_of(first, marks):
    """Status from the numbered line and the ✅/❌/⚠️/↪ lines under it."""
    if "✅" in first:
        return "✅ נסגר"
    if "↪" in first:
        return "↪ הועבר"
    if any(still_open(m) for m in marks):
        return "פתוח — סגור חלקית"
    if any(m.startswith(("✅", "↪")) for m in marks):
        return "לא ברור — סימון סגירה מתחת לשורה הממוספרת, לא עליה"
    if "ייתכן שנסגר" in first:
        return "פתוח — ייתכן שנסגר (לבדיקה)"
    return "פתוח"


def parse_ruling(text):
    """Items are (id or None, status, heading, numbered line, quoted marks). Items without an ID
    are read only under a conditions heading (תנאים… / מה נדרש… / א. ב. ג.)."""
    headers, items, heading, current, in_mark = {}, [], "", None, False
    for line in text.splitlines():
        h = HEADER.match(line.strip())
        if h:  # the first is the ruling's own line; later ones are updates, quoted too
            headers.setdefault(h.group(1), []).append(h.group(2).replace("**", "").strip())
        if line.startswith("#"):
            heading, current = line.lstrip("#").strip(), None
            continue
        m = ITEM.match(line)
        noid = None if m else re.match(r"^\d+\.\s+(.*)$", line)
        if m or (noid and is_condition_heading(heading)):
            current = [m.group(1) if m else None, None, heading, (m.group(2) if m else noid.group(1)).strip(), []]
            items.append(current)
        elif current is not None and line[:1] in (" ", "\t") and line.strip():
            body = line.strip().lstrip("-* ").strip()
            if body.startswith(("✅", "↪", "❌", "⚠️")):
                current[4].append(body)
                in_mark = not line.strip().startswith(("-", "*"))  # a plain wrapped line may continue it
            elif in_mark and current[4] and not line.strip().startswith(("-", "*")):
                current[4][-1] += " " + body  # the mark's sentence runs onto the next line
            else:
                in_mark = False
        elif current is not None and line.strip():
            current = None  # a new unindented line ends the item
    for i in items:
        i[1] = status_of(i[3], i[4])
        if i[1].startswith("פתוח — סגור"):
            i[4] = [m for m in i[4] if still_open(m)]  # what is still open
        elif i[1].startswith("פתוח"):
            i[4] = []  # ⚠️ lines are notes (corrections, warnings), not open parts
    return headers, [tuple(i) for i in items]


def report(venture):
    legal_dir = pathlib.Path(venture) / "משפטי"
    rows = index_rows(legal_dir)
    if rows is None:
        return None
    out, unverified = [], []
    for num, file_cell, in_force in rows:
        if not in_force.startswith("כן"):
            continue
        path = resolve(legal_dir, file_cell)
        out.append(f"## משפטי/{num} — בתוקף: {in_force}")
        if path is None or not path.exists():
            out.append(f"- הקובץ לא נמצא ({file_cell}) — לא נקרא.\n")
            continue
        headers, all_items = parse_ruling(path.read_text(encoding="utf-8"))
        items = [i for i in all_items if i[0]]
        noid = [i for i in all_items if not i[0]]
        for key in ("פסיקה", "נבדק מול", "האתר החי"):
            vals = headers.get(key) or ["(אין שורה כזו בקובץ)"]
            out.append(f"- {key}: {vals[0]}")
            out += [f"  - עדכון בהמשך הקובץ: {v}" for v in vals[1:]]
        checked = (headers.get("נבדק מול") or [""])[-1]
        live = (headers.get("האתר החי") or [""])[-1]
        if not checked:
            unverified.append(f"- משפטי/{num}: אין בקובץ שורת \"נבדק מול\" — לא ידוע מול מה נבדקה")
        elif UNVERIFIED.search(checked):
            unverified.append(f"- משפטי/{num}: נבדק מול: {checked}")
        if live.startswith("לא נבדק"):
            unverified.append(f"- משפטי/{num}: האתר החי: {live}")
        open_items = [i for i in items if i[1].startswith("פתוח")]
        unclear = [i for i in items if i[1].startswith("לא ברור")]
        if not all_items:
            out.append("- תנאים: אין בקובץ תנאים ממוספרים — לקרוא את הקובץ.")
        for cid, status, heading, line, notes in open_items + unclear:
            out.append(f"- `{cid}` — {status} — תחת \"{heading}\" — {line}")
            label = "עדיין פתוח" if status.startswith("פתוח — סגור") else "לקרוא ולהכריע"
            out += [f"  - {label}: {n}" for n in notes]
        partial = bool(re.search(r"(^|\s)רק\s", in_force))  # in force only in part: its old items are not the conditions
        for _, status, heading, line, notes in ([] if partial else noid):
            if status.startswith("לא ברור"):
                out.append(f"- (בלי מזהה בקובץ) — {status} — תחת \"{heading}\" — {line}")
                out += [f"  - לקרוא ולהכריע: {n}" for n in notes]
        unmarked = [i for i in noid if i[1] in ("פתוח", "פתוח — ייתכן שנסגר (לבדיקה)", "פתוח — סגור חלקית")]
        if partial and noid:
            out.append(f"- בתוקף רק בחלקו — {len(noid)} הפריטים הממוספרים בקובץ לא נמנים כאן; "
                       "מה שבתוקף כתוב בעמודת \"בתוקף\" — לקרוא את הקובץ.")
        elif unmarked:
            out.append(f"- עוד {len(unmarked)} פריטים ממוספרים בלי מזהה ובלי סימון, תחת כותרת של תנאים — "
                       "לא נספרו ולא ידוע אם פתוחים; לקרוא את הקובץ.")
        for label, mark in (("סגורים", "✅"), ("הועברו", "↪")):
            ids = [i[0] for i in items if i[1].startswith(mark)]
            if ids:
                out.append(f"- {label}: {', '.join(ids)}")
        out += [f"- סגור, בלי מזהה בקובץ: {i[3]}" for i in noid if i[1].startswith("✅") and not partial]
        extra = f", {len(unclear)} לא ברורים" if unclear else ""
        out.append(f"- סה\"כ: {len(open_items)} פתוחים{extra} מתוך {len(items)} מזהים\n")
    out.append("## פסיקות שלא נבדקו מול הדבר האמיתי — צריך בדיקה מחדש לפני שסומכים עליהן")
    out += unverified or ["- אין"]
    return "\n".join(out) + "\n"


def open_rulings(venture):
    """{ruling number: (open or unclear IDs, their headings)} for rulings in force — for board.py."""
    legal_dir = pathlib.Path(venture) / "משפטי"
    out = {}
    for num, file_cell, in_force in index_rows(legal_dir) or []:
        path = resolve(legal_dir, file_cell)
        if not in_force.startswith("כן") or path is None or not path.exists():
            continue
        _, items = parse_ruling(path.read_text(encoding="utf-8"))
        still = [(i[0], i[2]) for i in items if i[0] and not i[1].startswith(("✅", "↪"))]
        if still:
            out[num] = still
    return out


def main(argv):
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    text = report(argv[0])
    if text is None:
        print(f"אין יומן פסיקות ב-{argv[0]}/משפטי — לא נקרא.", file=sys.stderr)
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
