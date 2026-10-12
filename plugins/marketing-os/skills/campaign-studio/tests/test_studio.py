"""campaign-studio: core.py (checks, pack, patches, plan) and studio.py (the board server, inbox, commands).

Run: python3 plugins/marketing-os/skills/campaign-studio/tests/test_studio.py
Everything happens in temp folders; the server binds 127.0.0.1 on a free port.
"""
import contextlib
import http.client
import http.server
import io
import json
import os
import pathlib
import struct
import sys
import tempfile
import threading
import time
import zlib
from datetime import date

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import core  # noqa: E402
import studio  # noqa: E402

FAILS = []


def case(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


def png(w=4, h=3, color=(10, 120, 90)):
    rows = b"".join(b"\x00" + bytes(color) * w for _ in range(h))
    chunk = lambda k, d: struct.pack(">I", len(d)) + k + d + struct.pack(">I", zlib.crc32(k + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


SID = core.new_studio_id()


def good():
    c = core.empty_campaign("סדנה")
    c["studio_id"] = SID
    c.update({"link": "https://example.com/x", "ai_images": True, "budget_daily_ils": 40})
    c["angles"] = [{"id": "a1", "name": "זמן", "why": "", "images": [{"id": "i1", "file": "images/a1-1.png"}],
                    "headlines": [{"id": "h1", "text": "בוקר אחד"}, {"id": "h2", "text": "רגע לעצמך"}],
                    "texts": [{"id": "t1", "text": "20 דקות בזום."}]}]
    c["picks"] = {"a1|i1|h1|t1": "keep", "a1|i1|h2|t1": "drop"}
    return c


def root_with(campaign=None, venture="מיזם", slug="נובמבר"):
    root = pathlib.Path(tempfile.mkdtemp(prefix="studio-test-"))
    folder = root / venture / "שיווק" / "קמפיינים" / slug
    (folder / "images").mkdir(parents=True)
    (folder / "images" / "a1-1.png").write_bytes(png())
    core.save(folder, campaign or good())
    return root, folder


def packed(c, folder):
    c["packed_at"] = "2026-10-12T10:00:00+00:00"
    c["packed_hash"] = core.content_hash(c, folder)
    return c


# ---------- check ----------
root, d = root_with()
r = core.check(core.load(d), d)
case("clean campaign has no errors", r["errors"] == [] and r["kept"] == 1 and r["total"] == 2)

def errs(mut, today=None):
    c = good()
    mut(c)
    return core.check(c, d, today=today)

case("AI disclosure must be decided", any("AI" in e for e in errs(lambda c: c.update(ai_images=None))["errors"]))
case("link must be https", any("https" in e for e in errs(lambda c: c.update(link="http://x.com"))["errors"]))
case("no kept ad is an error", any("👍" in e for e in errs(lambda c: c.update(picks={}))["errors"]))
case("age under 18 is an error", any("18" in e for e in errs(lambda c: c["audience"].update(age_min=16))["errors"]))
case("boolean budget is rejected", any("תקציב" in e for e in errs(lambda c: c.update(budget_daily_ils=True))["errors"]))
case("NaN budget is rejected", any("תקציב" in e for e in errs(lambda c: c.update(budget_daily_ils=float("nan")))["errors"]))
r = errs(lambda c: c.update(budget_daily_ils=150))
case("budget over 100 warns, does not block", r["errors"] == [] and any("150" in w for w in r["warnings"]))
case("end today or earlier is an error",
     any("הסיום" in e for e in errs(lambda c: c.update(end="2026-10-01"), today=date(2026, 10, 12))["errors"]))
case("end before start is an error",
     any("הסיום" in e for e in errs(lambda c: c.update(start="2026-11-10", end="2026-11-01"), today=date(2026, 10, 1))["errors"]))
r = errs(lambda c: c.update(start="2026-01-01"), today=date(2026, 10, 1))
case("past start is only a warning", r["errors"] == [] and any("עבר" in w for w in r["warnings"]))
r = errs(lambda c: c["angles"][0]["headlines"][0].update(text="x" * 41))
case("long headline warns, does not block", r["errors"] == [] and len(r["warnings"]) == 1)
case("image outside the folder is an error",
     any("לא תקינה" in e for e in errs(lambda c: c["angles"][0]["images"][0].update(file="../../etc/passwd"))["errors"]))
case("non-image file is an error",
     any("לא תקינה" in e for e in errs(lambda c: c["angles"][0]["images"][0].update(file="campaign.json"))["errors"]))
r = errs(lambda c: c["angles"][0]["headlines"].append({"id": "h1", "text": "כפול"}))
case("duplicate ids are an error and do not crash", any("מזהה" in e for e in r["errors"]) and r["total"] == 0)
case("bad id characters are an error",
     any("מזהה" in e for e in errs(lambda c: c["angles"][0].update(id='x"><img>'))["errors"]))
case("a pick value other than keep/drop is an error",
     any("בחירה" in e for e in errs(lambda c: c["picks"].update({"a1|i1|h2|t1": "<script>"}))["errors"]))
(d / "campaign (1).json").write_text("{}")
case("a Drive conflict copy warns", any("עותק כפול" in w for w in core.check(good(), d)["warnings"]))
(d / "campaign (1).json").unlink()

# ---------- pack ----------
c = packed(good(), d)
case("packed after packing", core.is_packed(c, d))
c2 = json.loads(json.dumps(c)); c2["review_notes"] = "הערה"; c2["meta"]["campaign_id"] = "123"
case("review notes and Meta ids do not unpack", core.is_packed(c2, d))
c3 = json.loads(json.dumps(c)); c3["angles"][0]["headlines"][0]["text"] = "שונה"
case("a text edit unpacks", not core.is_packed(c3, d))
(d / "images" / "a1-1.png").write_bytes(png(color=(200, 10, 10)))
case("an image replaced under the same name unpacks", not core.is_packed(c, d))
(d / "images" / "a1-1.png").write_bytes(png())
case("the original bytes pack again", core.is_packed(c, d))
case("image_size reads a PNG", core.image_size(d / "images" / "a1-1.png") == (4, 3))

# ---------- plan ----------
p, e = core.plan(good(), d)
case("plan refuses an unpacked campaign", p is None and e)
p, e = core.plan(packed(good(), d), d)
case("plan of a packed campaign", e == [] and len(p["ads"]) == 1 and p["campaign"]["campaign_daily_budget"] == 4000
     and p["campaign"]["objective"] == "OUTCOME_TRAFFIC" and p["creative"]["self_ai_disclosure"] == "OPT_IN"
     and p["ads"][0]["headline"] == "בוקר אחד" and tuple(p["ads"][0]["image_size"]) == (4, 3))
sid = good()["studio_id"]
case("plan: stable tags from studio_id in every name", p["tag"] == f"[studio:{sid}]" and p["campaign"]["name"].endswith(p["tag"])
     and p["ad_set"]["name"].endswith(p["tag"]) and p["ads"][0]["name"].endswith(f"[studio:{sid}|a1|i1|h1|t1]")
     and p["packed_hash"] == packed(good(), d)["packed_hash"])
case("plan: traffic has a website destination", p["ad_set"]["destination_type"] == "WEBSITE")
c = good(); c["goal"] = "awareness"
p, _ = core.plan(packed(c, d), d)
case("plan: awareness has no destination key", "destination_type" not in p["ad_set"] and p["ad_set"]["optimization_goal"] == "REACH")
c = good(); c["audience"].update(gender="female", strict_age=True)
t = core.plan(packed(c, d), d)[0]["ad_set"]["targeting"]
case("plan: female and strict age", t["genders"] == [2] and t["targeting_automation"] == {"advantage_audience": 0})
case("plan: an inbox count from a staged copy blocks", core.plan(packed(good(), d), d, inbox_content_ops=2)[0] is None)
(d / "inbox").mkdir()
(d / "inbox" / "1.json").write_text(json.dumps({"format": 1, "ops": [{"op": "set", "path": ["meta", "campaign_id"], "value": "9"}]}))
case("plan: a meta-only inbox patch does not block", core.plan(packed(good(), d), d)[1] == [])
(d / "inbox" / "2.json").write_text(json.dumps({"format": 1, "ops": [{"op": "set", "path": ["name"], "value": "x"}]}))
case("plan: a content patch waiting in the inbox blocks", core.plan(packed(good(), d), d)[0] is None)
for f in (d / "inbox").iterdir():
    f.unlink()

# ---------- patch engine ----------
c = good()
new, touched = core.apply_ops(c, [{"op": "set", "path": ["angles", {"id": "a1"}, "headlines", {"id": "h2"}, "text"], "value": "חדש"},
                                  {"op": "add", "path": ["angles", {"id": "a1"}, "texts"], "value": {"id": "t2", "text": "עוד"}},
                                  {"op": "remove", "path": ["angles", {"id": "a1"}, "headlines", {"id": "h1"}]}], "claude")
case("set, add, remove by id", new["angles"][0]["headlines"] == [{"id": "h2", "text": "חדש"}]
     and new["angles"][0]["texts"][-1]["id"] == "t2" and c["angles"][0]["headlines"][0]["id"] == "h1" and len(touched) == 3)


def rejects(ops, source="claude"):
    try:
        core.apply_ops(good(), ops, source)
        return False
    except core.PatchError:
        return True


case("Claude cannot touch picks", rejects([{"op": "set", "path": ["picks"], "value": {}}]))
case("Claude cannot declare AI", rejects([{"op": "set", "path": ["ai_images"], "value": False}]))
case("Claude cannot pack", rejects([{"op": "set", "path": ["packed_hash"], "value": "x"}]))
case("the board cannot write Meta ids or review notes", rejects([{"op": "set", "path": ["meta", "campaign_id"], "value": "1"}], "board")
     and rejects([{"op": "set", "path": ["review_notes"], "value": "x"}], "board"))
case("a missing id rejects the whole patch", rejects([{"op": "set", "path": ["name"], "value": "x"},
                                                      {"op": "set", "path": ["angles", {"id": "zz"}, "name"], "value": "y"}]))
case("replacing a whole id list is refused", rejects([{"op": "set", "path": ["angles"], "value": []}]))
case("a duplicate id on add is refused", rejects([{"op": "add", "path": ["angles", {"id": "a1"}, "texts"], "value": {"id": "t1"}}]))
case("nobody changes studio_id", rejects([{"op": "set", "path": ["studio_id"], "value": "x"}])
     and rejects([{"op": "set", "path": ["studio_id"], "value": "x"}], "board"))
case("wrong types are refused", rejects([{"op": "set", "path": ["audience"], "value": "IL"}])
     and rejects([{"op": "set", "path": ["angles", {"id": "a1"}, "images", {"id": "i1"}, "file"], "value": 5}])
     and rejects([{"op": "set", "path": ["budget_daily_ils"], "value": "40"}])
     and rejects([{"op": "set", "path": ["audience", "countries"], "value": ["Israel"]}])
     and rejects([{"op": "add", "path": ["angles", {"id": "a1"}, "images"], "value": {"id": "i9", "file": 5}}])
     and rejects([{"op": "set", "path": ["angles", {"id": "a1"}, "headlines", {"id": "h1"}, "text"], "value": None}]))
case("unknown fields are refused", rejects([{"op": "set", "path": ["status"], "value": "ACTIVE"}]))
case("Claude can set review notes and Meta ids",
     not rejects([{"op": "set", "path": ["review_notes"], "value": "x"}, {"op": "set", "path": ["meta", "campaign_id"], "value": "1"}]))

# ---------- server ----------
root, d = root_with()
st = studio.Studio(root)
server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), studio.Handler)
port = server.server_address[1]
studio.Handler.studio, studio.Handler.port = st, port
threading.Thread(target=server.serve_forever, daemon=True).start()
KEY = "מיזם/נובמבר"
Q = "c=" + __import__("urllib.parse").parse.quote(KEY)


def req(method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Host": f"127.0.0.1:{port}"}
    if method == "POST":
        h["X-Studio"] = "1"
    h.update(headers or {})
    data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
    conn.request(method, path, body=data, headers=h)
    r = conn.getresponse()
    raw = r.read()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


s, b = req("GET", "/api/whoami")
case("whoami", s == 200 and b["app"] == "campaign-studio")
s, b = req("GET", "/api/campaigns")
case("campaign list", s == 200 and b["campaigns"][0]["key"] == KEY and b["campaigns"][0]["kept"] == 1)
s, b = req("GET", "/api/campaign?" + Q)
case("GET campaign", s == 200 and b["campaign"]["name"] == "סדנה" and b["check"]["errors"] == [])
rev0 = b["rev"]
s, _ = req("GET", "/api/campaign?" + Q, headers={"Host": "evil.example:80"})
case("foreign Host is refused", s == 403)
s, _ = req("POST", "/api/ops?" + Q, {"ops": []}, {"X-Studio": "", "Content-Type": "application/json"})
case("POST without X-Studio is refused", s == 403)
s, _ = req("POST", "/api/ops?" + Q, {"ops": []}, {"Origin": "https://evil.example"})
case("POST from another site is refused", s == 403)
s, _ = req("GET", "/files/" + __import__("urllib.parse").parse.quote("מיזם/נובמבר/../נובמבר/campaign.json"))
case("files: only images inside the folder", s == 404)
s, b = req("GET", "/files/" + __import__("urllib.parse").parse.quote("מיזם/נובמבר/images/a1-1.png"))
case("files: serves an image", s == 200 and b[:4] == b"\x89PNG")

s, b = req("POST", "/api/ops?" + Q, {"ops": [{"op": "set", "path": ["name"], "value": "סדנה 2"}], "base_rev": rev0})
case("board op applied", s == 200 and b["campaign"]["name"] == "סדנה 2" and b["rev"] > rev0)
s, b = req("POST", "/api/ops?" + Q, {"ops": [{"op": "set", "path": ["meta", "campaign_id"], "value": "1"}], "base_rev": b["rev"]})
case("board op on Meta ids is refused with a reason", s == 200 and b["failures"] and "הלוח" in b["failures"][0])
s, b = req("POST", "/api/ops?" + Q, {"ops": [{"op": "set", "path": ["budget_daily_ils"], "value": 33},
                                              {"op": "remove", "path": ["angles", {"id": "a1"}, "texts", {"id": "gone"}]}],
                                      "base_rev": b["rev"]})
case("one stale op does not cost the rest of the batch", s == 200 and b["campaign"]["budget_daily_ils"] == 33 and len(b["failures"]) == 1)

# Claude edits a headline through the inbox; the board, still on the old rev, edits the same headline
base = req("GET", "/api/campaign?" + Q)[1]["rev"]
(d / "inbox").mkdir(exist_ok=True)
H1 = ["angles", {"id": "a1"}, "headlines", {"id": "h1"}, "text"]
(d / "inbox" / "a.json").write_text(json.dumps({"format": 1, "ops": [{"op": "set", "path": H1, "value": "של Claude"}]}), encoding="utf-8")
st.apply_inboxes()
case("Claude's patch applied and removed", not (d / "inbox" / "a.json").exists()
     and core.load(d)["angles"][0]["headlines"][0]["text"] == "של Claude")
s, b = req("POST", "/api/ops?" + Q, {"ops": [{"op": "set", "path": H1, "value": "שלי"}], "base_rev": base})
case("same field: last write wins and the board is told", s == 200 and b["campaign"]["angles"][0]["headlines"][0]["text"] == "שלי"
     and b["conflicts"] and b["conflicts"][0]["claude_value"] == "של Claude")
s, b = req("POST", "/api/ops?" + Q, {"ops": [{"op": "set", "path": ["business_name"], "value": "x"}], "base_rev": base})
case("a different field is not a conflict", s == 200 and b["conflicts"] == [])

(d / "inbox" / "b.json").write_text(json.dumps({"format": 1, "ops": [{"op": "set", "path": ["picks"], "value": {}}]}), encoding="utf-8")
st.apply_inboxes()
s, b = req("GET", "/api/campaign?" + Q)
case("a founder-only patch is rejected with a reason", (d / "inbox" / "rejected" / "b.json").exists()
     and b["rejected"] and "המייסד" in b["rejected"][0]["reason"])
s, b = req("POST", "/api/dismiss?" + Q + "&name=b.json")
case("the founder dismisses it", s == 200 and b["rejected"] == [])

(d / "inbox" / "c.json").write_text('{"format": 1, "ops": [', encoding="utf-8")
st.apply_inboxes()
case("a half-written file waits", (d / "inbox" / "c.json").exists())
(d / "inbox" / "c.json").write_text(json.dumps({"format": 1, "ops": [{"op": "set", "path": ["name"], "value": "סדנה 3"}]}), encoding="utf-8")
st.apply_inboxes()
case("then applies once complete", not (d / "inbox" / "c.json").exists() and core.load(d)["name"] == "סדנה 3")
(d / "inbox" / "e.json").write_text("not json", encoding="utf-8")
st.first_seen[str(d / "inbox" / "e.json")] = time.monotonic() - 60
st.apply_inboxes()
case("unreadable for too long is rejected", (d / "inbox" / "rejected" / "e.json").exists())
(d / "inbox" / "f (1).json").write_text(json.dumps({"format": 1, "ops": [{"op": "set", "path": ["name"], "value": "כפול"}]}), encoding="utf-8")
st.apply_inboxes()
case("a sync conflict copy in the inbox is ignored", (d / "inbox" / "f (1).json").exists() and core.load(d)["name"] == "סדנה 3")
(d / "inbox" / "f (1).json").unlink()
(d / "inbox" / "g.json").write_text(json.dumps({"format": 2, "ops": []}), encoding="utf-8")
st.apply_inboxes()
case("an unknown format is rejected", (d / "inbox" / "rejected" / "g.json").exists())

base_dir = d.parent
(base_dir / "inbox").mkdir()
(base_dir / "inbox" / "new.json").write_text(json.dumps({"format": 1, "create": "דצמבר", "campaign": {
    "name": "דצמבר", "picks": {"x": "keep"}, "ai_images": True, "angles": []}}), encoding="utf-8")
st.apply_inboxes()
nc = core.load(base_dir / "דצמבר")
case("Cowork-style create makes the folder, ignoring founder-only fields", nc["name"] == "דצמבר" and nc["picks"] == {}
     and nc["ai_images"] is None and (base_dir / "דצמבר" / "images").is_dir())
(base_dir / "inbox" / "again.json").write_text(json.dumps({"format": 1, "create": "דצמבר", "campaign": {}}), encoding="utf-8")
st.apply_inboxes()
case("a create for an existing slug is rejected", (base_dir / "inbox" / "rejected" / "again.json").exists())

# pack
for f in (d / "inbox" / "rejected").glob("*"):
    f.unlink()
s, b = req("GET", "/api/campaign?" + Q)
s, b2 = req("POST", "/api/pack?" + Q, {"base_rev": b["rev"] - 1})
case("pack on a stale view is refused", s == 409 and "השתנה" in b2["error"])
s, b2 = req("POST", "/api/pack?" + Q, {"base_rev": b["rev"]})
case("pack", s == 200 and b2["check"]["packed"])
s, b3 = req("POST", "/api/ops?" + Q, {"ops": [{"op": "set", "path": ["budget_daily_ils"], "value": 50}], "base_rev": b2["rev"]})
case("an edit after packing unpacks", not b3["check"]["packed"])
s, b4 = req("POST", "/api/ops?" + Q, {"ops": [{"op": "set", "path": ["ai_images"], "value": None}], "base_rev": b3["rev"]})
s, b5 = req("POST", "/api/pack?" + Q, {"base_rev": b4["rev"]})
case("pack refuses with errors", s == 409 and not b5["check"]["packed"])

# a broken campaign does not take the list down
broken = d.parent / "שבור"
broken.mkdir()
(broken / "campaign.json").write_text('{"angles": "x", "audience": "IL"}', encoding="utf-8")
s, b = req("GET", "/api/campaigns")
case("a broken campaign shows as broken, the rest still list", s == 200 and any(x.get("broken") for x in b["campaigns"])
     and any(x["key"] == KEY and not x.get("broken") for x in b["campaigns"]))
s, b = req("GET", "/api/campaign?" + __import__("urllib.parse").parse.quote("c=מיזם/שבור", safe="="))
case("opening it says the file is broken", s == 500 and "פגום" in b["error"])
import shutil as _sh
_sh.rmtree(broken)
(base_dir / "inbox" / "rejected").mkdir(exist_ok=True)
s, b = req("GET", "/api/campaigns")
case("refused creates are listed for the home page", any(x["name"] == "again.json" for x in b["rejected"]))
s, b = req("POST", "/api/dismiss-new?v=" + __import__("urllib.parse").parse.quote("מיזם") + "&name=again.json")
case("and can be dismissed", s == 200 and not any(x["name"] == "again.json" for x in b["rejected"]))

# external change bumps rev
before = req("GET", "/api/campaign?" + Q)[1]["rev"]
c = core.load(d); c["business_name"] = "שונה בחוץ"; core.save(d, c)
case("a change made outside the server is noticed", req("GET", "/api/campaign?" + Q)[1]["rev"] > before)

# upload
s, b = req("POST", "/api/upload?" + Q, png(), {"X-Filename": "../../new%20pic.png"})
case("upload lands in images/ with a safe name", s == 200 and b["file"] == "images/new-pic.png" and (d / "images" / "new-pic.png").is_file())
s, b = req("POST", "/api/upload?" + Q, png(), {"X-Filename": "new%20pic.png"})
case("upload never overwrites", s == 200 and b["file"] == "images/new-pic-2.png")
s, b = req("POST", "/api/upload?" + Q, b"x", {"X-Filename": "a.svg"})
case("upload refuses other types", s == 400)
server.shutdown()

# ---------- commands ----------
root, d = root_with()
ops = pathlib.Path(tempfile.mkdtemp()) / "ops.json"
ops.write_text(json.dumps({"ops": [{"op": "set", "path": ["name"], "value": "מהשורה"}]}), encoding="utf-8")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = studio.cmd_patch(d, str(ops), port=1)
case("patch with no server applies it directly", rc == 0 and core.load(d)["name"] == "מהשורה" and "applied" in buf.getvalue())
ops.write_text(json.dumps({"ops": [{"op": "set", "path": ["picks"], "value": {}}]}), encoding="utf-8")
with contextlib.redirect_stderr(io.StringIO()):
    rc = studio.cmd_patch(d, str(ops), port=1)
case("patch refuses founder-only fields up front", rc == 1 and not list((d / "inbox").glob("*.json")))
cwd = os.getcwd()
os.chdir(d.parent)
ops.write_text(json.dumps({"ops": [{"op": "set", "path": ["name"], "value": "יחסי"}]}), encoding="utf-8")
with contextlib.redirect_stdout(io.StringIO()):
    rc = studio.cmd_patch(pathlib.Path(d.name), str(ops), port=1)
os.chdir(cwd)
case("patch with a relative folder really applies", rc == 0 and core.load(d)["name"] == "יחסי")
newdir = d.parent / "חדש"
with contextlib.redirect_stdout(io.StringIO()):
    rc = studio.cmd_new(newdir, None, "קמפיין חדש")
case("new creates a folder with a studio_id", rc == 0 and core.load(newdir)["name"] == "קמפיין חדש"
     and core.ID_RE.match(core.load(newdir)["studio_id"]))

print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
