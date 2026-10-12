#!/usr/bin/env python3
"""Campaign studio: the board server (the only writer of campaign.json) and the commands Claude uses.

  studio.py serve  [--root R] [--port 8765] [--open]   the board for every venture under R, at http://localhost:8765/
  studio.py new    <campaign-dir> [--from F] [--name N] a new campaign folder (F: {"campaign": {...}} or a bare campaign)
  studio.py patch  <campaign-dir> <ops.json | ->        a change request; the server applies it, or this command does
                                                        when no server is running
  studio.py check  <campaign-dir>                       errors and warnings (exit 1 on errors)
  studio.py plan   <campaign-dir>                       what to create in Meta; refuses unless packed, unchanged
                                                        and nothing waits in the inbox
  studio.py status [--port 8765]                        is the board server running, and on which folder
  studio.py install-autostart --root R                  start the board at Windows logon, hidden (Startup folder)
  studio.py uninstall-autostart

Root default: $CAMPAIGN_STUDIO_ROOT, else /mnt/c/Users/<user>/Documents/יזמות. See core.py for the folder layout.
"""
import argparse
import contextlib
import fcntl
import hashlib
import http.server
import json
import os
import pathlib
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import core  # noqa: E402

BOARD = HERE / "board.html"
DEFAULT_PORT = 8765
MAX_UPLOAD = 15 * 1024 * 1024
UNREADABLE_GRACE = 10.0
POLL = 1.5
LINK_NAME = "BusinessOS Campaign Studio.lnk"
LAUNCHER = pathlib.Path.home() / ".business-os" / "bin" / "campaign-studio"


def default_root():
    env = os.environ.get("CAMPAIGN_STUDIO_ROOT")
    if env:
        return env
    user = os.environ.get("WIN_USER") or os.environ.get("USER") or ""
    return f"/mnt/c/Users/{user}/Documents/יזמות"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- locks across processes ----------

LOCK_DIR = pathlib.Path.home() / ".cache" / "business-os" / "campaign-studio"


class RootLock:
    """A lock shared by every process working on one root: the board server and `studio.py patch`. It lives on the
    WSL file system, because file locks on /mnt/c are not reliable. Kinds: "write" around every write to a
    campaign.json, "server" held for the whole life of a board server."""

    def __init__(self, root, kind):
        rid = hashlib.sha1(str(pathlib.Path(root).resolve()).encode()).hexdigest()[:16]
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        self.path = LOCK_DIR / f"{rid}.{kind}.lock"
        self.fd = None

    def acquire(self, blocking=True):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            return True
        except BlockingIOError:
            os.close(self.fd)
            self.fd = None
            return False

    def release(self):
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None


# ---------- the studio: state shared by the server threads ----------

class Studio:
    """Every write happens under self.locked(): a thread lock plus the root's write lock. rev is "<boot>-<n>": n
    counts writes per campaign, boot changes when the server restarts, so a board tab never mistakes an old rev for
    the current one."""

    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.lock = threading.RLock()
        self.write_lock = RootLock(root, "write")
        self.boot = secrets.token_hex(3)
        self.revs = {}             # key -> int
        self.digests = {}          # key -> sha256 of campaign.json as last seen
        self.claude = {}           # key -> [(n, path_json, value)]
        self.unreadable_since = {} # inbox file -> when it was first seen unreadable
        self.seen_order = {}       # inbox file -> when it was first seen (applies in arrival order)
        self.done = {}             # inbox file -> (mtime_ns, size) handled but could not be removed

    @contextlib.contextmanager
    def locked(self):
        with self.lock:
            self.write_lock.acquire()
            try:
                yield
            finally:
                self.write_lock.release()

    # -- lookup --
    def campaigns(self):
        return {f"{v}/{s}": d for v, s, d in core.find_campaigns(self.root)}

    def folder(self, key):
        return self.campaigns().get(key)

    def _digest(self, folder):
        p = pathlib.Path(folder) / "campaign.json"
        return core.file_digest(p) if p.is_file() else None

    def _n(self, key, folder):
        """The write count, bumped when campaign.json changed outside this server (a sync tool, `patch` while no
        server ran)."""
        d = self._digest(folder)
        if key not in self.revs:
            self.revs[key], self.digests[key] = 1, d
        elif d != self.digests.get(key):
            self.revs[key] += 1
            self.digests[key] = d
        return self.revs[key]

    def rev(self, key, folder):
        return f"{self.boot}-{self._n(key, folder)}"

    def _base_n(self, base_rev):
        """The write count a board's rev refers to; 0 if it came from before a restart."""
        if isinstance(base_rev, str) and base_rev.startswith(self.boot + "-"):
            try:
                return int(base_rev.rsplit("-", 1)[1])
            except ValueError:
                return 0
        return 0

    def _write(self, key, folder, campaign):
        core.save(folder, campaign)
        self.revs[key] = self.revs.get(key, 0) + 1
        self.digests[key] = self._digest(folder)

    # -- state for the board --
    def state(self, key, folder):
        with self.lock:
            campaign = core.load(folder)
            return {"key": key, "campaign": campaign, "rev": self.rev(key, folder),
                    "check": core.check(campaign, folder), "rejected": self._rejected(folder),
                    "pending": core.pending_content_ops(folder)}

    @staticmethod
    def _rejected(base):
        out = []
        rdir = pathlib.Path(base) / "inbox" / "rejected"
        if rdir.is_dir():
            for p in sorted(rdir.glob("*.json")):
                r = p.with_name(p.name + ".reason.txt")
                out.append({"name": p.name, "reason": r.read_text(encoding="utf-8") if r.is_file() else
                            "הבקשה לא נכנסה."})
        return out

    def summary(self):
        out = []
        for key, folder in self.campaigns().items():
            venture, slug = key.split("/", 1)
            try:
                c = core.load(folder)
                chk = core.check(c, folder)
                out.append({"key": key, "venture": venture, "slug": slug, "name": c.get("name") or slug,
                            "packed": chk["packed"], "kept": chk["kept"], "total": chk["total"],
                            "errors": len(chk["errors"]), "launched": bool((c.get("meta") or {}).get("campaign_id"))})
            except Exception as e:  # one broken campaign must not take the list down
                out.append({"key": key, "venture": venture, "slug": slug, "name": slug,
                            "broken": f"קובץ הקמפיין פגום ({type(e).__name__}). בקש מ-Claude לבדוק אותו."})
        return out

    def new_rejections(self):
        """Creates from Claude that were refused, per venture, so the board's home page can show them."""
        out = []
        for venture in core.ventures(self.root):
            for r in self._rejected(venture.joinpath(*core.CAMPAIGNS_SUB)):
                out.append({"venture": venture.name, **r})
        return out

    # -- writes --
    def board_ops(self, key, folder, ops, base_rev):
        """Applies each op on its own, so one stale op (an item Claude already removed) does not cost the founder
        the rest of his edits. Returns (conflicts, failures)."""
        if not isinstance(ops, list) or not ops:
            raise core.PatchError("אין שינויים בבקשה.")
        with self.locked():
            campaign = core.load(folder)
            self._n(key, folder)
            touched, failures = [], []
            for op in ops:
                try:
                    core.apply_op_safe(campaign, op, "board")
                    touched.append(json.dumps(op["path"], ensure_ascii=False))
                except core.PatchError as e:
                    failures.append(str(e))
            base_n = self._base_n(base_rev)
            conflicts = [{"path": json.loads(pj), "claude_value": value}
                         for n, pj, value in self.claude.get(key, []) if n > base_n and pj in touched]
            if touched:
                self._write(key, folder, campaign)
            return conflicts, failures

    def pack(self, key, folder, base_rev):
        with self.locked():
            if base_rev != self.rev(key, folder):
                return "הקמפיין השתנה בזמן שהסתכלת (אולי מהשיחה). בדוק שוב ולחץ ארוז."
            campaign = core.load(folder)
            if core.check(campaign, folder)["errors"]:
                return "יש עוד מה לתקן לפני שאורזים."
            campaign["packed_at"] = now_iso()
            campaign["packed_hash"] = core.content_hash(campaign, folder)
            self._write(key, folder, campaign)
            return None

    # -- the inbox --
    @staticmethod
    def _sig(path):
        try:
            st = path.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _consume(self, path):
        """Removes a handled inbox file. If it cannot be removed (a sync tool or antivirus holds it), it is remembered,
        so it is never applied twice."""
        try:
            path.unlink()
        except OSError:
            self.done[str(path)] = self._sig(path)
        self.seen_order.pop(str(path), None)
        self.unreadable_since.pop(str(path), None)

    def _reject(self, path, reason):
        try:
            rdir = path.parent / "rejected"
            rdir.mkdir(exist_ok=True)
            target = rdir / path.name
            if target.exists():
                target = rdir / f"{path.stem}-{secrets.token_hex(3)}{path.suffix}"
            shutil.copyfile(path, target)
            target.with_name(target.name + ".reason.txt").write_text(reason, encoding="utf-8")
        except OSError as e:
            print(f"studio: could not record a rejected request {path}: {e}", file=sys.stderr, flush=True)
        self._consume(path)

    def _ready(self, path):
        """The parsed patch, None to wait, or raises PatchError. Unreadable files get a grace period (a sync tool or
        Cowork may still be writing them)."""
        data = core.read_patch(path)
        if data is not None:
            self.unreadable_since.pop(str(path), None)
            return data
        t = self.unreadable_since.setdefault(str(path), time.monotonic())
        if time.monotonic() - t > UNREADABLE_GRACE:
            raise core.PatchError("הקובץ לא נקרא כ-JSON תקין גם אחרי 10 שניות.")
        return None

    def _inbox_files(self, inbox):
        if not inbox.is_dir():
            return []
        files = []
        for p in inbox.iterdir():
            if not core.is_patch_file(p):
                continue
            if str(p) in self.done:
                if self.done[str(p)] == self._sig(p):
                    continue          # already handled; only its removal failed
                del self.done[str(p)]
            files.append(p)
            self.seen_order.setdefault(str(p), time.monotonic())
        return sorted(files, key=lambda p: (self.seen_order[str(p)], p.name))

    def apply_inboxes(self):
        """Creates from each venture's campaigns/inbox, then edits from each campaign's inbox. A failure in one place
        is reported and skipped; it never stops the rest."""
        with self.locked():
            for venture in core.ventures(self.root):
                base = venture.joinpath(*core.CAMPAIGNS_SUB)
                for p in self._inbox_files(base / "inbox"):
                    self._apply_create(base, p)
            for key, folder in self.campaigns().items():
                try:
                    files = self._inbox_files(pathlib.Path(folder) / "inbox")
                    if files:
                        campaign = core.load(folder)
                        self._n(key, folder)
                except Exception as e:  # a broken campaign.json: its requests wait, others go on
                    print(f"studio: {key}: {e}", file=sys.stderr, flush=True)
                    continue
                for p in files:
                    campaign = self._apply_edit(key, folder, campaign, p)

    def _apply_create(self, base, path):
        try:
            data = self._ready(path)
            if data is None:
                return
            slug = data.get("create")
            if not isinstance(slug, str) or not core.SLUG_RE.match(slug) or slug.lower() in core.RESERVED_SLUGS:
                raise core.PatchError("שם תיקייה לא תקין לקמפיין (אותיות, מספרים, מקף).")
            folder = base / slug
            if (folder / "campaign.json").exists():
                raise core.PatchError(f"קמפיין בשם {slug} כבר קיים.")
            campaign = core._new_campaign(data)
            (folder / "images").mkdir(parents=True, exist_ok=True)
            core.save(folder, campaign)
            self._consume(path)
        except core.PatchError as e:
            self._reject(path, str(e))
        except Exception as e:
            self._reject(path, f"הבקשה פגומה ולא נכנסה ({type(e).__name__}).")

    def _apply_edit(self, key, folder, campaign, path):
        """Returns the campaign as it is after this file."""
        try:
            data = self._ready(path)
            if data is None:
                return campaign
            new, touched = core.apply_ops(campaign, data.get("ops"), "claude")
            self._write(key, folder, new)
            n = self.revs[key]
            log = self.claude.setdefault(key, [])
            for t in touched:
                log.append((n, json.dumps(t, ensure_ascii=False), core.get_path(new, t)))
            del log[:-200]
            self._consume(path)
            return new
        except core.PatchError as e:
            self._reject(path, str(e))
        except Exception as e:
            self._reject(path, f"הבקשה פגומה ולא נכנסה ({type(e).__name__}).")
        return campaign

    def dismiss(self, base, name):
        rdir = pathlib.Path(base) / "inbox" / "rejected"
        p = core.inside(rdir, name)
        if p and p.is_file():
            p.unlink()
            r = p.with_name(p.name + ".reason.txt")
            if r.exists():
                r.unlink()


# ---------- HTTP ----------

class Handler(http.server.BaseHTTPRequestHandler):
    studio: Studio
    port: int = 0

    def log_message(self, format, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self):
        """Only the board itself: blocks DNS rebinding (Host) and other sites' pages (Origin)."""
        allowed = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        if self.headers.get("Host") not in allowed:
            return False
        origin = self.headers.get("Origin")
        return origin is None or urllib.parse.urlparse(origin).netloc in allowed

    def _query(self):
        u = urllib.parse.urlparse(self.path)
        return u.path, {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}

    def _campaign(self, q):
        key = q.get("c", "")
        folder = self.studio.folder(key)
        return key, folder

    def _json_body(self, limit=2 * 1024 * 1024):
        n = self.headers.get("Content-Length")
        if n is None or not n.isdigit() or int(n) > limit:
            return None
        try:
            return json.loads(self.rfile.read(int(n)) or b"null")
        except ValueError:
            return None

    def do_GET(self):
        try:
            self._get()
        except Exception as e:  # say what broke instead of dropping the connection
            print(f"studio: GET {self.path}: {e}", file=sys.stderr, flush=True)
            self._send(500, {"error": f"שגיאה בלוח ({type(e).__name__}). נסה שוב, ואם זה חוזר, ספר ל-Claude."})

    def do_POST(self):
        try:
            self._post()
        except Exception as e:
            print(f"studio: POST {self.path}: {e}", file=sys.stderr, flush=True)
            self._send(500, {"error": f"שגיאה בלוח ({type(e).__name__}). השינוי לא נשמר. נסה שוב."})

    def _get(self):
        if not self._host_ok():
            return self._send(403, {"error": "host"})
        path, q = self._query()
        if path == "/":
            return self._send(200, BOARD.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/whoami":
            return self._send(200, {"app": "campaign-studio", "root": str(self.studio.root)})
        if path == "/api/campaigns":
            return self._send(200, {"campaigns": self.studio.summary(), "rejected": self.studio.new_rejections(),
                                    "root": str(self.studio.root)})
        if path == "/api/campaign":
            key, folder = self._campaign(q)
            if not folder:
                return self._send(404, {"error": "הקמפיין לא נמצא."})
            try:
                return self._send(200, self.studio.state(key, folder))
            except Exception as e:  # a broken file: say so instead of looking disconnected
                return self._send(500, {"error": f"קובץ הקמפיין פגום ולא נפתח ({type(e).__name__}). בקש מ-Claude לבדוק אותו."})
        if path.startswith("/files/"):
            parts = urllib.parse.unquote(path[len("/files/"):]).split("/", 2)
            if len(parts) == 3:
                folder = self.studio.folder(f"{parts[0]}/{parts[1]}")
                p = core.inside(folder, parts[2]) if folder else None
                ctype = core.IMAGE_TYPES.get(p.suffix.lower()) if p else None
                if p and ctype and p.is_file():
                    return self._send(200, p.read_bytes(), ctype)
            return self._send(404, {"error": "not found"})
        self._send(404, {"error": "not found"})

    def _post(self):
        if not self._host_ok() or self.headers.get("X-Studio") != "1":
            return self._send(403, {"error": "forbidden"})
        path, q = self._query()
        if path == "/api/dismiss-new":
            venture = q.get("v", "")
            if venture in {k.split("/")[0] for k in self.studio.campaigns()} | {
                    p.name for p in self.studio.root.iterdir() if p.is_dir()}:
                self.studio.dismiss(self.studio.root.joinpath(venture, *core.CAMPAIGNS_SUB), q.get("name", ""))
            return self._send(200, {"rejected": self.studio.new_rejections()})
        key, folder = self._campaign(q)
        if not folder:
            return self._send(404, {"error": "הקמפיין לא נמצא."})
        if path == "/api/ops":
            body = self._json_body()
            if not isinstance(body, dict):
                return self._send(400, {"error": "בקשה פגומה."})
            try:
                conflicts, failures = self.studio.board_ops(key, folder, body.get("ops"), body.get("base_rev"))
            except core.PatchError as e:
                return self._send(409, {"error": str(e), **self.studio.state(key, folder)})
            return self._send(200, {**self.studio.state(key, folder), "conflicts": conflicts, "failures": failures})
        if path == "/api/pack":
            body = self._json_body() or {}
            err = self.studio.pack(key, folder, body.get("base_rev"))
            return self._send(409 if err else 200, {"error": err, **self.studio.state(key, folder)})
        if path == "/api/dismiss":
            self.studio.dismiss(folder, q.get("name", ""))
            return self._send(200, self.studio.state(key, folder))
        if path == "/api/upload":
            name = core.safe_name(urllib.parse.unquote(self.headers.get("X-Filename") or ""))
            if not name:
                return self._send(400, {"error": "רק תמונות JPG, PNG או WEBP."})
            n = self.headers.get("Content-Length")
            if n is None or not n.isdigit() or int(n) == 0:
                return self._send(411, {"error": "ההעלאה נכשלה. נסה שוב."})
            if int(n) > MAX_UPLOAD:
                return self._send(413, {"error": "התמונה גדולה מ-15MB."})
            data = self.rfile.read(int(n))
            images = pathlib.Path(folder) / "images"
            images.mkdir(exist_ok=True)
            stem, ext = os.path.splitext(name)
            target, i = images / name, 2
            while target.exists():
                target, i = images / f"{stem}-{i}{ext}", i + 1
            target.write_bytes(data)
            return self._send(200, {"file": f"images/{target.name}"})
        self._send(404, {"error": "not found"})


def whoami(port):
    try:
        with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/api/whoami",
                                                           headers={"Host": f"127.0.0.1:{port}"}), timeout=2) as r:
            data = json.loads(r.read())
            return data if data.get("app") == "campaign-studio" else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def open_browser(url):
    for cmd in (["wslview", url], ["explorer.exe", url], ["xdg-open", url]):
        if shutil.which(cmd[0]):
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return


def serve(root, port, do_open):
    url = f"http://localhost:{port}/"
    running = whoami(port)
    if running:
        print(f"studio: already running on {url} (root {running.get('root')})", flush=True)
        if do_open:
            open_browser(url)
        return 0
    server_lock = RootLock(root, "server")
    if not server_lock.acquire(blocking=False):
        print(f"studio: another board server already serves {root}", file=sys.stderr, flush=True)
        return 2
    studio = Studio(root)
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        print(f"studio: port {port} is taken by something else ({e})", file=sys.stderr)
        return 2
    Handler.studio, Handler.port = studio, port

    def loop():
        while True:
            try:
                studio.apply_inboxes()
            except Exception as e:  # keep the board alive; the next round retries
                print(f"studio: inbox round failed: {e}", file=sys.stderr, flush=True)
            time.sleep(POLL)

    threading.Thread(target=loop, daemon=True).start()
    print(f"studio: {url} (root {root})", flush=True)
    if do_open:
        open_browser(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


# ---------- commands for Claude ----------

def cmd_new(folder, src, name):
    folder = pathlib.Path(folder)
    if (folder / "campaign.json").exists():
        print(f"studio: {folder}/campaign.json already exists", file=sys.stderr)
        return 2
    payload = {"campaign": {"name": name}} if not src else json.loads(pathlib.Path(src).read_text(encoding="utf-8"))
    if "campaign" not in payload:
        payload = {"campaign": payload}
    try:
        campaign = core._new_campaign(payload)
    except core.PatchError as e:
        print(f"studio: {e}", file=sys.stderr)
        return 1
    if name:
        campaign["name"] = name
    (folder / "images").mkdir(parents=True, exist_ok=True)
    core.save(folder, campaign)
    print(f"studio: created {folder}/campaign.json")
    return 0


def campaign_root(folder):
    """The root that holds the ventures, from a campaign folder <root>/<venture>/שיווק/קמפיינים/<slug>; None if the
    folder is not laid out that way."""
    folder = pathlib.Path(folder).resolve()
    if len(folder.parents) < 4 or (folder.parents[1].name, folder.parents[0].name) != core.CAMPAIGNS_SUB:
        return None
    return folder.parents[3]


def cmd_patch(folder, src, port=None):
    """Validates against the current file, writes the request into inbox/, and waits for the board server to apply
    it — or applies it here, under the same lock, when no server serves this root. Prints what really happened."""
    folder = pathlib.Path(folder).resolve()
    root = campaign_root(folder)
    if not root or not (folder / "campaign.json").is_file():
        print(f"studio: {folder} is not a campaign folder (<root>/<venture>/שיווק/קמפיינים/<slug>/campaign.json)",
              file=sys.stderr)
        return 2
    raw = sys.stdin.read() if src == "-" else pathlib.Path(src).read_text(encoding="utf-8-sig")
    data = json.loads(raw)
    ops = data.get("ops") if isinstance(data, dict) else data
    try:
        core.apply_ops(core.load(folder), ops, "claude")
    except core.PatchError as e:
        print(f"studio: rejected — {e}", file=sys.stderr)
        return 1
    inbox = folder / "inbox"
    inbox.mkdir(exist_ok=True)
    name = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.json"
    tmp = inbox / f".{name}.tmp"
    tmp.write_text(json.dumps({"format": core.PATCH_FORMAT, "ops": ops}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, inbox / name)
    probe = RootLock(root, "server")
    server_running = not probe.acquire(blocking=False)
    if not server_running:
        probe.release()
        Studio(root).apply_inboxes()
    else:
        for _ in range(40):
            if not (inbox / name).exists():
                break
            time.sleep(0.25)
    rejected = inbox / "rejected" / name
    if rejected.exists():
        reason = rejected.with_name(name + ".reason.txt")
        print("studio: rejected — " + (reason.read_text(encoding="utf-8") if reason.exists() else "?"), file=sys.stderr)
        return 1
    if (inbox / name).exists():
        print("studio: waiting in the inbox — the board server has not applied it yet", file=sys.stderr)
        return 3
    print("studio: applied" + (" by the board server" if server_running else " (no board server running)"))
    return 0


# ---------- autostart (Windows Startup folder, hidden) ----------

LAUNCHER_SCRIPT = r'''#!/bin/sh
# campaign-studio launcher: finds the installed marketing-os plugin and runs the board server.
# Written by studio.py install-autostart. Started hidden at Windows logon by the Startup shortcut "%(task)s".
ROOT=%(root)s
PY=$(command -v python3) || exit 1
SCRIPT=$("$PY" - <<'EOF'
import json, os, pathlib
p = pathlib.Path.home() / ".claude/plugins/installed_plugins.json"
best = None
try:
    data = json.loads(p.read_text())
    entries = (data.get("plugins") or data).get("marketing-os@business-os-marketplace") or []
    for e in entries:
        v = tuple(int(x) for x in str(e.get("version", "0")).split(".") if x.isdigit())
        s = pathlib.Path(e.get("installPath", "")) / "skills/campaign-studio/scripts/studio.py"
        if s.is_file() and (best is None or v > best[0]):
            best = (v, s)
except Exception:
    pass
print(best[1] if best else "")
EOF
)
[ -n "$SCRIPT" ] || SCRIPT=%(fallback)s
exec "$PY" "$SCRIPT" serve --root "$ROOT" --port %(port)d
'''


def _ps(script):
    return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True)


def _startup_link():
    r = _ps("[Environment]::GetFolderPath('Startup')")
    folder = r.stdout.strip()
    return folder + "\\" + LINK_NAME if r.returncode == 0 and folder else None


def install_autostart(root, port):
    """A hidden shortcut in the Windows Startup folder (no admin rights needed; Task Scheduler's logon trigger needs
    them). It runs conhost --headless, so no window opens."""
    LAUNCHER.parent.mkdir(parents=True, exist_ok=True)
    LAUNCHER.write_text(LAUNCHER_SCRIPT % {"task": LINK_NAME, "root": shlex.quote(str(root)), "port": port,
                                            "fallback": shlex.quote(str(HERE / "studio.py"))}, encoding="utf-8")
    LAUNCHER.chmod(0o755)
    link = _startup_link()
    if not link:
        print("studio: Windows Startup folder not found", file=sys.stderr)
        return 1
    distro = os.environ.get("WSL_DISTRO_NAME", "Ubuntu")
    args = f"--headless wsl.exe -d {distro} --exec {LAUNCHER}".replace("'", "''")
    r = _ps("$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%s'); "
            "$s.TargetPath = \"$env:WINDIR\\System32\\conhost.exe\"; $s.Arguments = '%s'; $s.WindowStyle = 7; "
            "$s.Description = 'Business OS campaign board (localhost:%d)'; $s.Save()" % (link.replace("'", "''"), args, port))
    if r.returncode != 0:
        print(r.stderr.strip(), file=sys.stderr)
        return 1
    print(f"startup: {link}\nlauncher: {LAUNCHER}")
    _ps(f"Start-Process -WindowStyle Hidden -FilePath \"$env:WINDIR\\System32\\conhost.exe\" -ArgumentList '{args}'")
    return 0


def uninstall_autostart():
    link = _startup_link()
    if link:
        _ps("Remove-Item -LiteralPath '%s' -ErrorAction SilentlyContinue" % link.replace("'", "''"))
    if LAUNCHER.exists():
        LAUNCHER.unlink()
    print("studio: autostart removed (a running board stops at the next restart)")
    return 0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("serve", "new", "patch", "check", "plan", "status", "install-autostart",
                                        "uninstall-autostart"))
    ap.add_argument("folder", nargs="?")
    ap.add_argument("src", nargs="?")
    ap.add_argument("--root", default=None)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--name", default="")
    ap.add_argument("--from", dest="from_file")
    a = ap.parse_args(argv)
    root = a.root or default_root()

    if a.command == "serve":
        return serve(root, a.port, a.open)
    if a.command == "status":
        w = whoami(a.port)
        print(json.dumps({"running": bool(w), "url": f"http://localhost:{a.port}/", **(w or {})}, ensure_ascii=False))
        return 0 if w else 1
    if a.command == "install-autostart":
        return install_autostart(root, a.port)
    if a.command == "uninstall-autostart":
        return uninstall_autostart()
    if not a.folder:
        ap.error("folder is required")
    folder = pathlib.Path(a.folder)
    if a.command == "new":
        return cmd_new(folder, a.from_file, a.name)
    if a.command == "patch":
        if not a.src:
            ap.error("patch needs a file of ops, or - for stdin")
        return cmd_patch(folder, a.src, a.port)
    campaign = core.load(folder)
    if a.command == "check":
        r = core.check(campaign, folder)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 1 if r["errors"] else 0
    if a.command == "plan":
        p, errs = core.plan(campaign, folder)
        print(json.dumps(p if not errs else {"errors": errs}, ensure_ascii=False, indent=2))
        return 1 if errs else 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
