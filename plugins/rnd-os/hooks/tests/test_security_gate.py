"""Tries the known bypasses of the rnd-os security gate, and the everyday work it must keep allowing.

Run: python3 plugins/rnd-os/hooks/tests/test_security_gate.py
Every case sends a real PreToolUse event to the hook and checks its exit code (2 = blocked) and,
for blocks, the reason — so a crash (which also blocks) cannot pass as a correct block.
When a new bypass is found, add it here before fixing the hook.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

GATE = Path(__file__).resolve().parent.parent / "security-gate.py"
SEC = "rnd-os:security-lead"
LEAD = "rnd-os:rnd-lead"
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

# Reasons, as they appear in the hook's messages.
RECORD = "רק rnd-os:security-lead"
DEPLOY = "פקודת שחרור"
PUSH = "push לענף מוגן"
PIN = "מיזוג PR חסום בלי commit"
MCP_PIN = "expectedHeadSha"
DELETE = "מחיקת ענף מוגן"
CONTROL = "שינוי הגנות production"
API = "דרך ה-API של GitHub"
LINK = "קישור לתיקייה אחרת"
UNREADABLE = "לא ניתן לקרוא את האירוע"
NO_APPROVAL = "אין אישור אבטחה"
failures = []


def run(tool, tool_input, cwd, agent=None, raw=None):
    event = {"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd)}
    if agent:
        event["agent_type"] = agent
    data = raw if raw is not None else json.dumps(event).encode()
    p = subprocess.run([sys.executable, str(GATE)], input=data, capture_output=True)
    return p.returncode, p.stderr.decode("utf-8", "replace")


def case(name, want, result, reason=None):
    got, err = result
    ok = got == want and (want == 0 or reason is None or reason in err)
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> exit {got}, want {want} ({reason}): {err.strip()[:300]}"))
    if not ok:
        failures.append(name)


def bash(cmd, cwd, agent=LEAD):
    return run("Bash", {"command": cmd}, cwd, agent)


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=ENV)


def rev(repo, ref="HEAD"):
    return subprocess.run(["git", "-C", str(repo), "rev-parse", ref], capture_output=True, text=True).stdout.strip()


def commit(repo, rel="app.js", text="x\n", message="change"):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", message)


def approve(repo):
    d = repo / "rnd/security/approvals"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rev(repo)}.md").write_text("verdict: APPROVED\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "approval")


def main(root):
    def new_repo(branch="work", parent=None):
        repo = Path(tempfile.mkdtemp(dir=parent or root))
        git(repo, "init", "-q", "-b", branch)
        commit(repo, "app.js", "console.log(1)\n", "init")
        return repo

    repo = new_repo()
    (repo / "rnd/security/approvals").mkdir(parents=True)
    sha = rev(repo)
    rel = f"rnd/security/approvals/{sha}.md"
    body = {"content": "verdict: APPROVED\n"}

    print("== approval records: who may write them")
    case("security-lead writes a record", 0, run("Write", {"file_path": str(repo / rel), **body}, repo, SEC))
    case("rnd-lead writes a record", 2, run("Write", {"file_path": str(repo / rel), **body}, repo, LEAD), RECORD)
    case("main session writes a record", 2, run("Write", {"file_path": str(repo / rel), **body}, repo), RECORD)
    case("agent from another plugin named security-lead", 2, run("Write", {"file_path": str(repo / rel), **body}, repo, "venture:security-lead"), RECORD)
    case("project agent named security-lead", 2, run("Write", {"file_path": str(repo / rel), **body}, repo, "security-lead"), RECORD)

    print("== approval records: path tricks")
    case("double slash", 2, run("Write", {"file_path": f"{repo}/rnd/security//approvals/{sha}.md", **body}, repo), RECORD)
    case("dot segment, relative path", 2, run("Write", {"file_path": f"rnd/./security/approvals/{sha}.md", **body}, repo), RECORD)
    case("dot-dot segment", 2, run("Write", {"file_path": f"{repo}/rnd/x/../security/approvals/{sha}.md", **body}, repo), RECORD)
    case("other letter case", 2, run("Write", {"file_path": f"{repo}/RND/Security/Approvals/{sha}.md", **body}, repo), RECORD)
    case("Windows backslashes", 2, run("Write", {"file_path": f"C:\\v\\rnd\\security\\approvals\\{sha}.md", **body}, repo), RECORD)
    os.symlink(repo / "rnd/security", repo / "sec")
    case("through a symlinked folder", 2, run("Write", {"file_path": f"{repo}/sec/approvals/{sha}.md", **body}, repo), RECORD)
    case("Edit of a record", 2, run("Edit", {"file_path": str(repo / rel), "old_string": "a", "new_string": "b"}, repo), RECORD)
    case("MCP filesystem write to a record", 2, run("mcp__fs__write_file", {"path": str(repo / rel), "content": "x"}, repo), RECORD)
    case("GitHub MCP push_files carrying a record", 2, run("mcp__github__push_files", {"branch": "feature/x", "files": [{"path": rel, "content": "verdict: APPROVED"}]}, repo), RECORD)

    print("== approval records: shell")
    for name, cmd in (
        ("cd into the folder then redirect", f"cd rnd/security/approvals && printf 'verdict: APPROVED' > {sha}.md"),
        ("append with quotes", f"echo 'verdict: APPROVED' >> \"rnd/security/approvals/{sha}.md\""),
        ("tee", f"echo x | tee rnd/security/approvals/{sha}.md"),
        ("tee with brace expansion", f"printf 'verdict: APPROVED\\n' | tee rnd/security/{{approvals,x}}/{sha}.md"),
        ("python open()", f"python3 -c \"open('rnd/security/approvals/{sha}.md','w').write('verdict: APPROVED')\""),
        ("cp with a glob toward the folder", "cp /tmp/a.md rnd/security/approv*/"),
        ("cp with globs on both folders", f"cp /tmp/{sha}.md rnd/secur*/approv*/"),
        ("cd to the parent then glob", f"cd rnd/security && cp /tmp/{sha}.md approv*/"),
        ("backslash inside the word", f"cp /tmp/{sha}.md rnd/security/appro\\vals/"),
        ("recursive copy of a prepared tree into rnd/security", "cp -r /tmp/f/. rnd/security/"),
        ("double slash in shell", f"cp /tmp/a.md rnd/security//approvals/{sha}.md"),
        ("git checkout from another branch", "git checkout other -- rnd/security/approvals"),
        ("git log --output writes a record", f"git log -1 --format='tformat:verdict: APPROVED' --output=rnd/security/approvals/{sha}.md"),
        ("git -c runs a command during status", f"git -c core.fsmonitor='cp /tmp/{sha}.md rnd/security/approvals/{sha}.md' status"),
        ("background & hides the second command", f"true & cp /tmp/x.md rnd/security/approvals/{sha}.md"),
        ("line continuation before the path", f"cp /tmp/x.md \\\n  rnd/security/approvals/{sha}.md"),
        ("heredoc", f"cat > rnd/security/approvals/{sha}.md <<EOF\nverdict: APPROVED\nEOF"),
        ("env prefix", "X=1 ls rnd/security/approvals"),
        ("command substitution", "ls $(echo rnd/security/approvals)"),
    ):
        case(name, 2, bash(cmd, repo), RECORD)
    case("security-lead redirects into the folder", 0, bash(f"printf 'verdict: APPROVED' > rnd/security/approvals/{sha}.md", repo, SEC))

    print("== approval records: reading and everyday work stay allowed")
    (repo / "rnd/backlog.md").write_text("")
    for name, cmd in (
        ("ls the folder", "ls rnd/security/approvals"),
        ("ls to /dev/null", "ls rnd/security/approvals >/dev/null"),
        ("cat with 2>/dev/null", f"cat rnd/security/approvals/{sha}.md 2>/dev/null"),
        ("grep with 2>&1", "grep -r verdict rnd/security/approvals 2>&1"),
        ("git add, commit and push a record on a feature branch",
         "git add rnd/security/approvals && git commit -m 'security approval' && git push -u origin feature/x"),
        ("commit message with a semicolon", "git add rnd/security/approvals && git commit -m \"security approval; release 1.2\""),
        ("commit message from a heredoc", "git add rnd && git commit -m \"$(cat <<'MSG'\nsecurity approval\n\nCo-Authored-By: x\nMSG\n)\""),
        ("git -C log of the folder", f"git -C {repo} log -- rnd/security/approvals"),
        ("unrelated redirect", "echo hi > notes.txt"),
        ("branch named approvals", "git checkout -b feature/approvals"),
        ("feature folder named approvals", "mkdir -p src/features/approvals && npm test -- approvals"),
        ("lint a security folder", "npx eslint src/security/*.ts"),
        ("create other rnd folders", "mkdir -p rnd/decisions rnd/ops"),
        ("find inside rnd", "find rnd -name '*.md'"),
        ("append to a backlog inside rnd", "cd rnd && echo \"- item\" >> backlog.md"),
    ):
        case(name, 0, bash(cmd, repo))
    case("prettier with cwd inside rnd", 0, bash("npx prettier --write .", repo / "rnd"))
    case("Write a policy doc next to it", 0, run("Write", {"file_path": f"{repo}/docs/security/approvals-policy.md", **body}, repo))
    case("PR body mentions the record", 0, run("mcp__github__create_pull_request", {"title": "x", "body": f"Record: {rel}"}, repo))
    case("Notion page mentions the record", 0, run("mcp__notion__notion-update-page", {"content": f"see {rel}"}, repo))

    print("== production actions without approval")
    for name, cmd, reason in (
        ("vercel --prod", "vercel --prod", DEPLOY),
        ("vercel with quoted flag", "vercel '--prod'", DEPLOY),
        ("vercel --prod after a line continuation", "vercel deploy \\\n  --prod", DEPLOY),
        ("vercel --target=production", "vercel deploy --target=production", DEPLOY),
        ("vercel rollback", "vercel rollback", DEPLOY),
        ("vercel alias", "vercel alias set x.vercel.app example.com", DEPLOY),
        ("bash -c wrapper", "bash -c 'vercel deploy --prod'", DEPLOY),
        ("npm run deploy", "npm run deploy", DEPLOY),
        ("npm run with quoted script", "npm run 'deploy'", DEPLOY),
        ("yarn release:prod", "yarn release:prod", DEPLOY),
        ("pnpm -F web run deploy", "pnpm -F web run deploy", DEPLOY),
        ("npm --prefix web run deploy", "npm --prefix web run deploy", DEPLOY),
        ("yarn workspace web deploy", "yarn workspace web deploy", DEPLOY),
        ("make deploy", "make deploy", DEPLOY),
        ("make -C infra deploy", "make -C infra deploy", DEPLOY),
        ("./scripts/deploy.sh", "./scripts/deploy.sh", DEPLOY),
        ("env prefix before a deploy script", "VERCEL_ENV=production ./scripts/deploy.sh", DEPLOY),
        ("bash -x deploy script", "bash -x scripts/deploy.sh", DEPLOY),
        ("node with flags runs a deploy script", "node --env-file=.env scripts/deploy.mjs", DEPLOY),
        ("bash scripts/release.sh", "bash scripts/release.sh", DEPLOY),
        ("chained deploy script", "npm test && ./deploy.sh", DEPLOY),
        ("text piped into a shell", "echo 'vercel --prod' | bash", DEPLOY),
        ("gh pr merge without a pinned commit", "gh pr merge 12 --squash", PIN),
        ("gh workflow run", "gh workflow run deploy.yml", DEPLOY),
        ("gh api merge without sha", "gh api -X PUT repos/o/r/pulls/1/merge", PIN),
        ("push HEAD:refs/heads/main", "git push origin HEAD:refs/heads/main", PUSH),
        ("push HEAD:heads/main", "git push origin HEAD:heads/main", PUSH),
        ("push delete main", "git push origin :main", DELETE),
        ("push --delete main", "git push origin --delete main", DELETE),
        ("git push inside bash -c", "bash -c 'git push origin main'", PUSH),
        ("git push inside sh -c", "sh -c \"git push origin main\"", PUSH),
        ("git push inside bash -lc", "bash -lc 'git push origin main'", PUSH),
        ("git push inside bash -l -c", "bash -l -c 'git push origin main'", PUSH),
        ("git push after tests inside bash -c", "bash -c 'npm test && git push origin main'", PUSH),
        ("git by full path", "/usr/bin/git push origin main", PUSH),
        ("git push after a line continuation", "git push origin \\\n  main", PUSH),
        ("cd to a missing home, then deploy", "cd ~nosuchuser_zz; vercel --prod", DEPLOY),
    ):
        case(name, 2, bash(cmd, repo), reason)
    case("GitHub MCP merge without a pin", 2, run("mcp__github__merge_pull_request", {"pullNumber": 1}, repo), MCP_PIN)
    case("GitHub MCP merge with a key the server ignores", 2, run("mcp__github__merge_pull_request", {"pullNumber": 1, "sha": sha}, repo), MCP_PIN)
    case("Supabase MCP merge_branch", 2, run("mcp__supabase__merge_branch", {"branch_id": "x"}, repo), MCP_PIN)

    print("== the approval must cover the commit that ships")
    split = new_repo("main")
    git(split, "checkout", "-q", "-b", "work")
    git(split, "checkout", "-q", "main")
    commit(split, "app.js", "unreviewed\n", "unreviewed on main")
    git(split, "checkout", "-q", "work")
    approve(split)
    approved_work = rev(split)
    unreviewed = rev(split, "main")
    case("on approved work: push local main", 2, bash("git push origin main", split), PUSH)
    case("on approved work: push main~0:main", 2, bash("git push origin main~0:main", split), PUSH)
    case("on approved work: push work:main", 0, bash("git push origin work:main", split))
    case("on approved work: glob refspec pushes main too", 2, bash("git push origin 'refs/heads/*:refs/heads/*'", split), PUSH)
    case("on approved work: matching refspec", 2, bash("git push origin :", split), PUSH)
    case("gh pr merge pinned to the approved commit", 0, bash(f"gh pr merge 99 --squash --match-head-commit {approved_work}", split))
    case("gh pr merge pinned to another commit", 2, bash(f"gh pr merge 99 --match-head-commit {unreviewed}", split), NO_APPROVAL)
    case("second merge borrows the first one's pin", 2, bash(f"gh pr merge 5 --match-head-commit {approved_work} && gh pr merge 6 --squash", split), PIN)
    case("pin smuggled in an echo", 2, bash(f"gh pr merge 6 --squash; echo sha={approved_work}", split), PIN)
    case("gh api merge with approved sha", 0, bash(f"gh api -X PUT repos/o/r/pulls/1/merge -f sha={approved_work}", split))
    case("GitHub MCP merge pinned with expectedHeadSha", 0, run("mcp__github__merge_pull_request", {"owner": "o", "repo": "r", "pullNumber": 1, "expectedHeadSha": approved_work}, split))

    main_repo = new_repo("main")
    workspace = Path(tempfile.mkdtemp(dir=root))
    app = new_repo("main", parent=workspace)
    case("push HEAD while on main", 2, bash("git push origin HEAD", main_repo), PUSH)
    case("plain push while on main", 2, bash("git push", main_repo), PUSH)
    case("push option value is not the remote", 2, bash("git push -o ci.skip origin", main_repo), PUSH)
    case("git -C a repo on main from a feature cwd", 2, bash(f"git -C {main_repo} push", repo), PUSH)
    case("sh -c fetch then push HEAD on main", 2, bash("sh -c \"git fetch; git push origin HEAD\"", main_repo), PUSH)
    case("cd into an app on main, then push", 2, bash(f"cd {app.name} && git push", workspace), PUSH)
    case("cd into an app on main, then push HEAD", 2, bash(f"cd {app.name} && git push origin HEAD", workspace), PUSH)
    flags = " ".join(f"--flag-{i}" for i in range(40))
    started = time.monotonic()
    case("40 npm flags then a push stays fast and blocked", 2, bash(f"npm {flags} test && git push origin main", main_repo), PUSH)
    elapsed = time.monotonic() - started
    case(f"40 npm flags took {elapsed:.1f}s (limit 5s)", 0, (0 if elapsed < 5 else 1, ""))

    print("== everyday work stays allowed")
    for name, cmd in (
        ("npm run build", "npm run build"),
        ("npm install release-it", "npm install release-it"),
        ("npm run release:dry-run", "npm run release:dry-run"),
        ("npm run release-notes", "npm run release-notes"),
        ("npm run shipping:seed", "npm run shipping:seed"),
        ("node --test a shipping test", "node --test src/shipping.test.js"),
        ("python release notes script", "python3 scripts/release_notes.py"),
        ("pnpm's own deploy packages a folder", "pnpm deploy --filter api ./out"),
        ("reading a deploy script", "cat scripts/deploy.sh"),
        ("editing a deploy script", "chmod +x scripts/deploy.sh"),
        ("searching docs for a deploy command", "rg -n \"npm run deploy\" docs"),
        ("gh api read of protection", "gh api repos/o/r/branches/main/protection"),
        ("gh api read of deployments", "gh api repos/o/r/deployments"),
        ("gh ruleset view", "gh ruleset view 1"),
        ("gh pr create", "gh pr create --fill"),
        ("PR body mentions make deploy", "gh pr create --body \"after merge run make deploy\""),
        ("PR body mentions gh pr merge", "gh pr create --body \"merge with gh pr merge --squash\""),
        ("push a feature branch", "git push -u origin feature/x"),
        ("push a branch ending in release", "git push -u origin feature/release"),
        ("push a branch ending in main", "git push origin fix/main"),
        ("push HEAD on a feature branch", "git push origin HEAD"),
        ("preview deploy", "vercel deploy"),
        ("commit message mentioning vercel --prod", "git commit -m \"docs: explain how to run vercel --prod\""),
        ("commit message with 'alias.'", "git commit -m 'Add path alias. Update tsconfig'"),
        ("commit message starting with config", "git commit -m \"config: add import alias. closes #3\""),
        ("git add alias.ts", "git add src/alias.ts"),
        ("git diff tsconfig.alias.json", "git diff tsconfig.alias.json"),
        ("commit message mentioning push", "git commit -m 'push notifications'"),
    ):
        case(name, 0, bash(cmd, repo))
    case("GitHub MCP push_files to a feature branch", 0, run("mcp__github__push_files", {"branch": "feature/x"}, repo))
    case("GitHub MCP push_files to feature/release", 0, run("mcp__github__push_files", {"branch": "feature/release"}, repo))
    case("ClickUp merge tasks", 0, run("mcp__x__clickup_merge_tasks", {}, repo))
    case("Canva merge designs", 0, run("mcp__x__merge-designs", {}, repo))
    case("GitHub MCP list environments", 0, run("mcp__github__list_environments", {}, repo))
    case("Stripe webhook endpoint", 0, run("mcp__stripe__create_webhook_endpoint", {}, repo))
    case("Sentry environment update", 0, run("mcp__sentry__update_environment", {}, repo))

    print("== hosting-side protection and API branch writes: blocked even with approval")
    approved = new_repo()
    approve(approved)
    case("approved commit deploys", 0, bash("vercel --prod", approved))
    for name, cmd, reason in (
        ("delete branch protection", "gh api -X DELETE repos/o/r/branches/main/protection", CONTROL),
        ("delete branch protection, quoted method", "gh api -X 'DELETE' repos/o/r/branches/main/protection", CONTROL),
        ("delete branch protection after a line continuation", "gh api -X DELETE \\\n  repos/o/r/branches/main/protection", CONTROL),
        ("approve a pending deployment", "gh api repos/o/r/actions/runs/1/pending_deployments -f state=approved", CONTROL),
        ("approve a pending deployment, attached field values", "gh api repos/o/r/actions/runs/1/pending_deployments -fstate=approved -Fenvironment_ids[]=1", CONTROL),
        ("edit an environment", "gh api --method PUT repos/o/r/environments/production", CONTROL),
        ("set a secret", "gh secret set VERCEL_TOKEN", CONTROL),
        ("repo edit", "gh repo edit --default-branch x", CONTROL),
        ("vercel env add", "vercel env add API_KEY production", CONTROL),
        ("git alias hides a push", "git config alias.ship push", CONTROL),
        ("one-off git alias", "git -c alias.x=push x origin main", CONTROL),
        ("move main through the refs API", "gh api repos/o/r/git/refs/heads/main -X PATCH -f sha=abc", API),
        ("write a file on main through the contents API", "gh api repos/o/r/contents/a.js -X PUT -f branch=main -f content=x", API),
        ("merge through GraphQL", "gh api graphql -f query='mutation{mergePullRequest(input:{pullRequestId:\"x\"}){clientMutationId}}'", API),
    ):
        case(name, 2, bash(cmd, approved), reason)
    case("MCP branch protection update", 2, run("mcp__github__update_branch_protection", {}, approved), CONTROL)
    case("GitHub MCP push_files to main", 2, run("mcp__github__push_files", {"branch": "main", "files": []}, approved), API)

    print("== unreadable events block")
    case("invalid UTF-8 with a deploy", 2, run("", {}, repo, raw=b'{"tool_name":"Bash","tool_input":{"command":"\xff vercel --prod"}}'), UNREADABLE)
    case("event is not an object", 2, run("", {}, repo, raw=b"[]"), UNREADABLE)

    print("== approval records read only from the real folder")
    linked = new_repo()
    elsewhere = Path(tempfile.mkdtemp(dir=root))
    (elsewhere / f"{rev(linked)}.md").write_text("verdict: APPROVED\n")
    (linked / "rnd/security").mkdir(parents=True)
    os.symlink(elsewhere, linked / "rnd/security/approvals")
    case("approvals folder symlinked elsewhere", 2, bash("vercel --prod", linked), LINK)
    rec_link = new_repo()
    (rec_link / "rnd/security/approvals").mkdir(parents=True)
    os.symlink(elsewhere / f"{rev(linked)}.md", rec_link / f"rnd/security/approvals/{rev(rec_link)}.md")
    case("record file symlinked elsewhere", 2, bash("vercel --prod", rec_link), NO_APPROVAL)

    print("== design/ media and documents keep an approval; code under design/ does not")
    d = new_repo()
    approve(d)
    for rel_path in ("design/assets/ad/ad.png", "design/x.PNG", "design/assets/ad/record.md"):
        commit(d, rel_path, "data")
    case("committed media and documents under design/", 0, bash("vercel --prod", d))
    (d / "design/assets/ad/draft.webp").write_text("x")
    case("uncommitted design media", 0, bash("vercel --prod", d))
    (d / "design/assets/ad/draft.webp").unlink()
    commit(d, "design/ui/index.html", "<script>x</script>")
    case("committed HTML under design/ needs a new approval", 2, bash("vercel --prod", d), NO_APPROVAL)
    approve(d)
    (d / "app.js").write_text("changed\n")
    case("uncommitted product code blocks a deploy", 2, bash("vercel --prod", d), "שינויים שלא נכנסו")


if __name__ == "__main__":
    root = tempfile.mkdtemp(prefix="rnd-gate-test-")
    try:
        main(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)
