#!/usr/bin/env python3
"""rnd-os:verify — every check runs once per version, and every reviewer reads the same results.

Runs on a clean copy of one commit (git archive), never on the working tree:
  1. change map   — which domains the change base..head touches (rnd/domains.json in the repo; an
                    unmapped file counts as every domain), and the reviewers that suggests
  2. tripwire     — added lines that touch secrets or the outside world (env reads, token names, new
                    hosts, server/config paths, publish/token instructions) → security-lead now
  3. checks       — the project's own package.json scripts (test, test:browser; with --release also
                    check:release, test:load, test:stability), each under a memory cap and a timeout,
                    with a throwaway HOME so new code can't read the CLI logins in the real one
Writes summary.md / summary.json to ~/.cache/business-os/verify/<repo>/<key>/. The key covers the head
and base commits, this script's version, domains.json and every external input passed with --input, so
a result is reused only when nothing it depends on changed. A run with a failure is never reused.

Usage:
  verify.py <repo> [--commit SHA] [--base SHA] [--release] [--live-sha SHA] [--input FILE ...]
            [--map-only] [--timeout SECONDS] [--out DIR]
  --base      what the change is compared with. Default: `main` (for a release: the live commit).
  --release   release mode: base = --live-sha (or the commit Netlify reports as published, via
              --netlify-site); unknown → the empty tree, so the whole product counts as changed.
Exit: 0 all ran and passed · 1 a check failed · 2 could not run / nothing ran / no change · 3 tripwire lit
(checks passed) — the caller sends that diff to security-lead before anything runs with real secrets.
"""
import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

VERSION = "1"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
ALL = "*"
DEV_SCRIPTS = ("test", "test:browser")
RELEASE_SCRIPTS = ("check:release", "check:deploy", "test:load", "test:stability")
REVIEWERS = {
    "code": ["rnd-os:code-reviewer"],
    "tests": ["rnd-os:code-reviewer"],
    "security": ["rnd-os:security-lead"],
    "privacy": ["legal-os:legal-lead"],
    "customer_text": ["legal-os:legal-lead", "marketing-os:marketing-lead"],
    "design": ["design-os:design-lead"],
    "release_config": ["rnd-os:security-lead", "rnd-os:devops-engineer"],
    "docs": [],
}
# Tripwire: what in an added line means "this may touch a secret or the outside world".
TRIP_PATTERNS = [
    ("env_read", re.compile(r"process\.env\b|Deno\.env|Netlify\.env|\.env\.get\(|os\.environ|getenv\(|import\.meta\.env")),
    ("secret_name", re.compile(r"\b[A-Z0-9_]*(TOKEN|SECRET|API_KEY|PASSWORD|PRIVATE_KEY|AUTH)[A-Z0-9_]*\b|\bBearer\b")),
    ("publish_instruction", re.compile(r"netlify(-cli)?\s+(deploy|api|env)|--prod\b|personal access token|טוקן", re.I)),
]
URL = re.compile(r"https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
# a URL counts as "the code sends there" only in code; a link in page text is the legal reviewers' business
CODE_EXT = (".mjs", ".js", ".cjs", ".ts", ".gs", ".py", ".toml", ".sh")


def git(repo, *args, check=True):
    # core.quotePath=false: a Hebrew file name stays a name, not "\327\251…" that no rule matches
    r = subprocess.run(["git", "-c", "core.quotePath=false", "-C", str(repo), *args], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def resolve(repo, ref):
    return git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def load_config(repo, sha):
    """rnd/domains.json at that commit. Missing → None (every file is unmapped, so every domain)."""
    r = subprocess.run(["git", "-C", str(repo), "show", f"{sha}:rnd/domains.json"], capture_output=True, text=True)
    if r.returncode:
        return None
    return json.loads(r.stdout)


def glob_match(path, pattern):
    """fnmatch with ** spanning directories and a bare name matching at any depth."""
    if "/" not in pattern:
        return fnmatch.fnmatch(path.rsplit("/", 1)[-1], pattern)
    rx = re.escape(pattern).replace(r"\*\*/", "(?:.*/)?").replace(r"\*\*", ".*").replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
    return re.fullmatch(rx, path) is not None


def domains_of(path, config):
    """First matching rule wins (put specific rules first). No rule → every domain."""
    for rule in (config or {}).get("paths", []):
        if any(glob_match(path, p) for p in ([rule["match"]] if isinstance(rule["match"], str) else rule["match"])):
            return set(rule["domains"])
    return {ALL}


def change_map(repo, base, head, config):
    files = [f for f in git(repo, "diff", "--name-only", "--no-renames", base, head).splitlines() if f]
    per_file = {f: sorted(domains_of(f, config)) for f in files}
    touched = set().union(*map(set, per_file.values())) if per_file else set()
    unmapped = sorted(f for f, d in per_file.items() if ALL in d)
    if ALL in touched:
        touched = set(REVIEWERS) | (touched - {ALL})
    reviewers = sorted({r for d in touched for r in REVIEWERS.get(d, [])})
    return {"files": per_file, "domains": sorted(touched), "unmapped": unmapped, "reviewers": reviewers}


def tripwire(repo, base, head, config):
    """Hits in added lines only — a removed secret read is not a new risk."""
    cfg = config or {}
    paths = cfg.get("tripwire_paths", [])
    # e.g. tests/**: they run inside verify with a clean env (no secrets to read) and a throwaway HOME
    ignore = cfg.get("tripwire_ignore", [])
    known = set(cfg.get("known_hosts", []))
    removed_hosts, hits = set(), []
    # --no-renames: a moved file is a delete plus an add, so every line of it is "added" (a spike moved into
    # netlify/functions becomes a live endpoint — that must light, not vanish as a pure rename)
    diff = git(repo, "diff", "-U0", "--no-color", "--no-ext-diff", "--no-renames", base, head)
    for line in diff.splitlines():
        if line.startswith("-") and not line.startswith("---"):
            removed_hosts |= set(URL.findall(line))
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
            if current and any(glob_match(current, p) for p in ignore):
                current = None
            if current and any(glob_match(current, p) for p in paths):
                hits.append({"file": current, "why": "path", "line": ""})
            continue
        if not current or not line.startswith("+") or line.startswith("+++"):
            continue
        text = line[1:]
        for name, rx in TRIP_PATTERNS:
            if rx.search(text):
                hits.append({"file": current, "why": name, "line": text.strip()[:160]})
        for host in (URL.findall(text) if current.endswith(CODE_EXT) or current.endswith("_headers") else []):
            if host not in known and host not in removed_hosts and not host.endswith((".example", "example.com", ".test", ".invalid", ".localhost")):
                hits.append({"file": current, "why": f"new_host {host}", "line": text.strip()[:160]})
    return hits


def live_from_netlify(site_id, home):
    """The commit Netlify serves: the published deploy's title, set by `deploy --message <sha>`. Read-only."""
    r = subprocess.run(["npx", "--yes", "netlify-cli@27.10.0", "api", "getSite", "--data",
                        json.dumps({"site_id": site_id})], capture_output=True, text=True,
                       env={**os.environ, "HOME": home}, timeout=120)
    if r.returncode:
        return None
    return parse_published_sha(r.stdout)


def parse_published_sha(site_json):
    try:
        deploy = json.loads(site_json).get("published_deploy") or {}
    except ValueError:
        return None
    m = re.search(r"\b[0-9a-f]{40}\b", deploy.get("title") or "")
    return m.group(0) if m else None


def extract(repo, sha, dest):
    archive = subprocess.run(["git", "-C", str(repo), "archive", sha], capture_output=True)
    if archive.returncode:
        raise RuntimeError(f"git archive {sha}: {archive.stderr.decode(errors='replace').strip()}")
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive.stdout, check=True)


def run_scripts(repo, sha, scripts, timeout, workdir):
    """Each script in a clean archive, a throwaway HOME, a 3G memory cap and a timeout."""
    src = workdir / "src"
    src.mkdir()
    extract(repo, sha, src)
    pkg = src / "package.json"
    have = json.loads(pkg.read_text())["scripts"] if pkg.exists() else {}
    home = workdir / "home"
    home.mkdir()
    real_home = os.environ.get("HOME", "")
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "LANG": "C.UTF-8",
           "npm_config_cache": str(workdir / "npm-cache"),
           # systemd-run --user reaches the user manager through these; they hold no credentials
           **{k: os.environ[k] for k in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS") if k in os.environ},
           # browser tests use the shared, already-installed playwright-core and its browsers (binaries, no
           # credentials); the paths are passed, so HOME can stay a throwaway
           "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", f"{real_home}/.cache/ms-playwright"),
           "PLAYWRIGHT_CORE": os.environ.get("PLAYWRIGHT_CORE",
                                             f"{real_home}/.cache/business-os/screen-check/node_modules/playwright-core/index.mjs")}
    capped = shutil.which("systemd-run") is not None
    results = []
    for name in scripts:
        if name not in have:
            results.append({"check": name, "status": "not run", "why": "no such script in package.json"})
            continue
        cmd = ["npm", "run", "--silent", name]
        limit = timeout * 3 if name == "test:stability" else timeout  # the suite several times over
        if capped:
            cmd = ["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=3G", "-p", "MemorySwapMax=0",
                   "timeout", str(limit), *cmd]
        t0 = time.time()
        log = workdir / f"{name.replace(':', '-')}.log"
        with open(log, "w") as out:
            try:  # the timeout holds without systemd-run too
                r = subprocess.run(cmd, cwd=src, env=env, stdout=out, stderr=subprocess.STDOUT, timeout=limit + 30)
                code = r.returncode
            except subprocess.TimeoutExpired:
                code = "timeout"
        results.append({"check": name, "status": "pass" if code == 0 else "fail",
                        "exit": code, "seconds": round(time.time() - t0), "log": str(log)})
    return results


def approved_check(pages, drafts, legal_dir=None, base_pages=None):
    """The approved-wording check (approved_text.py): blocks on the pages, rulings in force, no uncovered new text."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("approved_text", pathlib.Path(__file__).with_name("approved_text.py"))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    status, why = mod.verdict(mod.check(pages, drafts, legal_dir, base_pages))
    return {"check": "approved-text", "status": status, "why": why}


def cache_key(head, base, config_text, inputs):
    h = hashlib.sha256(f"{VERSION}\n{head}\n{base}\n{config_text}".encode())
    for p in sorted(inputs):
        h.update(p.encode() + b"\0" + pathlib.Path(p).read_bytes())
    return h.hexdigest()[:16]


def summary_md(s):
    lines = [f"# verify — {s['repo']} @ {s['head'][:12]}", "",
             f"- בסיס: `{s['base'][:12]}` ({s['base_source']}) · מצב: {'שחרור' if s['release'] else 'סבב'} · {s['time']}",
             f"- תחומים שנגעו: {', '.join(s['map']['domains']) or 'אין'}",
             f"- בודקים מוצעים (המפה רק מוסיפה; ראש המחלקה מחליט): {', '.join(s['map']['reviewers']) or 'אין'}"]
    if s["map"]["unmapped"]:
        lines.append(f"- קבצים לא ממופים (נחשבים כל התחומים): {', '.join(s['map']['unmapped'][:20])}")
    lines += ["", "## חוט מעידה אבטחה", ""]
    if s["tripwire"]:
        lines.append("**נדלק** — `security-lead` על ה-diff הזה לפני שמשהו רץ עם סוד או חשבון אמיתי:")
        lines += [f"- `{h['file']}` — {h['why']}" + (f": `{h['line']}`" if h["line"] else "") for h in s["tripwire"][:40]]
    else:
        lines.append("לא נדלק.")
    lines += ["", "## בדיקות", "", "| בדיקה | תוצאה | שניות | לוג |", "|---|---|---|---|"]
    for r in s["checks"]:
        lines.append(f"| {r['check']} | {r['status']} | {r.get('seconds', '')} | {r.get('log', r.get('why', ''))} |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="rnd-os:verify")
    ap.add_argument("repo")
    ap.add_argument("--commit", default="HEAD")
    ap.add_argument("--base")
    ap.add_argument("--release", action="store_true")
    ap.add_argument("--live-sha")
    ap.add_argument("--netlify-site")
    ap.add_argument("--input", action="append", default=[], help="external file a check reads (part of the key)")
    ap.add_argument("--approved", action="append", default=[], help="drafts file with ```approved id=…``` blocks (legal); part of the key")
    ap.add_argument("--legal-dir", help="the venture's legal folder: the approved-text check reads which rulings are in force")
    ap.add_argument("--map-only", action="store_true")
    ap.add_argument("--domains", help="a domains.json to use instead of the one in the commit (replays, repos not set up yet)")
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--out", default=str(pathlib.Path.home() / ".cache" / "business-os" / "verify"))
    a = ap.parse_args(argv)
    repo = pathlib.Path(a.repo).resolve()
    try:
        head = resolve(repo, a.commit)
        if a.base:
            base, base_source = resolve(repo, a.base), "given"
        elif a.release:
            live = a.live_sha or (live_from_netlify(a.netlify_site, os.environ.get("HOME", "")) if a.netlify_site else None)
            base, base_source = (resolve(repo, live), "live") if live else (EMPTY_TREE, "live unknown — whole tree")
        else:
            base, base_source = resolve(repo, "main"), "main"
    except (RuntimeError, OSError) as e:
        print(f"verify: {e}", file=sys.stderr)
        return 2
    try:
        config = json.loads(pathlib.Path(a.domains).read_text()) if a.domains else load_config(repo, head)
        config_text = json.dumps(config, sort_keys=True) if config else ""
        a.input = a.input + [p for p in a.approved if p not in a.input]
        role = "A:" + "\0".join(sorted(a.approved)) + "L:" + (a.legal_dir or "")  # --input F and --approved F differ
        if a.legal_dir:
            a.input += [str(p) for p in sorted(pathlib.Path(a.legal_dir).glob("00 - *.md"))]
        key = cache_key(head, base, config_text + ("R" if a.release else "") + ("M" if a.map_only else "") + role, a.input)
    except (OSError, ValueError) as e:
        print(f"verify: could not read an input — {e}", file=sys.stderr)
        return 2
    if git(repo, "diff", "--name-only", base, head).strip() == "":
        print(f"verify: no change between {base[:12]} and {head[:12]} — nothing to map (wrong --base?)", file=sys.stderr)
        return 2
    scripts = DEV_SCRIPTS + (RELEASE_SCRIPTS if a.release else ())
    wanted = set() if a.map_only else set(scripts) | ({"approved-text"} if a.approved else set())
    out = pathlib.Path(a.out) / repo.name / key
    prev = out / "summary.json"
    if prev.exists():
        s = json.loads(prev.read_text())
        have = {r["check"] for r in s["checks"]}
        if wanted <= have and all(r["status"] in ("pass", "not run") for r in s["checks"]):
            print(f"verify: reused {out}/summary.md")
            return exit_code(s, a.map_only)
    map_ = change_map(repo, base, head, config)
    trip = tripwire(repo, base, head, config)
    checks = []
    if not a.map_only:
        with tempfile.TemporaryDirectory(prefix="verify-") as tmp:
            try:
                checks = run_scripts(repo, head, scripts, a.timeout, pathlib.Path(tmp))
                if a.approved:
                    pages_dir = (config or {}).get("pages_dir", "public")
                    base_pages = None
                    if base != EMPTY_TREE:  # unknown base → no base pages → all text is new → uncovered → fail
                        (pathlib.Path(tmp) / "base").mkdir()
                        extract(repo, base, pathlib.Path(tmp) / "base")
                        base_pages = pathlib.Path(tmp) / "base" / pages_dir
                    checks.append(approved_check(pathlib.Path(tmp) / "src" / pages_dir, a.approved, a.legal_dir, base_pages))
            except (RuntimeError, OSError, subprocess.CalledProcessError, ValueError) as e:
                print(f"verify: could not run — {e}", file=sys.stderr)
                return 2
            out.mkdir(parents=True, exist_ok=True)
            for r in checks:  # keep the logs with the summary
                if r.get("log") and pathlib.Path(r["log"]).exists():
                    dest = out / pathlib.Path(r["log"]).name
                    shutil.copy(r["log"], dest)
                    r["log"] = str(dest)
    s = {"repo": repo.name, "head": head, "base": base, "base_source": base_source, "release": a.release,
         "time": datetime.datetime.now().isoformat(timespec="seconds"), "version": VERSION,
         "map": map_, "tripwire": trip, "checks": checks, "inputs": a.input}
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1))
    (out / "summary.md").write_text(summary_md(s), encoding="utf-8")
    print(f"verify: {out}/summary.md")
    if trip:
        print("verify: tripwire lit — security-lead on this diff before anything runs with real secrets")
    return exit_code(s, a.map_only)


def exit_code(s, map_only):
    """1 a check failed (the tripwire is still printed and in the summary) · 2 nothing ran · 3 tripwire lit · 0 pass."""
    checks = s["checks"]
    if any(r["status"] == "fail" for r in checks):
        return 1
    if not map_only and not any(r["status"] == "pass" for r in checks):
        return 2
    return 3 if s["tripwire"] else 0


if __name__ == "__main__":
    sys.exit(main())
