#!/usr/bin/env python3
"""rnd-os:verify — the security stage: the scanners run by code, before any model reads the change.

  secrets   betterleaks over the change's commits (release/deep: the whole history of the head)
  sast      opengrep on clean copies of head and base; a finding is new unless the base has the same one
            (rule, file, line text). Deep: every finding in the head.
  headers   the CSP in the headers file(s): changed against the base, and csp_evaluator's high findings

Nothing that decides the result comes from the change itself: the betterleaks config and the rule pack are the
harness's, a project's allowlist and extra rules are read from the BASE commit, ignore files in the copies are
deleted, inline suppressions are switched off (and an added one lights the tripwire in verify.py). Every tool's
sha256 is checked against security/tools.json before each run; a missing or changed tool is "not run", and
verify.py blocks on that — a check that didn't run never passes.

Results: one dict per check {"check": "security:<name>", "status": "pass" | "fail" | "hits" | "not run", ...}.
A secret is "fail" (no model can wave it through). SAST and CSP findings are "hits": security-lead decides.

Install the pinned tools (no sudo): python3 security.py install
"""
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

HERE = pathlib.Path(os.environ.get("BOS_SECURITY_DIR", pathlib.Path(__file__).resolve().parent.parent / "security"))
MANIFEST = pathlib.Path(os.environ.get("BOS_TOOLS_MANIFEST", HERE / "tools.json"))
TOOLS = pathlib.Path(os.environ.get("BOS_TOOLS_DIR", pathlib.Path.home() / ".business-os" / "tools"))
ALLOW_PATH = "rnd/security/betterleaks-allow.toml"
PROJECT_RULES = "rnd/security/rules"
HEADER_FILES = ("public/_headers", "_headers")
IGNORE_FILES = (".semgrepignore", ".opengrepignore", ".gitleaksignore", ".betterleaksignore")
CSP_RE = re.compile(r"Content-Security-Policy:\s*(.+)", re.I)
CSP_JS = ("import {CspEvaluator} from 'csp_evaluator/dist/evaluator.js';"
          "import {CspParser} from 'csp_evaluator/dist/parser.js';"
          "const c = process.argv[1];"
          "console.log(JSON.stringify(new CspEvaluator(new CspParser(c).csp).evaluate()"
          ".filter(f => f.severity <= 30).map(f => ({severity: f.severity, directive: f.directive, value: f.value,"
          " why: f.description}))))")


def manifest():
    return json.loads(MANIFEST.read_text())


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tool(name):
    """(path, None) when the pinned binary is there and unchanged; (None, why) otherwise."""
    spec = manifest()[name]
    path = TOOLS / spec["path"]
    if not path.exists():
        return None, f"{name} {spec['version']} not installed — python3 {__file__} install"
    if sha256(path) != spec["sha256"]:
        return None, f"{name} at {path} does not match its pinned sha256 — reinstall: python3 {__file__} install"
    return path, None


def rules_dir():
    spec = manifest()["opengrep-rules"]
    path = TOOLS / spec["path"]
    if not (path / ".git").exists():
        return None, "opengrep-rules not fetched — python3 security.py install"
    head = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if head != spec["commit"]:
        return None, f"opengrep-rules at {head[:12]}, pinned {spec['commit'][:12]} — python3 security.py install"
    dirty = subprocess.run(["git", "-C", str(path), "status", "--porcelain"], capture_output=True, text=True).stdout
    if dirty.strip():
        return None, "opengrep-rules has local changes — python3 security.py install"
    return path, None


def csp_dir():
    path = TOOLS / manifest()["csp_evaluator"]["path"]
    pkg = path / "node_modules" / "csp_evaluator" / "package.json"
    want = manifest()["csp_evaluator"]["npm"].split("@")[-1]
    if not pkg.exists() or json.loads(pkg.read_text()).get("version") != want:
        return None, "csp_evaluator not installed — python3 security.py install"
    return path, None


def git_show(repo, sha, rel):
    r = subprocess.run(["git", "-C", str(repo), "show", f"{sha}:{rel}"], capture_output=True)
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None


def scrub(tree):
    """Ignore files in a copy decide nothing: they belong to the change under test."""
    for name in IGNORE_FILES:
        for p in pathlib.Path(tree).rglob(name):
            p.unlink()


# ---------- secrets ----------

def secrets(repo, base, head, full, workdir, empty_tree):
    path, why = tool("betterleaks")
    if not path:
        return {"check": "security:secrets", "status": "not run", "why": why}
    cfg = workdir / "betterleaks.toml"
    cfg.write_text((HERE / "betterleaks.toml").read_text() + "\n" + (git_show(repo, base, ALLOW_PATH) or "")
                   if base != empty_tree else (HERE / "betterleaks.toml").read_text())
    no_ignore = workdir / "no-ignore"
    no_ignore.mkdir(exist_ok=True)
    report = workdir / "betterleaks.json"
    log_opts = head if full or base == empty_tree else f"{base}..{head}"
    r = subprocess.run([str(path), "git", str(repo), "--log-opts", log_opts, "-c", str(cfg), "-i", str(no_ignore),
                        "--ignore-gitleaks-allow", "-f", "json", "-r", str(report), "--no-banner", "--redact"],
                       capture_output=True, text=True, timeout=900)
    if r.returncode not in (0, 1) or not report.exists():
        return {"check": "security:secrets", "status": "not run", "why": f"betterleaks exit {r.returncode}: {r.stderr[-300:]}"}
    hits = [{"rule": h.get("RuleID"), "file": h.get("File"), "line": h.get("StartLine"), "commit": (h.get("Commit") or "")[:12]}
            for h in json.loads(report.read_text() or "[]")]
    scope = "the head's whole history" if log_opts == head else f"{base[:12]}..{head[:12]}"
    return {"check": "security:secrets", "status": "fail" if hits else "pass", "scope": scope, "hits": hits,
            "why": f"{len(hits)} secret(s) — values not shown; see file:line" if hits else f"no secret in {scope}"}


# ---------- static analysis ----------

def rule_files(repo, base, empty_tree, workdir):
    rdir, why = rules_dir()
    if not rdir:
        return None, why
    files = [rdir / rel for rel in (HERE / "pack.txt").read_text().split()]
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        return None, f"pack rule missing: {missing[0]}"
    files += sorted((HERE / "rules").glob("*.yaml"))
    if base != empty_tree:  # the project's own rules, as they were before this change
        listing = subprocess.run(["git", "-C", str(repo), "ls-tree", "--name-only", f"{base}:{PROJECT_RULES}"],
                                 capture_output=True, text=True)
        for name in listing.stdout.split() if listing.returncode == 0 else []:
            if name.endswith((".yaml", ".yml")):
                dest = workdir / f"project-{name}"
                dest.write_text(git_show(repo, base, f"{PROJECT_RULES}/{name}") or "")
                files.append(dest)
    return files, None


def opengrep(path, files, tree):
    args = [str(path), "scan", "--quiet", "--json", "--disable-nosem", "--disable-version-check"]
    for f in files:
        args += ["-f", str(f)]
    r = subprocess.run(args + ["."], cwd=tree, capture_output=True, text=True, timeout=1200)
    try:
        d = json.loads(r.stdout)
    except ValueError:
        return None, f"opengrep exit {r.returncode}: {r.stderr[-300:]}"
    bad_rules = [e for e in d.get("errors", []) if "Rule parse" in str(e.get("type")) or "InvalidRule" in str(e.get("type"))]
    if bad_rules:
        return None, f"a rule did not load: {str(bad_rules[0].get('message'))[:200]}"
    gaps = sorted({s["file"] for e in d.get("errors", []) if "PartialParsing" in str(e.get("type"))
                   for s in (e.get("spans") or [])})
    return {"results": d.get("results", []), "parse_gaps": gaps}, None


def fingerprint(x):
    return (x["check_id"].split(".")[-1], x["path"], re.sub(r"\s+", " ", x["extra"].get("lines", "")).strip())


def sast(repo, base, src, base_src, full, workdir, empty_tree):
    path, why = tool("opengrep")
    files, why2 = rule_files(repo, base, empty_tree, workdir) if path else (None, why)
    if not path or not files:
        return {"check": "security:sast", "status": "not run", "why": why or why2 or "no rules to run"}
    now, err = opengrep(path, files, src)
    if err or now is None:
        return {"check": "security:sast", "status": "not run", "why": err}
    old = set()
    if not full and base_src is not None:
        before, err = opengrep(path, files, base_src)
        if err or before is None:
            return {"check": "security:sast", "status": "not run", "why": "base: " + err}
        old = {fingerprint(x) for x in before["results"]}
    hits = [{"rule": fingerprint(x)[0], "file": x["path"], "line": x["start"]["line"],
             "severity": x["extra"].get("severity", ""), "msg": x["extra"].get("message", "")[:240]}
            for x in now["results"] if fingerprint(x) not in old]
    hits.sort(key=lambda h: h["severity"] != "ERROR")  # the sharp ones first
    return {"check": "security:sast", "status": "hits" if hits else "pass", "hits": hits,
            "parse_gaps": now["parse_gaps"], "scope": "every finding" if full or base_src is None else "new in this change",
            "why": f"{len(hits)} finding(s) for security-lead" if hits else "no new finding"}


# ---------- headers ----------

def csp_of(tree):
    for rel in HEADER_FILES:
        p = pathlib.Path(tree) / rel
        if p.exists():
            m = CSP_RE.search(p.read_text(encoding="utf-8", errors="replace"))
            return rel, (m.group(1).strip() if m else "")
    return None, None


def headers(src, base_src):
    rel, after = csp_of(src)
    if rel is None:
        return {"check": "security:headers", "status": "pass", "why": "no headers file"}
    cdir, why = csp_dir()
    if not cdir:
        return {"check": "security:headers", "status": "not run", "why": why}
    hits = []
    _, before = csp_of(base_src) if base_src is not None else (None, None)
    if before is not None and before != after:
        hits.append({"rule": "csp-changed", "file": rel, "before": before, "after": after})
    if not after:
        hits.append({"rule": "csp-missing", "file": rel})
    else:
        r = subprocess.run(["node", "--input-type=module", "-e", CSP_JS, after], cwd=cdir, capture_output=True, text=True, timeout=60)
        if r.returncode:
            return {"check": "security:headers", "status": "not run", "why": f"csp_evaluator: {r.stderr[-200:]}"}
        hits += [dict(f, rule="csp-evaluator", file=rel) for f in json.loads(r.stdout or "[]")]
    return {"check": "security:headers", "status": "hits" if hits else "pass", "hits": hits,
            "why": f"{len(hits)} CSP finding(s)" if hits else "CSP unchanged and no high finding"}


def run(repo, base, head, src, base_src, full, workdir, empty_tree, sast_full=False):
    """src/base_src: clean copies (git archive) of head and base; base_src None when the base is the empty tree.
    full: secrets over the whole history (release, deep). sast_full: every static finding, not only new (deep)."""
    workdir = pathlib.Path(workdir)
    for tree in (src, base_src):
        if tree is not None:
            scrub(tree)
    return [secrets(repo, base, head, full, workdir, empty_tree),
            sast(repo, base, src, base_src, sast_full, workdir, empty_tree),
            headers(src, base_src)]


def fingerprint_of_tools():
    """Part of verify's cache key: a result is reused only with the same tools, rules and pack."""
    h = hashlib.sha256(MANIFEST.read_bytes() + (HERE / "pack.txt").read_bytes())
    for f in sorted((HERE / "rules").glob("*.yaml")) + [HERE / "betterleaks.toml"]:
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


# ---------- install ----------

def install():
    m = manifest()
    TOOLS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        bl = m["betterleaks"]
        if tool("betterleaks")[0] is None:
            arc = pathlib.Path(tmp) / "bl.tgz"
            urllib.request.urlretrieve(bl["url"], arc)
            if sha256(arc) != bl["archive_sha256"]:
                sys.exit("betterleaks: archive sha256 does not match the manifest — not installed")
            dest = TOOLS / pathlib.Path(bl["path"]).parent
            dest.mkdir(parents=True, exist_ok=True)
            with tarfile.open(arc) as t:
                t.extractall(dest, filter="data")
        og = m["opengrep"]
        if tool("opengrep")[0] is None:
            dest = TOOLS / og["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(og["url"], pathlib.Path(tmp) / "og")
            if sha256(pathlib.Path(tmp) / "og") != og["sha256"]:
                sys.exit("opengrep: sha256 does not match the manifest — not installed")
            shutil.move(pathlib.Path(tmp) / "og", dest)
            dest.chmod(0o755)
    rules = m["opengrep-rules"]
    rpath = TOOLS / rules["path"]
    if not (rpath / ".git").exists():
        subprocess.run(["git", "clone", "-q", rules["repo"], str(rpath)], check=True)
    subprocess.run(["git", "-C", str(rpath), "fetch", "-q", "origin"], check=False)
    subprocess.run(["git", "-C", str(rpath), "checkout", "-q", "--force", rules["commit"]], check=True)
    cdir = TOOLS / m["csp_evaluator"]["path"]
    if csp_dir()[0] is None:
        cdir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["npm", "install", "--silent", "--ignore-scripts", "--no-audit", "--no-fund", "--prefix", str(cdir),
                        m["csp_evaluator"]["npm"]], check=True)
    for name in ("betterleaks", "opengrep"):
        print(name, "ok" if tool(name)[0] else tool(name)[1])
    print("opengrep-rules", "ok" if rules_dir()[0] else rules_dir()[1])
    print("csp_evaluator", "ok" if csp_dir()[0] else csp_dir()[1])


if __name__ == "__main__":
    if sys.argv[1:] == ["install"]:
        install()
    elif sys.argv[1:] == ["status"]:
        for name in ("betterleaks", "opengrep"):
            print(name, "ok" if tool(name)[0] else tool(name)[1])
        print("opengrep-rules", "ok" if rules_dir()[0] else rules_dir()[1])
        print("csp_evaluator", "ok" if csp_dir()[0] else csp_dir()[1])
    else:
        print(__doc__)
