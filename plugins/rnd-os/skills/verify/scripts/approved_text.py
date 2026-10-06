#!/usr/bin/env python3
"""Approved wording vs the pages — the check behind legal-lead's one-line ruling (legal-lead.md, gate step 7).

The legal drafter marks each approved wording in the drafts file as a fenced block:

    ```approved id=50א-4.1
    <the exact text, as it must appear on the page>
    ```

A pass needs all three:
  1. every block's text appears, word for word (whitespace, markdown and HTML entities aside), in the visible
     text of the pages (*.html under the folder, tags, scripts and templates stripped); an empty block fails;
  2. every block belongs to a ruling in force: the number its id starts with (50 in 50א-4.1) has a row in the
     legal register (`00 - …md` in --legal-dir) whose last column starts with "כן". A drafter's block that no
     ruling approved is a draft ("קובץ בתיקייה הוא טיוטה") — it doesn't count;
  3. with --base-pages: every text segment that is new on the pages since the base is inside some block.
     New text that no block covers is exactly what a one-line ruling must not wave through.

  approved_text.py <pages dir> <drafts file> [...] --legal-dir DIR [--base-pages DIR]

It proves equality, not truth: whether the approved text is still true (a product fact changed since) is the
question legal-lead still asks. Exit 0 pass · 1 fail · 2 could not check (no blocks, no register).
"""
import argparse
import html
import pathlib
import re
import sys

BLOCK = re.compile(r"^```approved\s+id=(\S+)\s*\n(.*?)\n?```", re.M | re.S)
BLOCK_TAGS = r"p|li|h[1-6]|td|th|dt|dd|label|button|a|div|section|summary|figcaption|caption|option|br|tr|header|footer|nav"


def norm(text):
    text = html.unescape(text).replace(" ", " ")
    text = re.sub(r"\*\*|__|`", "", text)
    return re.sub(r"\s+", " ", text).strip()


def page_segments(path):
    """The page's visible text, one segment per block element."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    raw = re.sub(r"(?is)<(script|style|template)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<!--.*?-->", " ", raw)
    raw = re.sub(rf"(?i)</?(?:{BLOCK_TAGS})\b[^>]*>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return [s for s in (norm(x) for x in raw.split("\n")) if s]


def pages_of(folder):
    return {p.name: page_segments(p) for p in sorted(pathlib.Path(folder).glob("*.html"))}


def in_force(legal_dir):
    """Ruling numbers whose row in the register says בתוקף: כן…"""
    idx = next(iter(sorted(pathlib.Path(legal_dir).glob("00 - *.md"))), None)
    if idx is None:
        return None
    nums = set()
    for line in idx.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and re.fullmatch(r"\d{2}[א-ת]?", cells[0]) and cells[-1].startswith("כן"):
            nums.add(cells[0][:2])
    return nums


def check(pages_dir, drafts, legal_dir=None, base_pages=None):
    """{'found','missing','empty','not_in_force','uncovered','register'}"""
    pages = pages_of(pages_dir)
    text = {name: " ".join(segs) for name, segs in pages.items()}
    force = in_force(legal_dir) if legal_dir else None
    r = {"found": [], "missing": [], "empty": [], "not_in_force": [], "uncovered": [], "register": force is not None}
    blocks = []
    for d in drafts:
        for bid, body in BLOCK.findall(pathlib.Path(d).read_text(encoding="utf-8")):
            want = norm(body)
            if not want:
                r["empty"].append(bid)
                continue
            blocks.append(want)
            where = [name for name, t in text.items() if want in t]
            (r["found"] if where else r["missing"]).append((bid, where, want[:120]))
            num = re.match(r"\d{2}", bid)
            if force is not None and (not num or num.group(0) not in force):
                r["not_in_force"].append(bid)
    if base_pages is not None:
        old = {s for segs in pages_of(base_pages).values() for s in segs}
        for name, segs in pages.items():
            for s in segs:
                if s not in old and not any(s in b for b in blocks):
                    r["uncovered"].append((name, s[:120]))
    return r


def verdict(r):
    """(status, why) — pass only when nothing is missing, empty, unapproved or uncovered, and the register was read."""
    if not (r["found"] or r["missing"] or r["empty"]):
        return "not run", "no ```approved id=…``` blocks in the drafts given"
    if not r["register"]:
        return "not run", "ruling in force not checked — pass --legal-dir (the folder with `00 - …` register)"
    bad = ([f"missing {b}" for b, _, _ in r["missing"]] + [f"empty {b}" for b in r["empty"]]
           + [f"ruling not in force: {b}" for b in r["not_in_force"]]
           + [f"new text not in any block ({n}): «{s}»" for n, s in r["uncovered"][:10]])
    return ("fail", "; ".join(bad)) if bad else ("pass", f"{len(r['found'])} blocks identical, in force, no uncovered new text")


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("pages")
    ap.add_argument("drafts", nargs="+")
    ap.add_argument("--legal-dir")
    ap.add_argument("--base-pages")
    a = ap.parse_args(argv[1:])
    status, why = verdict(check(a.pages, a.drafts, a.legal_dir, a.base_pages))
    print(f"נוסח מאושר: {status} — {why}")
    return {"pass": 0, "fail": 1}.get(status, 2)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
