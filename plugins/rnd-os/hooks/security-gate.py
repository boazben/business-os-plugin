#!/usr/bin/env python3
"""rnd-os security gate — PreToolUse hook.

Rules, enforced for every tool call Claude makes (main session and subagents
alike) in a folder where rnd-os is enabled:

1. Production-affecting actions — deploys, infra apply, production
   migrations, pushes and merges to protected branches, publishing — are
   blocked unless security-lead has recorded an approval for the exact commit
   being shipped: HEAD for a deploy, the pushed commit for a push, the PR head
   named with --match-head-commit (or the API's sha / expectedHeadSha) for a merge.
2. Nobody except the rnd-os:security-lead agent may write approval records.
3. Nobody changes what guards production on the hosting side — branch
   protection, deployment environments, secrets, git aliases — or writes
   branches through the hosting API instead of git. Those belong to the
   founder, in the GitHub or Vercel UI.

Approval record: <repo>/rnd/security/approvals/<full-commit-sha>.md with a line
`verdict: APPROVED` or `verdict: APPROVED_WITH_ACCEPTED_RISK`. It stays valid
while every change since that commit is under rnd/, is business-os state
(.business-os/), or is an inert media or document file under design/ (so
committing the record itself, or a new ad image, does not invalidate it). Any
code change — including HTML, SVG, CSS or JS under design/, which can run if the
repo is served — needs a new approval.

What this is not: a security boundary. Every agent shares one filesystem and
one shell, so a determined agent can still reach production by a path no
pattern list sees: a script it wrote earlier, a string built at runtime, a git
hook or `git -c` command, checking out a branch that already carries a record.
This gate stops honest mistakes and the known bypasses. The boundary is on the
hosting side — a production deployment that waits for the founder's click; see
skills/production-protection.

Blocks by exiting 2 with the reason on stderr; an unreadable event or any
failure inside the checks also blocks. Hooks only see Claude's tool calls: the
founder running a command in their own terminal is never blocked.
Tests: hooks/tests/test_security_gate.py — add a case for every new bypass.
"""
import glob
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

SECURITY_AGENT = "rnd-os:security-lead"
APPROVALS_REL = "rnd/security/approvals"
DESIGN_INERT_EXTS = ("png", "jpg", "jpeg", "webp", "gif", "mp4", "mov", "webm", "md", "pdf")
NON_CODE_EXCLUDES = (":(exclude)rnd", ":(exclude).business-os",
                     *(f":(exclude,glob,icase)design/**/*.{ext}" for ext in DESIGN_INERT_EXTS))
PROTECTED_BRANCHES = {"main", "master", "prod", "production", "release"}
VERDICT_RE = re.compile(r"^verdict:\s*(APPROVED|APPROVED_WITH_ACCEPTED_RISK)\s*$", re.M)
SHA_RE = re.compile(r"[0-9a-f]{40}")

SHIP_WORD = r"(deploy|release|publish|ship|promote)"
# A shipping script name ends there, or says it targets production: `deploy`, `release:prod`.
# `release-notes`, `shipping:seed` and `release:dry-run` are not shipping.
SHIP_END = r"(?=$|[\s;&|)'\"]|[:._-](prod|production|live)\b)"
OPT = r"(--[\w-]+(=\S+)?|-\w[\w-]*)"  # one option token; the two forms never overlap
OPT_VALUE = r"(\s+[^\s;&|-]\S*)?"
# `gh api` that changes something: an explicit write method, or fields (which make it a POST).
GH_API_WRITE = (r"\bgh\s+api\b(?=[^;&|\n]*(-X\s*(POST|PUT|PATCH|DELETE)\b|--method[=\s]+(POST|PUT|PATCH|DELETE)\b"
                r"|\s-[fF]|\s--(raw-)?field\b|\s--input\b))")

DEPLOY_PATTERNS = [re.compile(p, re.I) for p in (
    r"\bvercel\b[^;&|\n]*\s--(prod|target[=\s]+production)\b",
    r"\bvercel\s+(promote|alias|rollback|rolling-release)\b",
    # Netlify publish: a --prod CLI deploy, publishing an already-built deploy through the API, or
    # unlocking auto publishing (every later merge would go live without asking).
    r"\b(netlify|netlify-cli|ntl)\s+(deploy\b[^;&|\n]*\s--(prod|prod-if-unlocked)\b|unlock\b"
    r"|api\s+(restoreSiteDeploy|unlockDeploy)\b)",
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
    r"\b(npm|pnpm|yarn|bun)\s+publish\b",
    # `npm run deploy`, `pnpm -F web run deploy`, `yarn workspace web release:prod`.
    r"\b(npm|pnpm|yarn|bun)(\s+(" + OPT + r"|workspace)" + OPT_VALUE + r")*\s+(run(-script)?\s+)?" + SHIP_WORD + SHIP_END,
    r"\b(make|just|task)(\s+-\S+" + OPT_VALUE + r")*\s+" + SHIP_WORD + SHIP_END,
    # Running a shipping script (reading or editing one is fine), with env prefixes and interpreter flags.
    r"(^\s*|\b(bash|sh|zsh|node|python3?|tsx|ts-node|bun|deno\s+run)(\s+-\S+)*\s+)(\w+=\S*\s+)*(\S*/)?"
    r"(deploy|release|publish|ship)([._-](prod|production|live))?\.(sh|bash|py|js|mjs|cjs|ts)\b",
    r"\bdocker\s+push\b",
    r"\bgh\s+release\s+create\b",
    r"\bgh\s+workflow\s+run\b",
)]
# pnpm's own `deploy` command copies a workspace package into a folder; it ships nothing.
PNPM_BUILTIN_DEPLOY_RE = re.compile(r"\bpnpm((\s+" + OPT + OPT_VALUE + r")*)\s+deploy\b", re.I)
GH_PR_MERGE_RE = re.compile(r"\bgh\s+pr\s+merge\b", re.I)
GH_API_PR_MERGE_RE = re.compile(GH_API_WRITE + r"[^;&|\n]*/pulls/\d+/merge\b", re.I)
MERGE_PIN_RE = re.compile(r"--match-head-commit[=\s]+(\S+)|\bsha=(\S+)")

# Changing what guards production is never Claude's call, approval or not.
HOSTING_CONTROL_PATTERNS = [re.compile(p, re.I) for p in (
    GH_API_WRITE + r"[^;&|\n]*(/protection|/rulesets|/environments|pending_deployments|/actions/(secrets|variables)|/actions/permissions(?![^;&|\n]*\benabled=false\b)|/collaborators|/keys\b|/hooks\b)",
    r"\bgh\s+(secret|variable)\s+(set|delete|remove)\b",
    r"\bgh\s+repo\s+(edit|delete|rename|archive)\b",
    r"\bvercel\s+(env\s+(add|rm|remove)|project\s+(rm|remove)|teams)\b",
    # Netlify: the founder let Claude manage settings (17.9.2026); publishing goes through Rule 1.
    # Still never Claude's: a draft deploy (its URL skips the pre-live review), secrets, and deleting.
    r"\b(netlify|netlify-cli|ntl)\s+(deploy\b(?![^;&|\n]*--prod)|build\s+--deploy"
    r"|env:(set|unset|import|clone)|sites:delete)\b",
    r"\b(netlify|netlify-cli|ntl)\s+api\s+(createSiteDeploy|createEnvVars|setEnvVarValue|updateEnvVar"
    r"|deleteEnvVar|deleteEnvVarValue|deleteSite|deleteDeploy|deleteSiteDeploy)\b",
    r"\bgit\b(\s+-c\s*|[^;&|\n]*\bconfig\b[^;&|\n]*\s)alias\.",
)]
# Branches change through `git push` of an approved commit, not through the API.
API_BRANCH_WRITE_PATTERNS = [re.compile(p, re.I) for p in (
    GH_API_WRITE + r"[^;&|\n]*/(git/refs|contents/|merges\b|deployments\b)",
    r"\bgh\s+api\s+graphql\b[^;&|\n]*(mergePullRequest|enablePullRequestAutoMerge|updateRef|createCommitOnBranch|mergeBranch)",
)]

MCP_ACTION_RE = re.compile(r"(deploy|publish|promote|release|rollout|apply_migration)", re.I)
MCP_MERGE_RE = re.compile(r"(merge_pull|pull_request_merge|merge_pr\b|merge_branch)", re.I)
MCP_BRANCH_WRITE_RE = re.compile(r"(push_files|create_or_update_file|delete_file|update_ref|create_ref)", re.I)
MCP_READONLY_RE = re.compile(r"^(list|get|search|read|fetch|describe|view|check)", re.I)
# Protection settings: any server for unambiguous names, hosting servers for generic ones.
MCP_CONTROL_ANY_RE = re.compile(r"(branch_protection|ruleset|deploy_key|collaborator|actions_secret|repository_secret"
                                r"|repo_secret|environment_secret|env_var|environment_variable)", re.I)
MCP_CONTROL_HOSTING_RE = re.compile(r"(protection|environment|secret|webhook)", re.I)
HOSTING_SERVERS = ("github", "vercel", "netlify", "cloudflare")
MCP_MERGE_PIN_KEYS = ("expectedHeadSha", "expected_head_sha")
MCP_PATH_KEYS = {"path", "paths", "file_path", "filepath", "file", "source", "destination", "target", "dest", "src"}

HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1([^\n]*)\n(.*?)\n[ \t]*\2[ \t]*(?=\n|$)", re.S)
QUOTED_RE = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")
MESSAGE_OPT_RE = re.compile(r"(\s(?:-m|--message|--body|--title|--notes|--description|--subject))"
                            r"(?:=|\s+)(\"(?:[^\"\\]|\\.)*\"|'[^']*'|[^\s-]\S*)")
REDIRECT_RE = re.compile(r"(?:\d|&)?>>?\s*(&\d+|[^\s;&|<>]+)")
TOKEN_REDIRECT_PREFIX_RE = re.compile(r"^(\d*|&)>>?|^<+")
BRACE_RE = re.compile(r"^(.*?)\{([^{}]*,[^{}]*)\}(.*)$")
SHELLS = {"bash", "sh", "zsh", "dash"}
EXEC_WORDS = SHELLS | {"python", "python3", "node", "ruby", "perl", "deno", "bun", "tsx", "eval", "xargs",
                       "ssh", "source", "."}
TEXT_ONLY_COMMANDS = {"grep", "egrep", "rg", "echo", "printf", "cat", "ls", "head", "tail", "wc", "less", "man"}
READ_ONLY_COMMANDS = TEXT_ONLY_COMMANDS | {"stat", "file", "diff", "sha256sum", "md5sum", "test", "[", "true",
                                           "cd", "pwd", "find"}
READ_ONLY_GIT = {"log", "show", "diff", "status", "ls-files", "blame", "rev-parse", "add", "commit", "push", "fetch"}
RECURSIVE_COPIERS = {"mv", "rsync", "tar", "unzip", "ditto"}
COMMAND_PREFIXES = {"env", "sudo", "command", "nohup", "time", "exec", "nice"}
GIT_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
PUSH_OPTS_WITH_VALUE = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}


def block(reason):
    print(reason, file=sys.stderr)
    sys.exit(2)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=20)


def is_security_lead(event):
    return (event.get("agent_type") or "") == SECURITY_AGENT


# ---------- shell text ----------

def normalize(command):
    """Join `\\` line continuations and drop heredoc bodies that are only text
    (a commit message, a file being written) — but keep bodies fed to a shell
    or interpreter, which run."""
    command = command.replace("\r\n", "\n")

    def heredoc(m):
        line_start = m.string.rfind("\n", 0, m.start()) + 1
        before = split_segments(m.string[line_start:m.start()])
        runner = command_word(split_words(before[-1])) if before else ""
        return m.group(0) if runner in EXEC_WORDS else "<<" + m.group(2) + m.group(3)

    command = HEREDOC_RE.sub(heredoc, command)
    return re.sub(r"\\\n", " ", command)


def split_segments(command):
    """Split on && || | |& ; & and newlines outside quotes."""
    out, buf, i, quote, n = [], [], 0, None, len(command)
    while i < n:
        c = command[i]
        if quote:
            buf.append(c)
            if c == "\\" and quote == '"' and i + 1 < n:
                buf.append(command[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            buf += [c, command[i + 1]]
            i += 2
            continue
        if c in "'\"":
            quote = c
            buf.append(c)
            i += 1
            continue
        if command[i:i + 2] in ("&&", "||", "|&"):
            out.append("".join(buf))
            buf = []
            i += 2
            continue
        if c in ";\n|" or (c == "&" and (i == 0 or command[i - 1] not in "<>") and command[i + 1:i + 2] != ">"):
            out.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    out.append("".join(buf))
    return [s for s in out if s.strip()]


def split_words(segment):
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def command_word(words):
    for w in words:
        if re.match(r"^\w+=", w) or os.path.basename(w) in COMMAND_PREFIXES:
            continue
        return os.path.basename(w.lstrip("("))
    return ""


def unquote(text):
    return re.sub(r"[\"'\\]", "", text)


def scrub(segment):
    """Blank out commit messages, PR bodies and titles: text about a deploy is not a deploy."""
    words = split_words(segment)
    if any(os.path.basename(w) in ("git", "gh") for w in words[:4]):
        return MESSAGE_OPT_RE.sub(r"\1 MSG", segment)
    return segment


def segments(command, cwd):
    """(text, words, directory) per segment, following `cd` the way the shell would."""
    here = Path(cwd)
    for raw in split_segments(command):
        text = scrub(raw)
        words = split_words(text)
        yield text, words, here
        if words and words[0].lstrip("(") in ("cd", "pushd") and len(words) > 1:
            here = resolve(words[1].rstrip(")"), here)


def resolve(raw, base):
    try:
        p = Path(os.path.expanduser(raw))
    except RuntimeError:  # ~user that does not exist
        p = Path(raw)
    return p if p.is_absolute() else Path(base) / p


# ---------- rule 2: approval records ----------

def in_approvals(path_text, include_parents=False):
    """A path inside security/approvals. With include_parents, also the rnd and
    rnd/security folders that contain it (a recursive copy into those lands in approvals)."""
    parts = [p for p in str(path_text).replace("\\", "/").casefold().split("/") if p not in ("", ".")]
    if any(a == "security" and b == "approvals" for a, b in zip(parts, parts[1:])):
        return True
    return include_parents and (parts[-1:] == ["rnd"] or parts[-2:] == ["rnd", "security"])


def spellings(p):
    return (str(p), os.path.normpath(p), os.path.realpath(p))


def write_path_in_approvals(raw, cwd):
    p = resolve(str(raw).replace("\\", "/"), cwd)
    return any(in_approvals(s) for s in spellings(p))


def expand_braces(token, depth=0):
    m = BRACE_RE.match(token)
    if not m or depth > 4:
        return [token]
    return [x for alt in m.group(2).split(",") for x in expand_braces(m.group(1) + alt + m.group(3), depth + 1)][:64]


def token_paths(token, here):
    paths = []
    for t in expand_braces(TOKEN_REDIRECT_PREFIX_RE.sub("", token.replace("\\", ""))):
        t = t.lstrip("$(").rstrip(")")
        if t.startswith("-"):
            if "=" not in t:
                continue
            t = t.split("=", 1)[1]
        if not t or len(t) > 400:
            continue
        base = resolve(t, here)
        matches = glob.glob(str(base)) if any(c in t for c in "*?[") else []
        paths += [s for m in (matches or [base]) for s in spellings(m)]
    return paths


def read_only_segment(words, text):
    if not words:
        return True
    for m in REDIRECT_RE.finditer(QUOTED_RE.sub("Q", text)):
        if m.group(1) != "/dev/null" and not m.group(1).startswith("&"):
            return False
    if "$(" in text or "`" in text or "<<" in text or "=" in words[0]:
        return False
    cmd = os.path.basename(words[0].lstrip("("))
    if cmd == "git":
        rest = words[1:]
        while rest and rest[0].startswith("-"):
            if rest[0] == "-c" or rest[0].startswith("--config-env"):
                return False  # a config value can be a command git runs
            rest = rest[2:] if rest[0] in GIT_OPTS_WITH_VALUE else rest[1:]
        return bool(rest) and rest[0] in READ_ONLY_GIT and not any(w.startswith("--output") for w in rest)
    if cmd == "find":
        return not any(w in ("-delete", "-exec", "-execdir", "-ok", "-fprint") for w in words)
    return cmd in READ_ONLY_COMMANDS


def bash_changes_approvals(command, cwd):
    """A command that touches the approvals folder (by any spelling, glob, brace
    or relative path after `cd`) must be read-only in every segment."""
    touched, read_only = False, True
    for text, words, here in segments(command, cwd):
        cmd = command_word(words)
        recursive = cmd in RECURSIVE_COPIERS or (
            cmd == "cp" and any(re.fullmatch(r"-\w*[rRa]\w*|--recursive|--archive", w) for w in words[1:]))
        if any(in_approvals(p, recursive) for w in words[1:] for p in token_paths(w, here)):
            touched = True
        if not read_only_segment(words, text):
            read_only = False
    return touched and not read_only


def strings_in(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings_in(item)


def mcp_paths(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).casefold() in MCP_PATH_KEYS:
                yield from strings_in(value)
            else:
                yield from mcp_paths(value)
    elif isinstance(node, list):
        for item in node:
            yield from mcp_paths(item)


# ---------- rule 1: what ships ----------

def head_branch(repo):
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def branch_name(ref):
    return re.sub(r"^(refs/)?heads/", "", ref)


def local_protected(repo):
    return [(repo, b) for b in sorted(PROTECTED_BRANCHES)
            if git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{b}").returncode == 0]


def push_targets(command, cwd, depth=0):
    """(repo, rev) for every protected branch a command pushes; rev None = deletion."""
    targets = []
    for _, words, here in segments(command, cwd):
        for i, w in enumerate(words):
            name = os.path.basename(w)
            if depth < 3 and name in SHELLS:
                j, runs_string = i + 1, False
                while j < len(words) and words[j].startswith("-"):
                    runs_string |= bool(re.fullmatch(r"-\w*c\w*", words[j]))
                    j += 1
                if runs_string and j < len(words):
                    targets += push_targets(words[j], here, depth + 1)
            elif depth < 3 and name == "eval":
                targets += push_targets(" ".join(words[i + 1:]), here, depth + 1)
        k = 0
        while k < len(words) and (re.match(r"^\w+=", words[k]) or os.path.basename(words[k]) in COMMAND_PREFIXES
                                  or (k and words[k].startswith("-") and os.path.basename(words[k - 1]) in COMMAND_PREFIXES)):
            k += 1
        if k >= len(words) or os.path.basename(words[k].lstrip("(")) != "git":
            continue
        repo, j = here, k + 1
        while j < len(words) and words[j].startswith("-"):
            if words[j] == "-C" and j + 1 < len(words):
                repo = resolve(words[j + 1], repo)
            j += 2 if words[j] in GIT_OPTS_WITH_VALUE else 1
        if j >= len(words) or words[j] != "push":
            continue
        args, positional, flags, a = words[j + 1:], [], set(), 0
        while a < len(args):
            if args[a] in PUSH_OPTS_WITH_VALUE:
                a += 2
                continue
            if args[a].startswith("-"):
                flags.add(args[a].split("=")[0])
            else:
                positional.append(args[a])
            a += 1
        if flags & {"--all", "--mirror", "--branches"}:
            targets += local_protected(repo)
            continue
        deleting = bool(flags & {"--delete", "-d"})
        refs = positional[1:]
        if not refs:
            if head_branch(repo) in PROTECTED_BRANCHES:
                targets.append((repo, "HEAD"))
            continue
        for ref in refs:
            spec = ref.lstrip("+")
            src, dst = spec.split(":", 1) if ":" in spec else (spec, spec)
            if spec == ":" or "*" in dst:
                targets += local_protected(repo)
                continue
            dst_name = head_branch(repo) if dst in ("HEAD", "@") else branch_name(dst)
            if dst_name in PROTECTED_BRANCHES:
                targets.append((repo, None if deleting or not src else src))
    return targets


def approval_problem(start_dir, rev="HEAD", require_clean=True):
    """Return None if commit `rev` is security-approved, else the reason."""
    top = git(start_dir, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return (f"{start_dir} אינו git repository. אישור אבטחה נקשר ל-commit מסוים, "
                "ולכן פעולת production מותרת רק מתוך repo עם קוד מחויב.")
    repo = Path(top.stdout.strip())
    resolved = git(repo, "rev-parse", "--verify", "-q", f"{rev}^{{commit}}").stdout.strip()
    sha = resolved or (rev if SHA_RE.fullmatch(rev or "") else "")
    if not sha:
        return f"לא ניתן לזהות את ה-commit '{rev}' ב-{repo} — אין מה לאשר."
    if require_clean:
        dirty = git(repo, "status", "--porcelain", "--", ".", *NON_CODE_EXCLUDES).stdout.strip()
        if dirty:
            return ("יש שינויים שלא נכנסו ל-commit (מחוץ ל-rnd/ ולקבצי מדיה ומסמכים ב-design/). אישור האבטחה מכסה קוד מחויב בלבד — "
                    f"יש לבצע commit ולקבל אישור על ה-commit החדש.\n{dirty[:800]}")
    approvals = repo / APPROVALS_REL
    if approvals.exists() and approvals.resolve() != repo.resolve() / APPROVALS_REL:
        return (f"{APPROVALS_REL} (או תיקייה מעליה) הוא קישור לתיקייה אחרת. "
                "רשומות אישור נקראות רק מהתיקייה עצמה ב-repo.")
    for record in sorted(approvals.glob("*.md")):
        approved = record.stem
        if record.is_symlink() or not SHA_RE.fullmatch(approved) or not VERDICT_RE.search(record.read_text(errors="ignore")):
            continue
        if approved == sha:
            return None
        if git(repo, "merge-base", "--is-ancestor", approved, sha).returncode != 0:
            continue
        changed = git(repo, "diff", "--name-only", approved, sha, "--", ".", *NON_CODE_EXCLUDES)
        if changed.returncode == 0 and not changed.stdout.strip():
            return None
    return (f"אין אישור אבטחה בתוקף ל-commit {sha}.\n"
            f"נדרש קובץ {APPROVALS_REL}/{sha}.md שנכתב על ידי security-lead עם verdict: APPROVED. "
            "rnd-lead צריך להעביר את ה-commit הזה ל-security-lead לבדיקה לפני שחרור.")


# ---------- the hook ----------

def check(event):
    tool = str(event.get("tool_name") or "")
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    cwd = str(event.get("cwd") or ".")
    command = normalize(str(tool_input.get("command") or "")) if tool == "Bash" else ""
    parts = tool.split("__")
    action = parts[-1] if tool.startswith("mcp__") else ""
    server = parts[1].casefold() if action and len(parts) >= 3 else ""
    writes_mcp = bool(action) and not MCP_READONLY_RE.match(action)

    segs = [(text, unquote(text), words, here) for text, words, here in segments(command, cwd)]
    fed_to_shell = any(command_word(words) in EXEC_WORDS for _, _, words, _ in segs)
    # Text-printing commands (grep "vercel --prod" docs) are not actions, unless piped into a shell.
    acting = [s for s in segs if fed_to_shell or command_word(s[2]) not in TEXT_ONLY_COMMANDS]

    def any_match(patterns, texts):
        return any(p.search(t) for p in patterns for t in texts)

    # Rule 3 — hosting-side protection is the founder's, in the UI.
    if any(any_match(HOSTING_CONTROL_PATTERNS, (s[0], s[1])) for s in acting) or (writes_mcp and (
            MCP_CONTROL_ANY_RE.search(action)
            or (any(h in server for h in HOSTING_SERVERS) and MCP_CONTROL_HOSTING_RE.search(action)))):
        block("🔒 rnd-os security gate — חסום: שינוי הגנות production (הגנת ענפים, סביבות deploy, "
              "סודות, git aliases) נעשה רק על ידי המייסד, בממשק של GitHub או Vercel. "
              "תכתוב למייסד מה צריך לשנות ולמה.")
    if any(any_match(API_BRANCH_WRITE_PATTERNS, (s[0], s[1])) for s in acting) or (
            action and MCP_BRANCH_WRITE_RE.search(action)
            and branch_name(str(tool_input.get("branch") or tool_input.get("ref") or "")) in PROTECTED_BRANCHES):
        block("🔒 rnd-os security gate — חסום: כתיבה לענפים, מיזוג או deploy דרך ה-API של GitHub. "
              "ענף מוגן משתנה רק ב-git push של commit מאושר, או ב-gh pr merge --match-head-commit <sha מאושר>.")

    # Rule 2 — only security-lead writes approval records.
    if not is_security_lead(event):
        denied = f"חסום: רק rnd-os:security-lead רשאי לכתוב או לשנות רשומות אישור אבטחה ({APPROVALS_REL})."
        if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
            if path and write_path_in_approvals(path, cwd):
                block(denied)
        if command and bash_changes_approvals(command, cwd):
            block(denied)
        if writes_mcp and any(write_path_in_approvals(s, cwd) for s in mcp_paths(tool_input)):
            block(denied)

    # Rule 1 — production-affecting actions need a valid approval for the commit that ships.
    checks = []  # (label, repo, rev, require_clean)
    for text, plain, _words, here in acting:
        shipping = PNPM_BUILTIN_DEPLOY_RE.sub(" pnpm-package ", text)
        if any_match(DEPLOY_PATTERNS, (shipping, unquote(shipping))):
            checks.append(("פקודת שחרור/תשתית ל-production", here, "HEAD", True))
        merges = len(GH_PR_MERGE_RE.findall(plain)) + sum(1 for _ in GH_API_PR_MERGE_RE.finditer(plain))
        if merges:
            pins = [a or b for a, b in MERGE_PIN_RE.findall(plain)]
            if len(pins) < merges or not all(SHA_RE.fullmatch(p) for p in pins):
                block("🔒 rnd-os security gate — מיזוג PR חסום בלי commit מזוהה. "
                      "כל מיזוג צריך gh pr merge <n> --match-head-commit <sha מלא> (או sha=<sha> ב-API), "
                      "כך שהמיזוג יקרה רק אם זה ה-commit ש-security-lead אישר.")
            checks += [("מיזוג PR לענף מוגן", here, p, False) for p in pins]
    for repo, rev in push_targets(command, cwd) if command else []:
        if rev is None:
            block("🔒 rnd-os security gate — חסום: מחיקת ענף מוגן (main/master/production). רק המייסד, בממשק.")
        checks.append(("push לענף מוגן (main/master/production)", repo, rev, False))
    if writes_mcp and MCP_MERGE_RE.search(action):
        sha = next((str(tool_input[k]) for k in MCP_MERGE_PIN_KEYS if tool_input.get(k)), "")
        if not SHA_RE.fullmatch(sha):
            block(f"🔒 rnd-os security gate — מיזוג דרך MCP ({tool}) חסום בלי expectedHeadSha מלא של ה-commit המאושר.")
        checks.append((f"מיזוג דרך MCP ({tool})", Path(cwd), sha, False))
    elif writes_mcp and MCP_ACTION_RE.search(action):
        checks.append((f"פעולת MCP שמשפיעה על production ({tool})", Path(cwd), "HEAD", True))

    for label, repo, rev, require_clean in checks:
        problem = approval_problem(repo, rev, require_clean)
        if problem:
            block(f"🔒 rnd-os security gate — {label} חסומה.\n{problem}")


def main():
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(event, dict):
            raise ValueError("event is not a JSON object")
    except Exception as exc:  # an event we cannot read must not let the call through
        block(f"🔒 rnd-os security gate — לא ניתן לקרוא את האירוע ({type(exc).__name__}) — חוסם כברירת מחדל.")
    try:
        check(event)
    except SystemExit:
        raise
    except Exception as exc:  # a check that crashes must not let the call through
        block(f"🔒 rnd-os security gate — הבדיקה נכשלה ({type(exc).__name__}: {exc}) — חוסם כברירת מחדל.")
    sys.exit(0)


if __name__ == "__main__":
    main()
