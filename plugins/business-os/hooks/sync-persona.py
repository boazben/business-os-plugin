#!/usr/bin/env python3
"""SessionStart: rebuild the CEO-persona CLAUDE.md copies from main, and flag stale plugins.

Why at session start and not on commit: most persona edits come from Claude Code
cloud sessions and reach this machine through `git pull`, which never runs the
repo's post-commit hook — the copies silently went a week stale that way. So the
check happens when the copies are about to be read.

What it does (WSL/Linux only; exits quietly anywhere else, e.g. Cowork's cloud):
1. Fetches main from GitHub over HTTPS (5 s limit). Offline -> uses the local main.
2. Takes CEO-PERSONA.md from that commit, drops the leading technical note, and checks
   every gate anchor in the text that is actually written — the UNION of the trusted anchors
   and the fetched commit's, so a commit cannot drop a rule by dropping its anchor too.
   The trusted list is ~/.business-os/trusted-anchors.txt, which only this hook writes, after
   a version passed; `git pull`/`git merge` cannot change it. Missing or emptied, it is rebuilt
   from the version named in the last generated copy's footer — never from local main, which
   a pull can change (local main only on the very first run, before any copy was generated).
   An anchor removed upstream needs the founder's OK, recorded in
   ~/.business-os/approved-anchor-removals.txt (one anchor per line); an approval is used up
   once the removal is synced. A version that fails keeps the existing copies.
3. Writes ~/repos/CLAUDE.md and ~/ventures/CLAUDE.md atomically, with a generated-file
   header and a trailing "גרסת פרסונה: <sha>" line, only when the content changed.
4. Reports (one line each): the local repo is behind origin (the marketplace installs
   from the local tree, so a pull is needed); an installed plugin whose version differs
   from the repo (update + restart); plugin content changed without a version bump.
Output goes to stdout, which Claude Code adds to the session's context.
"""
import json
import os
import pathlib
import platform
import re
import signal
import subprocess
import sys
import tempfile

HOME = pathlib.Path(os.path.expanduser("~"))
REPO = pathlib.Path(os.environ.get("BOS_PLUGIN_REPO", HOME / "business-os-plugin"))
REMOTE_URL = "https://github.com/boazben/business-os-plugin.git"
TARGETS = [HOME / "repos" / "CLAUDE.md", HOME / "ventures" / "CLAUDE.md"]
MARKETPLACE = "business-os-marketplace"
HEADER = ("<!-- נוצר אוטומטית מ-CEO-PERSONA.md ב-business-os-plugin ({sha}) בפתיחת שיחה. "
          "לא לערוך כאן — עורכים את המקור בריפו. -->\n\n")


def git(*args, timeout=10):
    """Run git in its own process group, so a timeout also kills its helpers
    (git-remote-https would otherwise outlive a stalled fetch)."""
    p = subprocess.Popen(["git", "-C", str(REPO), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        p.communicate()
        raise
    if p.returncode != 0:
        raise RuntimeError(err.strip() or f"git {' '.join(args)} failed")
    return out


def strip_note(text):
    """Drop the leading '> ' technical note block (the copies never carried it)."""
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and lines[i].startswith(">"):
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    return "".join(lines[i:]) if i else text


def norm(s):
    return re.sub(r"\s+", " ", s)


MIN_ANCHORS = 10
APPROVED_REMOVALS = HOME / ".business-os" / "approved-anchor-removals.txt"
TRUSTED_ANCHORS = HOME / ".business-os" / "trusted-anchors.txt"
FETCH_REF = "refs/bos/remote-main"  # private ref: parallel sessions and the user's own fetches don't share FETCH_HEAD


def parse_anchors(text):
    text = (text or "").replace("\ufeff", "")  # a BOM from a Windows editor
    return [l.strip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]


def missing_anchors(persona, anchors_text):
    body = norm(persona)
    return [a for a in parse_anchors(anchors_text) if norm(a) not in body]


def removed_anchors(base_text, new_text, approved_text=""):
    """Anchors present in the trusted base but gone from the new list, minus founder-approved ones."""
    new = {norm(a) for a in parse_anchors(new_text)}
    ok = {norm(a) for a in parse_anchors(approved_text)}
    return [a for a in parse_anchors(base_text) if norm(a) not in new and norm(a) not in ok]


def build_copy(persona, sha):
    return HEADER.format(sha=sha[:7]) + strip_note(persona).rstrip() + f"\n\nגרסת פרסונה: {sha[:7]}\n"


def write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".CLAUDE.md.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


FOOTER = re.compile(r"^גרסת פרסונה: ([0-9a-f]{7,40})\s*$", re.M)


def last_synced_sha(targets):
    """The persona version in the footer of a generated copy, or None if none was ever generated."""
    for t in targets:
        try:
            text = t.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if text.startswith("<!-- נוצר אוטומטית"):
            m = FOOTER.findall(text)
            if m:
                return m[-1]
    return None


def trusted_base(trusted_path, targets):
    """The anchor list to compare against. Raises RuntimeError when it can't be established."""
    if trusted_path.exists():
        text = trusted_path.read_text(encoding="utf-8", errors="replace")
        if len(parse_anchors(text)) >= MIN_ANCHORS:
            return text
    synced = last_synced_sha(targets)
    if synced:  # the trusted list was deleted or emptied: rebuild it from the last good version
        try:
            git("cat-file", "-e", f"{synced}^{{commit}}")
        except RuntimeError:
            raise RuntimeError(f"רשימת העוגנים המהימנה ({trusted_path}) חסרה, ואי אפשר לשחזר אותה מגרסה "
                               f"{synced} — צריך את בעז")
        try:
            return git("show", f"{synced}:scripts/persona-anchors.txt")
        except RuntimeError:
            pass  # that version predates the anchors file: same as a first run
    try:  # first run ever: no copy was generated yet, or only from before the anchors file
        return git("show", "refs/heads/main:scripts/persona-anchors.txt")
    except RuntimeError:
        return ""


def consume_approvals(approved_path, new_anchors_text):
    """Drop approvals whose removal has now been synced, so they can't cover a later removal."""
    if not approved_path.exists():
        return
    new = {norm(a) for a in parse_anchors(new_anchors_text)}
    lines = approved_path.read_text(encoding="utf-8", errors="replace").splitlines()
    keep = [l for l in lines if not l.strip() or l.lstrip().startswith("#") or norm(l.strip()) in new]
    if len(keep) != len(lines):
        write_atomic(approved_path, "\n".join(keep) + ("\n" if keep else ""))


def sync_copies(persona, anchors_text, sha, targets, base_anchors=None, approved="", trusted_path=None):
    """Return report lines. Never writes a version that fails the anchor check.

    base_anchors: the trusted anchor list. Its anchors are checked too, and one that the new
    list dropped without the founder's recorded OK blocks the write. On success the new list
    becomes the trusted one (trusted_path)."""
    if len(parse_anchors(anchors_text)) < MIN_ANCHORS:
        return [f"רשימת עוגני השערים בגרסה {sha[:7]} ריקה או קצרה מדי — העותקים הקודמים נשארו."]
    dropped = removed_anchors(base_anchors, anchors_text, approved) if base_anchors else []
    if dropped:
        return [f"בגרסה {sha[:7]} הוסר עוגן של שער ({len(dropped)}) — צריך אישור של בעז "
                f"(שורה ב-{APPROVED_REMOVALS}); העותקים הקודמים נשארו. הוסר: " + " | ".join(dropped[:3])]
    ok = {norm(a) for a in parse_anchors(approved)}
    new = parse_anchors(anchors_text)
    seen = {norm(a) for a in new}
    union = "\n".join(new + [a for a in parse_anchors(base_anchors) if norm(a) not in ok and norm(a) not in seen])
    missing = missing_anchors(strip_note(persona), union)  # the text that is written, not the dropped note
    if missing:
        return [f"גרסת הפרסונה {sha[:7]} נכשלה בבדיקת השערים ({len(missing)} עוגנים חסרים) — "
                "העותקים הקודמים נשארו. חסר: " + " | ".join(missing[:3])]
    text = build_copy(persona, sha)
    changed = []
    for t in targets:
        if not t.parent.exists():
            continue
        old = t.read_text(encoding="utf-8", errors="replace") if t.exists() else None
        if old != text:
            write_atomic(t, text)
            changed.append(str(t).replace(str(HOME), "~"))
    if trusted_path is not None:
        write_atomic(trusted_path, "\n".join(new) + "\n")
    if changed:
        return [f"עותקי הפרסונה עודכנו לגרסה {sha[:7]}: {', '.join(changed)}. "
                f"אם ההוראות שנטענו בשיחה הזו לא מסתיימות ב'גרסת פרסונה: {sha[:7]}' — לפתוח שיחה חדשה."]
    return []


def plugin_lines(cwd):
    installed = HOME / ".claude" / "plugins" / "installed_plugins.json"
    if not installed.exists():
        return []
    try:
        data = json.loads(installed.read_text(encoding="utf-8")).get("plugins", {})
    except (OSError, ValueError):
        return []
    out = []
    for key, entries in data.items():
        name, _, market = key.partition("@")
        if market != MARKETPLACE:
            continue
        manifest = REPO / "plugins" / name / ".claude-plugin" / "plugin.json"
        try:
            repo_version = json.loads(manifest.read_text(encoding="utf-8")).get("version")
        except (OSError, ValueError):
            continue
        for e in entries if isinstance(entries, list) else [entries]:
            if e.get("projectPath") and os.path.realpath(e["projectPath"]) != os.path.realpath(cwd):
                continue
            if e.get("version") and repo_version and e["version"] != repo_version:
                out.append(f"plugin {name}: מותקן {e['version']}, בריפו {repo_version} — "
                           f"להריץ `claude plugin update {name}` ולפתוח שיחה מחדש.")
                continue
            sha = e.get("gitCommitSha")
            if not sha:
                continue
            try:
                r = subprocess.run(["git", "-C", str(REPO), "diff", "--quiet", sha, "HEAD", "--",
                                    f"plugins/{name}"], capture_output=True, timeout=10)
            except subprocess.TimeoutExpired:
                continue
            if r.returncode == 1:  # 0 same, 1 differs, >1 unknown sha: stay quiet
                out.append(f"plugins/{name} השתנה בלי העלאת גרסה — השינוי לא יגיע לשיחות עד "
                           "שהגרסה תעלה (commit דרך ה-hooks של הריפו).")
    return out


def main():
    if platform.system() != "Linux" or not (REPO / ".git").exists():
        return 0
    lines = []
    ref = "refs/heads/main"
    try:
        git("fetch", "--quiet", REMOTE_URL, f"+main:{FETCH_REF}", timeout=5)
        ref = FETCH_REF
    except (RuntimeError, subprocess.TimeoutExpired, OSError):
        lines.append("לא הצלחתי למשוך את main מ-GitHub — הפרסונה נבנתה מהעותק המקומי.")
    if ref == FETCH_REF:
        try:
            behind = int(git("rev-list", "--count", f"refs/heads/main..{FETCH_REF}").strip() or 0)
            ahead = int(git("rev-list", "--count", f"{FETCH_REF}..refs/heads/main").strip() or 0)
            if behind:
                lines.append(f"business-os-plugin המקומי מפגר ב-{behind} commits אחרי GitHub — "
                             "צריך `git pull` בריפו (ה-plugins מותקנים מהעותק המקומי).")
            if ahead:
                lines.append(f"{ahead} commits מקומיים שלא ב-GitHub — עותקי הפרסונה נבנו בלעדיהם.")
        except (RuntimeError, subprocess.TimeoutExpired, ValueError, OSError):
            pass  # no local main (e.g. renamed): nothing to compare, the fetched version is used
    try:
        # The version is the last commit that touched the persona or its anchors, so
        # unrelated commits don't rewrite the copies or ask for a new session.
        sha = git("log", "-1", "--format=%H", ref, "--", "CEO-PERSONA.md", "scripts/persona-anchors.txt").strip() \
            or git("rev-parse", ref).strip()
        persona = git("show", f"{sha}:CEO-PERSONA.md")
        anchors = git("show", f"{sha}:scripts/persona-anchors.txt")
        base = trusted_base(TRUSTED_ANCHORS, TARGETS)
        approved = (APPROVED_REMOVALS.read_text(encoding="utf-8", errors="replace")
                    if APPROVED_REMOVALS.exists() else "")
        out = sync_copies(persona, anchors, sha, TARGETS, base, approved, TRUSTED_ANCHORS)
        lines += out
        if not out or "עודכנו" in out[0]:  # synced: approvals for removals now done are used up
            consume_approvals(APPROVED_REMOVALS, anchors)
    except (RuntimeError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
        lines.append(f"סנכרון הפרסונה נכשל ({exc}) — העותקים לא שונו.")
    try:
        lines += plugin_lines(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
    except Exception as exc:  # never lose the lines above
        lines.append(f"בדיקת גרסאות ה-plugins נכשלה ({exc}).")
    for l in lines:
        print(f"business-os: {l}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # a session must never fail to start because of this hook
        print(f"business-os: סנכרון הפרסונה נכשל ({exc}) — העותקים לא שונו.")
        sys.exit(0)
