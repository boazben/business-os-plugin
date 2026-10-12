#!/usr/bin/env python3
"""business-os:tidy — keeps the venture folders and the machine from bloating again.

The folder grew to ~1,450 files before a single sale (12.10.2026): every step wrote a new
file next to the old one and nothing removed anything. This script only counts and archives;
it never decides what is current.

  tidy.py                 one 🧹 line when something bloated, nothing when clean (SessionStart)
  tidy.py --hook          the same line, from a cache younger than CACHE_HOURS (the venture sits on
                          a Windows drive and a full scan takes ~10s — too slow for every session start)
  tidy.py --report        every finding, with paths
  tidy.py archive <path> --why "<reason>" [--by "<what replaces it>"]
                          move a file or folder of a venture to its archive and log it

Ventures: the "ventures" list in ~/.business-os/signals.json, plus --venture <path>.
Also deletes ~/.business-os/tmp/<run> folders older than TMP_DAYS — that is the one place
for temporary work, and a run that forgot to clean up gets cleaned here.
Read-only apart from that and `archive`.
"""
import argparse
import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

HOME = pathlib.Path(os.path.expanduser("~"))
BOS = HOME / ".business-os"
TMP_DAYS = 2
CACHE_HOURS = 6
CACHE = BOS / "tidy-last.json"
FILE_BUDGET = 700          # files in a venture, outside _מערכת
SKIP_DIRS = {"_מערכת", ".git", "node_modules", ".business-os", "__pycache__"}

# A name that says "I am one version of something": v2, v5.1, סבב 2, גרסה 3, (טיוטה), (הצעה)...
MARKERS = re.compile(
    r"\s*(?:[-–(]\s*)?(?:\bv\d+(?:\.\d+)*\b|סבב\s*\d+|גרסה\s*\d+|טיוטה|טיוטא|הצעה|ארכיון להשוואה|"
    r"לפני תיקוני[^)]*|copy|עותק)\s*\)?",
    re.I)
DATE = re.compile(r"\(?\d{1,2}\.\d{1,2}\.\d{4}\)?|\d{4}-\d{2}-\d{2}")


def stem_key(name):
    """The name with version markers and dates removed — two siblings with the same key are versions."""
    base = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", name)
    base = MARKERS.sub(" ", base)
    base = DATE.sub(" ", base)
    base = re.sub(r"[\s\-–_,()]+", " ", base).strip().lower()
    return base


def versioned(name):
    return bool(MARKERS.search(re.sub(r"\.[A-Za-z0-9]{1,5}$", "", name)))


def ventures(extra=()):
    paths = list(extra)
    try:
        paths += json.loads((BOS / "signals.json").read_text(encoding="utf-8")).get("ventures", [])
    except (OSError, ValueError):
        pass
    return [pathlib.Path(p) for p in dict.fromkeys(paths) if pathlib.Path(p).is_dir()]


def walk(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        yield pathlib.Path(dirpath), dirnames, filenames


def version_groups(root):
    """Folders holding two or more siblings that are versions of one thing (at least one carries a marker)."""
    found = []
    for d, dirs, files in walk(root):
        groups = {}
        for n in dirs + files:
            groups.setdefault(stem_key(n), []).append(n)
        for key, names in groups.items():
            if key and len(names) > 1 and any(versioned(n) for n in names):
                found.append((d.relative_to(root), sorted(names)))
    return found


def legal_dir(root):
    for name in ("משפטי", "legal"):
        if (root / name).is_dir():
            return root / name
    return None


def unindexed_legal(root):
    """Files in the legal folder's top level that the index (00 - …) does not name."""
    ld = legal_dir(root)
    if not ld:
        return []
    idx = next(iter(sorted(ld.glob("00 - *.md"))), None)
    text = idx.read_text(encoding="utf-8") if idx else ""
    return sorted(p.name for p in ld.iterdir()
                  if p.is_file() and p != idx and p.suffix == ".md" and p.name not in text)


def file_count(root):
    return sum(len(f) for _, _, f in walk(root))


def leftovers(root):
    out = []
    t = root / "_מערכת" / "transfer"
    if t.is_dir() and any(t.iterdir()):
        out.append(str(t.relative_to(root)))
    return out


def home_leftovers():
    """Work folders a run left in the home folder (~/bos-*, ~/*-work, ~/*-backup)."""
    return sorted(p.name for p in HOME.iterdir()
                  if p.is_dir() and not p.name.startswith(".")
                  and re.match(r"^(bos-.+|.+-work|.+-state-backup|.+-probe)$", p.name))


def merged_branches(repo):
    r = subprocess.run(["git", "-C", str(repo), "branch", "--merged", "main", "--format=%(refname:short)"],
                       capture_output=True, text=True)
    if r.returncode:
        return []
    cur = subprocess.run(["git", "-C", str(repo), "branch", "--show-current"], capture_output=True, text=True).stdout.strip()
    return [b for b in r.stdout.split() if b not in ("main", cur)]


def stale_worktrees(repo):
    r = subprocess.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"], capture_output=True, text=True)
    if r.returncode:
        return []
    merged = set(merged_branches(repo))
    out, path = [], None
    for line in r.stdout.splitlines():
        if line.startswith("worktree "):
            path = line[9:]
        elif line.startswith("branch refs/heads/") and line[18:] in merged and path and pathlib.Path(path) != pathlib.Path(repo):
            out.append(path)
    return out


def repos():
    found = [HOME / "business-os-plugin"]
    if (HOME / "repos").is_dir():
        found += [p for p in (HOME / "repos").iterdir() if (p / ".git").is_dir()]
    return [p for p in found if (p / ".git").exists()]


def clean_tmp(now=None, base=None):
    """Delete ~/.business-os/tmp/<run> folders untouched for TMP_DAYS. Returns their names."""
    base = base or BOS / "tmp"
    now = now or time.time()
    gone = []
    if not base.is_dir():
        return gone
    for p in base.iterdir():
        newest = max([p.stat().st_mtime] + [q.stat().st_mtime for q in p.rglob("*")]) if p.is_dir() else p.stat().st_mtime
        if now - newest > TMP_DAYS * 86400:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
            gone.append(p.name)
    return gone


def findings(venture_paths, budget=FILE_BUDGET, home=True):
    out = []
    for v in venture_paths:
        tag = v.name
        groups = version_groups(v)
        if groups:
            sample = "; ".join(f"{d}/{{{', '.join(n)}}}" if str(d) != "." else "{" + ", ".join(n) + "}"
                               for d, n in groups[:3])
            out.append((f"{tag}: {len(groups)} קבוצות גרסאות זו לצד זו", sample))
        bad = unindexed_legal(v)
        if bad:
            out.append((f"{tag}: {len(bad)} קבצים במשפטי בלי שורה באינדקס", ", ".join(bad[:5])))
        n = file_count(v)
        if n > budget:
            out.append((f"{tag}: {n} קבצים (תקרה {budget})", "מה שהוחלף — לארכיון"))
        for left in leftovers(v):
            out.append((f"{tag}: שאריות העברה ב-{left}", "מוחקים אחרי שההעברה הסתיימה"))
    if home:
        h = home_leftovers()
        if h:
            out.append((f"תיקיות עבודה בבית: {len(h)}", ", ".join(h)))
        for r in repos():
            wt = stale_worktrees(r)
            if wt:
                out.append((f"{r.name}: {len(wt)} worktrees על ענף שמוזג", ", ".join(wt)))
            mb = merged_branches(r)
            if len(mb) > 3:
                out.append((f"{r.name}: {len(mb)} ענפים שמוזגו", ", ".join(mb[:6])))
    return out


def archive_root(venture):
    """<…>/יזמות/<venture> → <…>/יזמות-ארכיון/<venture>."""
    return venture.parent.parent / (venture.parent.name + "-ארכיון") / venture.name


def archive(target, why, by="", venture=None, today=None):
    target = pathlib.Path(target).resolve()
    venture = venture or next((v for v in ventures() if v in target.parents), None)
    if venture is None:
        sys.exit(f"{target} לא בתוך תיקיית venture מוכרת (signals.json או --venture)")
    if not target.exists():
        sys.exit(f"{target} לא קיים")
    today = today or datetime.date.today().isoformat()
    rel = target.relative_to(venture)
    dest = archive_root(venture) / f"{today} - הוחלף" / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        sys.exit(f"כבר קיים בארכיון: {dest}")
    count = sum(1 for p in target.rglob("*") if p.is_file()) if target.is_dir() else 1
    shutil.move(str(target), str(dest))
    log = archive_root(venture) / "יומן העברות.md"
    if not log.exists():
        log.write_text(f"# יומן העברות לארכיון — {venture.name}\n\n"
                       "| תאריך | מה | מאיפה | לאן | למה | במקומו |\n|---|---|---|---|---|---|\n", encoding="utf-8")
    cell = lambda s: str(s).replace("|", "/").replace("\n", " ")
    row = (f"| {today} | {cell(rel)} ({count} קבצים) | {cell(venture.name + '/' + str(rel))} | "
           f"{cell(dest.relative_to(archive_root(venture).parent.parent))} | {cell(why)} | {cell(by or '—')} |\n")
    with log.open("a", encoding="utf-8") as fh:
        fh.write(row)
    return dest


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("cmd", nargs="?", choices=["archive"])
    ap.add_argument("path", nargs="?")
    ap.add_argument("--why")
    ap.add_argument("--by", default="")
    ap.add_argument("--venture", action="append", default=[])
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--hook", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "archive":
        if not a.path or not a.why:
            ap.error("archive <path> --why <reason>")
        target = pathlib.Path(a.path).resolve()
        dest = archive(target, a.why, a.by, next((v for v in ventures(a.venture) if v in target.parents), None))
        print(f"הועבר: {dest}")
        return 0
    if a.hook:
        try:
            cached = json.loads(CACHE.read_text(encoding="utf-8"))
            if time.time() - cached["time"] < CACHE_HOURS * 3600:
                if cached["line"]:
                    print(cached["line"])
                return 0
        except (OSError, ValueError, KeyError, TypeError):
            pass
    try:
        gone = clean_tmp()
    except OSError:
        gone = []
    found = findings(ventures(a.venture))
    if a.report:
        for title, detail in found:
            print(f"- {title}\n  {detail}")
        if gone:
            print(f"- נמחקו תיקיות זמניות ישנות: {', '.join(gone)}")
        if not found:
            print("נקי.")
        return 0
    line = ("🧹 ניקיון: " + " · ".join(t for t, _ in found)
            + " — לפני העבודה: `business-os:tidy` (מה שהוחלף לארכיון, בלי לשאול את המייסד).") if found else ""
    if line:
        print(line)
    try:
        CACHE.write_text(json.dumps({"time": time.time(), "line": line}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
