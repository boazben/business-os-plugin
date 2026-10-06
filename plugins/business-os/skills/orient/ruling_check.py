#!/usr/bin/env python3
"""Structure check for a legal ruling that takes conditions from another — the part of legal-verifier §4
("שינוי מבנה בלבד") that code can do, so the verifier only reads what really changed.

  ruling_check.py <old ruling> <new ruling>

Each condition is read whole: its numbered line and the indented lines under it, and the stage heading it
sits under (א. לפני פרסום / ב. לפני השקה …). Checks:
- every `↪` in the old ruling names a target that can be read (`ל-`, `ל־` or `ל`, then an ID), and the target
  exists in the new ruling. A `↪` whose target can't be read is a problem, not a skip;
- a condition carried with different words — on any of its lines — or under a different stage heading is
  listed for legal-verifier (a dropped clause changes the meaning, V49-1 was exactly that; a stage change can
  turn a blocking condition into a later one);
- the new ruling has no duplicate condition IDs (changelog sections that quote IDs aside);
- a `מחליף את:` header: if it names the old ruling, every condition still open there must be resolved (↪ or
  ✅) — one left open and the old ruling stays in force; if it names something else, that is reported, because
  then this check can't tell whether the old ruling leaves force.
Read-only. Exit: 0 structure and wording hold · 1 a structural problem · 3 structure holds, but wording or
stage changed (send those to legal-verifier).
"""
import collections
import importlib.util
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("conditions", HERE / "conditions.py")
assert _spec and _spec.loader
conditions = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(conditions)

ID = r"\d{2}[א-ת]?\.\d+"
MOVED = re.compile(r"↪\s*הועבר\s+ל[-־]?\s*`?(" + ID + r")`?\s*(?:\([^)]*\))?\.?\s*")
CHANGELOG = re.compile(r"השתנה|עדכון|היסטוריה|אימות")
REPLACES = re.compile(r"(?m)^(?:>\s*)?\**מחליף את:\**\s*(.+)$")


def items(text):
    """[(id, stage heading, whole text, first line)] — the numbered line plus its indented continuation."""
    out, heading, cur = [], "", None
    for line in text.splitlines():
        if line.startswith("#"):
            heading, cur = line.lstrip("#").strip(), None
            continue
        m = conditions.ITEM.match(line)
        if m:
            cur = [m.group(1), heading, [m.group(2)], m.group(2)]
            out.append(cur)
        elif cur is not None and line[:1] in (" ", "\t") and line.strip():
            cur[2].append(line.strip())
        elif cur is not None and line.strip():
            cur = None
    return [(i, h, " ".join(body), first) for i, h, body, first in out]


def wording(text):
    """The condition's own words: without the move/close marks, markdown and spacing."""
    text = MOVED.sub("", text, count=1)
    text = re.sub(r"\*\*|`", "", text)
    return re.sub(r"\s+", " ", text).strip()


def stage(heading):
    m = re.match(r"^([א-ת])\.\s", heading)
    return m.group(1) if m else heading


def word_diff(a, b):
    aw, bw = a.split(), b.split()
    ca, cb = collections.Counter(aw), collections.Counter(bw)
    gone = [w for w in aw if ca[w] > cb.get(w, 0)]
    added = [w for w in bw if cb[w] > ca.get(w, 0)]
    return " ".join(dict.fromkeys(gone))[:200], " ".join(dict.fromkeys(added))[:200]


def check(old_path, new_path):
    old_text = pathlib.Path(old_path).read_text(encoding="utf-8")
    new_text = pathlib.Path(new_path).read_text(encoding="utf-8")
    old_no = pathlib.Path(old_path).stem.split(" ")[0]  # "49" from "49 - …"
    problems, changed, notes = [], [], []
    new_by_id = collections.defaultdict(list)
    for cid, heading, whole, _first in items(new_text):
        if not CHANGELOG.search(heading):
            new_by_id[cid].append((heading, whole))
    problems += [f"מזהה כפול בפסיקה החדשה: {cid} ({len(v)} פעמים)" for cid, v in new_by_id.items() if len(v) > 1]
    headers = [h.strip() for h in REPLACES.findall(new_text)]
    replaces = any(re.search(rf"(?<!\d){re.escape(old_no)}\s*-", h) for h in headers)
    if headers and not replaces:
        problems.append(f"'מחליף את:' מפנה ל-{'; '.join(headers)[:120]} ולא לפסיקה {old_no} — אי אפשר לדעת אם היא יוצאת מתוקף")
    _, parsed = conditions.parse_ruling(old_text)
    status = {cid: st for cid, st, *_ in parsed if cid}
    for cid, heading, whole, first in items(old_text):
        own = cid.split(".")[0] == old_no  # 45.2 listed in 49 is 45's condition, quoted; it stays in force there
        if "↪" in first:
            m = MOVED.search(first)
            if not m:
                problems.append(f"{cid}: ↪ בלי יעד שאפשר לקרוא («{first[:80]}») — לכתוב `↪ הועבר ל-<מזהה>`")
                continue
            target = m.group(1)
            if target not in new_by_id:
                problems.append(f"{cid} ↪ {target}: אין {target} בפסיקה החדשה — התנאי נעלם")
                continue
            new_heading, new_whole = new_by_id[target][0]
            a, b = wording(whole), wording(new_whole)
            if a != b:
                gone, added = word_diff(a, b)
                changed.append(f"{cid} → {target}: הנוסח השתנה במעבר. ירד: «{gone}» · נוסף: «{added}»")
            elif stage(heading) != stage(new_heading):
                changed.append(f"{cid} → {target}: עבר שלב — «{heading[:50]}» → «{new_heading[:50]}»")
            else:
                notes.append(f"{cid} → {target}: הועבר בלי שינוי נוסח ובאותו שלב")
        elif replaces and own and status.get(cid, "פתוח").startswith(("פתוח", "לא ברור")):
            problems.append(f"{cid}: פתוח בפסיקה שמוחלפת, ולא סומן ↪ או ✅ — הפסיקה הישנה לא יוצאת מתוקף")
    return problems, changed, notes, replaces


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    problems, changed, notes, replaces = check(argv[1], argv[2])
    print(f"# בדיקת מבנה: {pathlib.Path(argv[1]).name} → {pathlib.Path(argv[2]).name}"
          f" ({'מחליפה' if replaces else 'לא מחליפה'})")
    for title, rows in (("בעיות מבנה", problems), ("נוסח או שלב שהשתנו — ל-legal-verifier", changed), ("תקין", notes)):
        if rows:
            print(f"\n## {title}\n" + "\n".join(f"- {r}" for r in rows))
    if not (problems or changed or notes):
        print("\nאין תנאים שעברו בין הפסיקות.")
    return 1 if problems else 3 if changed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
