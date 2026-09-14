#!/usr/bin/env python3
"""rnd-os security gate — PreToolUse hook.

Two rules, enforced for every tool call Claude makes (main session and
subagents alike) in a folder where rnd-os is enabled:

1. Production-affecting actions — deploys, infra apply, production
   migrations, pushes to protected branches, publishing — are blocked unless
   security-lead has recorded an approval for the exact code being shipped.
2. Nobody except the security-lead agent may write approval records.

Approval record: <repo>/rnd/security/approvals/<full-commit-sha>.md with a line
`verdict: APPROVED` or `verdict: APPROVED_WITH_ACCEPTED_RISK`. It stays valid
while every change since that commit is under rnd/ (so committing the record
itself does not invalidate it); any code change needs a new approval.

Blocks by exiting 2 with the reason on stderr. Hooks only see Claude's tool
calls: the founder running a command in their own terminal is never blocked.
"""
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

APPROVALS_REL = "rnd/security/approvals"
EXCLUDE_RND = ":(exclude)rnd"
PROTECTED_BRANCHES = {"main", "master", "prod", "production", "release"}
VERDICT_RE = re.compile(r"^verdict:\s*(APPROVED|APPROVED_WITH_ACCEPTED_RISK)\s*$", re.M)
SHA_RE = re.compile(r"[0-9a-f]{40}")

DEPLOY_PATTERNS = [re.compile(p, re.I) for p in (
    r"\bvercel\b[^;&|\n]*\s--prod\b",
    r"\bvercel\s+promote\b",
    r"\bnetlify\s+deploy\b[^;&|\n]*\s--prod\b",
    r"\b(fly|flyctl)\s+deploy\b",
    r"\bwrangler\s+(deploy|publish|pages\s+deploy|versions\s+deploy)\b",
    r"\bfirebase\s+deploy\b",
    r"\bgcloud\s+(run|app|functions)\s+deploy\b",
    r"\baws\s+(cloudformation\s+deploy|lambda\s+update-function-code|ecs\s+update-service|amplify\s+start-deployment)\b",
    r"\b(sam|cdk|serverless|sls)\s+deploy\b",
    r"\b(terraform|tofu)\s+apply\b",
    r"\bpulumi\s+up\b",
    r"\bkubectl\s+(apply|create|replace|set\s+image|rollout\s+restart)\b",
    r"\bhelm\s+(install|upgrade)\b",
    r"\brailway\s+up\b",
    r"\bdoctl\s+apps\s+(create|update|create-deployment)\b",
    r"\bgit\s+push\s+heroku\b",
    r"\bsupabase\s+(db\s+push|functions\s+deploy)\b",
    r"\bprisma\s+migrate\s+deploy\b",
    r"\b(npm|pnpm|yarn)\s+publish\b",
    r"\bdocker\s+push\b",
    r"\bgh\s+release\s+create\b",
)]

MCP_ACTION_RE = re.compile(r"(deploy|publish|promote|release|rollout|apply_migration)", re.I)
MCP_READONLY_RE = re.compile(r"^(list|get|search|read|fetch|describe|view|check)", re.I)
WRITE_VERB_RE = re.compile(
    r"(>|\btee\b|\bcp\b|\bmv\b|\brm\b|\bsed\s+-i|\btouch\b|\bln\b|\bdd\b|\btruncate\b"
    r"|\bpython3?\b|\bnode\b|\bperl\b|\bruby\b|-delete\b"
    r"|\bgit\s+(rm|mv|checkout|restore|reset|stash|clean|apply)\b)"
)
CD_PREFIX_RE = re.compile(r"""^\s*cd\s+(?:"([^"]+)"|'([^']+)'|(\S+))\s*(?:&&|;)""")
SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||;|\||\n")


def block(reason):
    print(reason, file=sys.stderr)
    sys.exit(2)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=20)


def is_security_lead(event):
    return (event.get("agent_type") or "").split(":")[-1] == "security-lead"


def target_dir(command, cwd):
    m = CD_PREFIX_RE.match(command)
    if not m:
        return Path(cwd)
    d = Path(next(g for g in m.groups() if g)).expanduser()
    return d if d.is_absolute() else Path(cwd) / d


def pushes_protected_branch(command, repo):
    for segment in SEGMENT_SPLIT_RE.split(command):
        if not re.search(r"\bgit\b.*\bpush\b", segment):
            continue
        try:
            tokens = shlex.split(segment)
        except ValueError:
            tokens = segment.split()
        if "push" not in tokens:
            continue
        args = tokens[tokens.index("push") + 1:]
        if any(a in ("--all", "--mirror") for a in args):
            return True
        positional = [a for a in args if not a.startswith("-")]
        refs = positional[1:]
        if not refs:
            branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
            if branch in PROTECTED_BRANCHES:
                return True
            continue
        for ref in refs:
            dst = ref.lstrip("+").split(":")[-1].split("/")[-1]
            if dst in PROTECTED_BRANCHES:
                return True
    return False


def approval_problem(start_dir):
    """Return None if the code at HEAD is security-approved, else the reason."""
    top = git(start_dir, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return (f"{start_dir} אינו git repository. אישור אבטחה נקשר ל-commit מסוים, "
                "ולכן פעולת production מותרת רק מתוך repo עם קוד מחויב.")
    repo = Path(top.stdout.strip())
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    if not head:
        return f"אין commit ב-{repo} — אין מה לאשר."
    dirty = git(repo, "status", "--porcelain", "--", ".", EXCLUDE_RND).stdout.strip()
    if dirty:
        return ("יש שינויים שלא נכנסו ל-commit (מחוץ ל-rnd/). אישור האבטחה מכסה קוד מחויב בלבד — "
                f"יש לבצע commit ולקבל אישור על ה-commit החדש.\n{dirty[:800]}")
    for record in sorted((repo / APPROVALS_REL).glob("*.md")):
        sha = record.stem
        if not SHA_RE.fullmatch(sha) or not VERDICT_RE.search(record.read_text(errors="ignore")):
            continue
        if sha == head:
            return None
        if git(repo, "merge-base", "--is-ancestor", sha, head).returncode != 0:
            continue
        changed = git(repo, "diff", "--name-only", sha, head, "--", ".", EXCLUDE_RND)
        if changed.returncode == 0 and not changed.stdout.strip():
            return None
    return (f"אין אישור אבטחה בתוקף ל-commit {head}.\n"
            f"נדרש קובץ {APPROVALS_REL}/{head}.md שנכתב על ידי security-lead עם verdict: APPROVED. "
            "rnd-lead צריך להעביר את ה-commit הזה ל-security-lead לבדיקה לפני שחרור.")


def main():
    try:
        event = json.load(sys.stdin)
    except (ValueError, OSError):
        sys.exit(0)

    tool = event.get("tool_name") or ""
    tool_input = event.get("tool_input") or {}
    cwd = event.get("cwd") or "."
    security = is_security_lead(event)

    # Rule 2 — only security-lead writes approval records.
    if not security:
        if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
            if APPROVALS_REL in path.replace("\\", "/"):
                block(f"חסום: רק security-lead רשאי לכתוב רשומות אישור אבטחה ({APPROVALS_REL}).")
        if tool == "Bash":
            command = str(tool_input.get("command") or "")
            if APPROVALS_REL in command and WRITE_VERB_RE.search(command):
                block(f"חסום: רק security-lead רשאי לשנות רשומות אישור אבטחה ({APPROVALS_REL}).")

    # Rule 1 — production-affecting actions need a valid approval.
    gated = None
    repo_hint = Path(cwd)
    if tool == "Bash":
        command = str(tool_input.get("command") or "")
        repo_hint = target_dir(command, cwd)
        if any(p.search(command) for p in DEPLOY_PATTERNS):
            gated = "פקודת שחרור/תשתית ל-production"
        else:
            try:
                if pushes_protected_branch(command, repo_hint):
                    gated = "push לענף מוגן (main/master/production)"
            except Exception as exc:  # fail closed on a detected push we cannot analyse
                block(f"חסום: שער האבטחה לא הצליח לנתח push ({exc}).")
    elif tool.startswith("mcp__"):
        action = tool.split("__")[-1]
        if MCP_ACTION_RE.search(action) and not MCP_READONLY_RE.match(action):
            gated = f"פעולת MCP שמשפיעה על production ({tool})"

    if not gated:
        sys.exit(0)
    try:
        problem = approval_problem(repo_hint)
    except Exception as exc:
        problem = f"שער האבטחה נכשל בבדיקה ({exc}) — חוסם כברירת מחדל."
    if problem:
        block(f"🔒 rnd-os security gate — {gated} חסומה.\n{problem}")
    sys.exit(0)


if __name__ == "__main__":
    main()
