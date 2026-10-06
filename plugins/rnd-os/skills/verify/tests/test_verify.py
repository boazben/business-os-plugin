"""plugins/rnd-os/skills/verify/scripts/verify.py: change map, tripwire, cache, and the checks run.

Run: python3 plugins/rnd-os/skills/verify/tests/test_verify.py
Each case builds a small git repo in a temp folder; nothing outside it is touched.
"""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[5]
spec = importlib.util.spec_from_file_location("verify", ROOT / "plugins/rnd-os/skills/verify/scripts/verify.py")
assert spec and spec.loader
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)
failures = []


def check(name, cond):
    if not cond:
        failures.append(name)
    print(("ok   " if cond else "FAIL ") + name)


CONFIG = {
    "paths": [
        {"match": "tests/**", "domains": ["tests"]},
        {"match": "netlify/lib/content/gen/**", "domains": ["code", "customer_text"]},
        {"match": ["netlify/**", "_headers", "netlify.toml"], "domains": ["code", "security"]},
        {"match": "public/*.html", "domains": ["code", "customer_text"]},
        {"match": "public/assets/img/**", "domains": ["design"]},
        {"match": "*.css", "domains": ["design"]},
        {"match": "rnd/**", "domains": ["docs"]},
    ],
    "tripwire_paths": ["netlify/functions/**", "netlify.toml", "_headers"],
    "known_hosts": ["shahut.co.il"],
}


def sh(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def commit(repo, files, msg):
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    sh(repo, "add", "-A")
    sh(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", msg)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def quiet(fn, *a):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*a)


with tempfile.TemporaryDirectory() as d:
    repo = pathlib.Path(d) / "site"
    repo.mkdir()
    sh(repo, "init", "-q", "-b", "main")
    base = commit(repo, {
        "rnd/domains.json": json.dumps(CONFIG),
        "public/index.html": "<p>שלום</p>",
        "netlify/lib/content/gen/templates.mjs": "export const t = 1;\n",
        "netlify/functions/notify.mjs": "export default () => 1;\n",
        "tests/a.test.mjs": "\n",
        "package.json": json.dumps({"scripts": {"test": "node -e \"process.exit(0)\""}}),
    }, "base")

    # the favicon change of BOS-81: four pages, the generator, a new icon, a test
    fav = commit(repo, {
        "public/index.html": "<link rel=icon href=/assets/img/icon.svg><p>שלום</p>",
        "netlify/lib/content/gen/templates.mjs": "export const t = 2; // icon\n",
        "public/assets/img/icon.svg": "<svg/>",
        "tests/a.test.mjs": "// icon\n",
    }, "favicon")
    cfg = v.load_config(repo, fav)
    m = v.change_map(repo, base, fav, cfg)
    check("favicon: no security domain", "security" not in m["domains"])
    check("favicon: customer text (pages changed) and design (icon)", {"customer_text", "design"} <= set(m["domains"]))
    check("favicon: nothing unmapped", m["unmapped"] == [])
    check("favicon: tripwire stays quiet", v.tripwire(repo, base, fav, cfg) == [])

    # the P8 spike of BOS-81: reads an account token from env and calls the Netlify API
    p8 = commit(repo, {"spike/p8.mjs": "const t = process.env.NETLIFY_AUTH_TOKEN;\nfetch('https://api.netlify.com/api/v1/sites', {headers: {Authorization: `Bearer ${t}`}});\n"}, "p8")
    hits = v.tripwire(repo, fav, p8, cfg)
    whys = " ".join(h["why"] for h in hits)
    check("P8: env read lights the tripwire", "env_read" in whys)
    check("P8: a secret name lights it", "secret_name" in whys)
    check("P8: a new host lights it", "new_host api.netlify.com" in whys)
    m = v.change_map(repo, fav, p8, cfg)
    check("an unmapped file counts as every domain", m["unmapped"] == ["spike/p8.mjs"] and "security" in m["domains"])
    check("every domain suggests security-lead", "rnd-os:security-lead" in m["reviewers"])

    # a server path lights the tripwire even with no suspicious line
    srv = commit(repo, {"netlify/functions/notify.mjs": "export default () => 2;\n"}, "server")
    check("a tripwire path lights it", any(h["why"] == "path" for h in v.tripwire(repo, p8, srv, cfg)))
    moved_host = commit(repo, {"scripts/a.mjs": "fetch('https://x.host/v1');\n"}, "host in")
    moved_host2 = commit(repo, {"scripts/a.mjs": "// moved\n", "scripts/b.mjs": "fetch('https://x.host/v2');\n"}, "host moved")
    check("a host removed in one line and added in another is not new",
          not any("new_host" in h["why"] for h in v.tripwire(repo, moved_host, moved_host2, cfg)))
    check("…while a host that only appears is new", any("new_host x.host" in h["why"] for h in v.tripwire(repo, srv, moved_host, cfg)))

    # a spike moved into netlify/functions becomes a live endpoint: a pure rename must still light it
    sh(repo, "mv", "spike/p8.mjs", "netlify/functions/p8.mjs")
    sh(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "move p8")
    mv = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    whys = " ".join(h["why"] for h in v.tripwire(repo, moved_host2, mv, cfg))
    check("a renamed file with a secret read lights the tripwire", "env_read" in whys and "path" in whys)
    heb = commit(repo, {"public/שלום.mjs": "const k = process.env.SECRET_TOKEN;\n"}, "hebrew path")
    check("a Hebrew file name is read, not skipped as a quoted path", any(h["why"] == "env_read" for h in v.tripwire(repo, mv, heb, cfg)))
    nenv = commit(repo, {"netlify/lib/x.mjs": "const v = n.env.get(name);\n"}, "netlify env")
    check("an env read through Netlify.env.get lights it", any(h["why"] == "env_read" for h in v.tripwire(repo, heb, nenv, cfg)))

    # a link in page text is not a host the code sends to
    lnk = commit(repo, {"public/index.html": "<a href='https://support.apple.com/x'>עזרה</a>"}, "link")
    check("a link in a page doesn't light the tripwire", not any("new_host" in h["why"] for h in v.tripwire(repo, nenv, lnk, cfg)))

    # a README publish instruction
    rd = commit(repo, {"README.md": "Run: npx netlify-cli deploy --prod\n"}, "readme")
    check("a publish instruction lights it", any(h["why"] == "publish_instruction" for h in v.tripwire(repo, lnk, rd, cfg)))
    check("no change between base and head → exit 2, not an empty map", quiet(v.main, [str(repo), "--commit", rd, "--base", rd, "--map-only"]) == 2)

    # first matching rule wins: the generator is customer text, not the netlify/** server rule
    check("first rule wins", v.domains_of("netlify/lib/content/gen/x.mjs", cfg) == {"code", "customer_text"})
    check("no config → every domain", v.domains_of("anything", None) == {v.ALL})
    check("** spans directories", v.glob_match("public/assets/img/a/b.svg", "public/assets/img/**"))
    check("bare name matches at any depth", v.glob_match("a/b/site.css", "*.css"))

    # live commit from Netlify's site JSON
    check("published sha read from the deploy title",
          v.parse_published_sha(json.dumps({"published_deploy": {"title": "deploy " + "a" * 40}})) == "a" * 40)
    check("no title → unknown", v.parse_published_sha(json.dumps({"published_deploy": {}})) is None)
    check("bad json → unknown", v.parse_published_sha("not json") is None)

    # a full run: checks pass, summary written, then reused; the key changes with an external input
    out = pathlib.Path(d) / "cache"
    os.environ.pop("PLAYWRIGHT_CORE", None)
    code = quiet(v.main, [str(repo), "--commit", fav, "--base", base, "--out", str(out), "--timeout", "60"])
    runs = list(out.glob("site/*/summary.json"))
    check("a run with passing checks exits 0", code == 0)
    s = json.loads(runs[0].read_text()) if runs else {}
    check("test ran and passed", any(r["check"] == "test" and r["status"] == "pass" for r in s.get("checks", [])))
    check("a missing script is 'not run', not pass", any(r["check"] == "test:browser" and r["status"] == "not run" for r in s.get("checks", [])))
    check("summary.md written", runs and (runs[0].parent / "summary.md").exists())
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        v.main([str(repo), "--commit", fav, "--base", base, "--out", str(out)])
    check("same inputs → reused", "reused" in buf.getvalue())
    ext = pathlib.Path(d) / "ruling.md"
    ext.write_text("v1")
    k1 = v.cache_key(fav, base, "", [str(ext)])
    ext.write_text("v2")
    check("an external input changes the key", v.cache_key(fav, base, "", [str(ext)]) != k1)

    # a failing check exits 1 and is never reused
    bad = commit(repo, {"package.json": json.dumps({"scripts": {"test": "node -e \"process.exit(1)\""}})}, "bad")
    check("a failing check exits 1", quiet(v.main, [str(repo), "--commit", bad, "--base", fav, "--out", str(out), "--timeout", "60"]) == 1)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        v.main([str(repo), "--commit", bad, "--base", fav, "--out", str(out), "--timeout", "60"])
    check("a failed result is not reused", "reused" not in buf.getvalue())

    # the checks can't read the real HOME
    leak = commit(repo, {"package.json": json.dumps({"scripts": {"test": "node -e \"process.exit(process.env.HOME===require('os').homedir() && !process.env.HOME.includes('verify-') ? 1 : 0)\""}})}, "home")
        # exit 3, not 0: the test line reads process.env, so the tripwire lights — and the check itself passed
    check("checks run with a throwaway HOME (check passes; env read lights the tripwire)",
          quiet(v.main, [str(repo), "--commit", leak, "--base", bad, "--out", str(out), "--timeout", "60"]) == 3)

    # approved wording: a block in the drafts must appear word for word in a page, belong to a ruling in force,
    # and cover every new text segment
    drafts = pathlib.Path(d) / "50א - נוסחים.md"
    drafts.write_text("נוסח:\n```approved id=50א-4.1\nאיסוף עצמי **בתיאום** מראש, בלי דמי משלוח.\n```\n", encoding="utf-8")
    legal = pathlib.Path(d) / "legal"
    legal.mkdir()
    (legal / "00 - יומן פסיקות.md").write_text("| # | נושא | קובץ | בתוקף |\n|---|---|---|---|\n| 50 | מחיר | `50.md` | כן |\n| 46 | ישן | `46.md` | לא — הוחלף |\n", encoding="utf-8")
    spec2 = importlib.util.spec_from_file_location("approved_text", ROOT / "plugins/rnd-os/skills/verify/scripts/approved_text.py")
    assert spec2 and spec2.loader
    at = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(at)
    pages_old, pages_new = pathlib.Path(d) / "old", pathlib.Path(d) / "new"
    pages_old.mkdir(); pages_new.mkdir()
    (pages_old / "terms.html").write_text("<p>תנאים</p>", encoding="utf-8")
    (pages_new / "terms.html").write_text("<p>תנאים</p><p>איסוף עצמי <b>בתיאום</b>&nbsp;מראש,\n בלי דמי משלוח.</p><script>x</script>", encoding="utf-8")
    st, why = at.verdict(at.check(pages_new, [str(drafts)], legal, pages_old))
    check("approved text found despite markup, &nbsp; and line breaks; ruling in force; new text covered", st == "pass")
    st, why = at.verdict(at.check(pages_new, [str(drafts)], None, pages_old))
    check("no register → 'not run', never pass", st == "not run")
    (pages_new / "terms.html").write_text("<p>תנאים</p><p>איסוף עצמי בתיאום מראש, בלי דמי משלוח.</p><p>משלוח חינם לכל הארץ!</p>", encoding="utf-8")
    st, why = at.verdict(at.check(pages_new, [str(drafts)], legal, pages_old))
    check("new text next to the approved block, not in any block → fail", st == "fail" and "משלוח חינם" in why)
    (pages_new / "terms.html").write_text("<p>איסוף עצמי בתיאום מראש, בתוספת דמי משלוח.</p>", encoding="utf-8")
    st, why = at.verdict(at.check(pages_new, [str(drafts)], legal, None))
    check("a changed word → the block is missing → fail", st == "fail" and "missing 50א-4.1" in why)
    old_drafts = pathlib.Path(d) / "46א.md"
    old_drafts.write_text("```approved id=46א-1\nתנאים\n```\n```approved id=46א-2\n```\n", encoding="utf-8")
    st, why = at.verdict(at.check(pages_old, [str(old_drafts)], legal, None))
    check("a block of a ruling not in force fails, and an empty block fails", st == "fail" and "not in force: 46א-1" in why and "empty 46א-2" in why)

    ok_page = commit(repo, {"package.json": json.dumps({"scripts": {"test": "node -e \"process.exit(0)\""}}),
                            "public/terms.html": "<p>איסוף עצמי <b>בתיאום</b>&nbsp;מראש,\n בלי דמי משלוח.</p>"}, "terms ok")
    quiet(v.main, [str(repo), "--commit", ok_page, "--base", rd, "--input", str(drafts), "--out", str(out)])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = v.main([str(repo), "--commit", ok_page, "--base", rd, "--approved", str(drafts), "--legal-dir", str(legal), "--out", str(out)])
    check("--approved after --input with the same file is not a cache hit", "reused" not in buf.getvalue())
    mine = [json.loads(p.read_text()) for p in out.glob("site/*/summary.json")]
    mine = [r for r in mine if r["head"] == ok_page and any(c["check"] == "approved-text" for c in r["checks"])]
    check("verify --approved --legal-dir records approved-text as pass", code in (0, 3) and mine and
          any(c["check"] == "approved-text" and c["status"] == "pass" for c in mine[0]["checks"]))
    empty = commit(repo, {"package.json": json.dumps({"scripts": {}})}, "no scripts")
    check("no check ran → exit 2, not 0", quiet(v.main, [str(repo), "--commit", empty, "--base", ok_page, "--out", str(out)]) == 2)

    # release with an unknown live commit compares with the empty tree
    code = quiet(v.main, [str(repo), "--commit", fav, "--release", "--map-only", "--out", str(out)])
    rel = [json.loads(p.read_text()) for p in out.glob("site/*/summary.json")]
    check("release, live unknown → whole tree", any(r["release"] and r["base"] == v.EMPTY_TREE for r in rel))
    check("bad commit → exit 2", quiet(v.main, [str(repo), "--commit", "nope", "--out", str(out)]) == 2)

print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
