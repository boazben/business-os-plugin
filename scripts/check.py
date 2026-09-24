#!/usr/bin/env python3
"""Repo checks that must pass before a commit and before a persona copy is written.

1. Every gate anchor is still in the persona (after its leading '> ' note, which the copies
   drop) — the anchors of HEAD, of ~/.business-os/trusted-anchors.txt (kept by sync-persona;
   a pull or merge cannot change it) and of the new version together, so a commit cannot drop
   a rule by dropping its anchor too. Removing an anchor needs the founder's OK, recorded in
   ~/.business-os/approved-anchor-removals.txt.
2. Read instructions rewritten in BOS-49 do not go back to English venture paths
   (the venture folders are Hebrew; an English path reads nothing, or a whole folder).

Usage: check.py [--staged] [--persona FILE]
  --staged   check what is being committed (the git index), not the working tree
  --persona  check this persona file instead of CEO-PERSONA.md
Exit 1 with one line per failure.
"""
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# (file, forbidden text) — only the lines BOS-49 rewrote. Output folders
# (design/…, legal/drafts/…) are a separate, later migration and are not checked.
READ_PATH_DENYLIST = [
    ("plugins/legal-os/agents/legal-quick-check.md", "legal/register.md"),
    ("plugins/legal-os/agents/legal-drafter.md", "תקרא את `legal/register.md`"),
    ("plugins/legal-os/agents/legal-lead.md", "וגם את `rnd/`, `marketing/` ו-`design/`"),
    ("plugins/design-os/agents/visual-designer.md", "design/brand/brand-book.md"),
    ("plugins/design-os/agents/design-critic.md", "(`design/brand/brand-book.md`)"),
    ("plugins/design-os/agents/design-critic.md", "מקור השוואה: design/brand/brand-book.md"),
    ("plugins/design-os/agents/design-lead.md", "תקרא את `design/brand/`"),
    ("plugins/marketing-os/agents/brand-guardian.md", "`design/reviews/` ואל תבדוק"),
    ("plugins/rnd-os/agents/rnd-lead.md", "`context/`"),
    ("plugins/business-os/skills/run-board/SKILL.md", "context/03"),
]


def norm(text):
    return re.sub(r"\s+", " ", text)


MIN_ANCHORS = 10
APPROVED = pathlib.Path(os.path.expanduser("~")) / ".business-os" / "approved-anchor-removals.txt"
TRUSTED = pathlib.Path(os.path.expanduser("~")) / ".business-os" / "trusted-anchors.txt"
COPIES = [pathlib.Path(os.path.expanduser("~")) / d / "CLAUDE.md" for d in ("repos", "ventures")]


def parse(text):
    text = (text or "").replace("\ufeff", "")  # a BOM from a Windows editor
    return [l.strip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]


def strip_note(text):
    """Drop the leading '> ' note block, as sync-persona does when it writes the copies."""
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and lines[i].startswith(">"):
        i += 1
    return "".join(lines[i:])


def git_show(spec):
    r = subprocess.run(["git", "-C", str(ROOT), "show", spec], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def trusted_text():
    """sync-persona's trusted list; if it was deleted or emptied, the list of the version named in
    the last generated copy's footer (the same fallback the sync uses)."""
    if TRUSTED.exists():
        text = TRUSTED.read_text(encoding="utf-8", errors="replace")
        if len(parse(text)) >= MIN_ANCHORS:
            return text
    for c in COPIES:
        try:
            body = c.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.findall(r"^גרסת פרסונה: ([0-9a-f]{7,40})\s*$", body, re.M)
        if body.startswith("<!-- נוצר אוטומטית") and m:
            return git_show(f"{m[-1]}:scripts/persona-anchors.txt") or ""
    return ""


def anchors(text=None):
    if text is None:
        text = (ROOT / "scripts" / "persona-anchors.txt").read_text(encoding="utf-8")
    return parse(text)


def check_persona(persona_text, anchors_text=None, base_text=None, approved_text=""):
    new = anchors(anchors_text)
    fails = []
    if len(new) < MIN_ANCHORS:
        fails.append(f"persona: anchor list is empty or shorter than {MIN_ANCHORS}")
    ok = {norm(a) for a in parse(approved_text)}
    dropped = [a for a in parse(base_text) if norm(a) not in {norm(x) for x in new} and norm(a) not in ok]
    fails += [f"persona: gate anchor removed without the founder's recorded OK ({APPROVED}): {a}" for a in dropped]
    body = norm(strip_note(persona_text))
    seen = {norm(a) for a in new}
    union = new + [a for a in dict.fromkeys(parse(base_text)) if norm(a) not in seen and norm(a) not in ok]
    fails += [f"persona: gate anchor missing: {a}" for a in union if norm(a) not in body]
    return fails


def check_read_paths():
    fails = []
    for rel, bad in READ_PATH_DENYLIST:
        path = ROOT / rel
        if path.exists() and bad in path.read_text(encoding="utf-8"):
            fails.append(f"{rel}: English venture read path is back: {bad}")
    return fails


def main(argv):
    staged = "--staged" in argv
    argv = [a for a in argv if a != "--staged"]
    persona_path = None
    if len(argv) == 2 and argv[0] == "--persona":
        persona_path = pathlib.Path(argv[1])
    elif argv:
        print(__doc__, file=sys.stderr)
        return 2
    if persona_path:
        persona = persona_path.read_text(encoding="utf-8")
        anchors_text = None
    elif staged:
        persona = git_show(":CEO-PERSONA.md") or ""
        anchors_text = git_show(":scripts/persona-anchors.txt") or ""
    else:
        persona = (ROOT / "CEO-PERSONA.md").read_text(encoding="utf-8")
        anchors_text = None
    base = (git_show("HEAD:scripts/persona-anchors.txt") or "") + "\n" + trusted_text()
    approved = APPROVED.read_text(encoding="utf-8", errors="replace") if APPROVED.exists() else ""
    fails = check_persona(persona, anchors_text, base, approved) + check_read_paths()
    for f in fails:
        print(f"check: {f}", file=sys.stderr)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
