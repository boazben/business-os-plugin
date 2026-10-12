"""Campaign studio core: the campaign file, its checks, the pack, the patch engine and the plan for Meta.

No I/O beyond the campaign folder. The server (studio.py) is the only writer of campaign.json; everyone else sends
patches — the board over HTTP, Claude as files in the campaign's inbox/.

Folder layout, under a root that holds the ventures (the founder's Windows project folder):

    <root>/<venture>/שיווק/קמפיינים/<slug>/campaign.json
                                           images/
                                           inbox/            patch files from Claude, applied then deleted
                                           inbox/rejected/   patches that could not be applied, with a reason
    <root>/<venture>/שיווק/קמפיינים/inbox/new-<slug>.json   a new campaign from Claude
"""
import copy
import hashlib
import json
import math
import os
import pathlib
import re
import secrets
import struct
import tempfile
import urllib.parse
from datetime import date

CAMPAIGNS_SUB = ("שיווק", "קמפיינים")
PATCH_FORMAT = 1

GOALS = {"traffic": "OUTCOME_TRAFFIC", "awareness": "OUTCOME_AWARENESS"}
CTAS = ("LEARN_MORE", "SIGN_UP", "SHOP_NOW", "BOOK_NOW", "ORDER_NOW", "CONTACT_US", "GET_OFFER", "SUBSCRIBE")
GENDERS = ("all", "female", "male")
HEADLINE_SOFT = 40
TEXT_SOFT = 125
MANY_ADS = 6
BUDGET_WARN_ILS = 100
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
SLUG_RE = re.compile(r"^[A-Za-z0-9א-ת][A-Za-z0-9א-ת_-]{0,59}$")

# Written once when a campaign is created; Meta object names are tagged with it, so a launch can find what exists.
FIXED = ("studio_id", "version")
# Not the campaign's content: the pack itself, notes from review, and Meta identifiers.
NOT_CONTENT = ("packed_at", "packed_hash", "review_notes", "meta")
# Only the founder decides these, from the board. A patch from Claude that touches one is rejected whole.
FOUNDER_ONLY = ("picks", "ai_images", "packed_at", "packed_hash")
# Only Claude writes these (review findings, Meta ids); the board shows them.
CLAUDE_ONLY = ("review_notes", "meta")
# Lists whose items carry an id; every other list is a plain value (countries).
ID_LISTS = ("angles", "images", "headlines", "texts")


class PatchError(Exception):
    """A patch that cannot be applied. The message is Hebrew and goes to the founder as is."""


# ---------- files ----------

def new_studio_id():
    return secrets.token_hex(5)


def empty_campaign(name=""):
    return {
        "version": 1, "studio_id": new_studio_id(), "name": name, "goal": "traffic", "link": "", "business_name": "",
        "budget_daily_ils": 30, "start": "", "end": "", "cta": "LEARN_MORE", "ai_images": None,
        "audience": {"countries": ["IL"], "age_min": 18, "age_max": 65, "gender": "all", "strict_age": False, "who": ""},
        "angles": [], "picks": {}, "review_notes": "", "packed_at": None, "packed_hash": None,
        "meta": {"ad_account_id": "", "page_id": "", "instagram_user_id": "", "campaign_id": ""},
    }


def load(folder):
    return json.loads((pathlib.Path(folder) / "campaign.json").read_text(encoding="utf-8"))


def save(folder, campaign):
    """Atomic: a dot-named temp file in the same folder, then rename. Sync tools skip dot files."""
    path = pathlib.Path(folder) / "campaign.json"
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".campaign-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(campaign, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def campaigns_dir(root, venture):
    return pathlib.Path(root, venture, *CAMPAIGNS_SUB)


def find_campaigns(root):
    """[(venture, slug, folder)] for every campaign folder that has a campaign.json."""
    out = []
    root = pathlib.Path(root)
    if not root.is_dir():
        return out
    for venture in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
        base = venture.joinpath(*CAMPAIGNS_SUB)
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if d.is_dir() and d.name not in ("inbox",) and (d / "campaign.json").is_file():
                out.append((venture.name, d.name, d))
    return out


def is_foreign(name):
    """Files that sync tools and editors leave next to ours (Google Drive, Office, Chrome)."""
    return (name.startswith((".", "~$")) or name.lower() in ("desktop.ini", "thumbs.db")
            or name.endswith((".tmp", ".crswap", ".reason.txt")) or bool(re.search(r" \(\d+\)\.[^.]+$", name)))


def conflict_copies(folder):
    """Google Drive / sync conflict copies of campaign.json, e.g. 'campaign (1).json'."""
    return sorted(p.name for p in pathlib.Path(folder).glob("campaign*.json") if p.name != "campaign.json")


# ---------- images ----------

def image_size(path):
    """(width, height) of a PNG or JPEG, or None. Enough to compare with what Meta reports after upload."""
    try:
        with open(path, "rb") as f:
            head = f.read(26)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                return struct.unpack(">II", head[16:24])
            if head[:2] != b"\xff\xd8":
                return None
            f.seek(2)
            while True:
                marker = f.read(2)
                if len(marker) < 2 or marker[0] != 0xFF:
                    return None
                if marker[1] in (0xD8, 0x01) or 0xD0 <= marker[1] <= 0xD7:
                    continue
                length = struct.unpack(">H", f.read(2))[0]
                if 0xC0 <= marker[1] <= 0xCF and marker[1] not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">xHH", f.read(5))
                    return (w, h)
                f.seek(length - 2, 1)
    except (OSError, struct.error):
        return None


def file_digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def inside(folder, rel):
    """The resolved path of rel if it stays inside folder, else None."""
    root = pathlib.Path(folder).resolve()
    p = (root / str(rel or "")).resolve()
    return p if root in p.parents else None


def safe_name(name):
    stem, ext = os.path.splitext(os.path.basename(name or ""))
    ext = ext.lower()
    if ext not in IMAGE_TYPES:
        return None
    stem = re.sub(r"[^A-Za-z0-9א-ת_-]+", "-", stem).strip("-")[:60] or "image"
    return stem + ext


# ---------- combos, picks, pack ----------

def combos(campaign):
    """Every ad the board offers: within each angle, image x headline x text."""
    out = []
    for a in campaign.get("angles") or []:
        for i in a.get("images") or []:
            for h in a.get("headlines") or []:
                for t in a.get("texts") or []:
                    out.append({"key": "|".join((a["id"], i["id"], h["id"], t["id"])), "angle": a, "image": i,
                                "headline": h, "text": t})
    return out


def kept(campaign):
    picks = campaign.get("picks") or {}
    return [c for c in combos(campaign) if picks.get(c["key"]) == "keep"]


def content_hash(campaign, folder):
    """The content minus NOT_CONTENT, plus the bytes of every image a kept ad uses. An image replaced under the same
    name changes the hash, so the pack no longer holds."""
    content = {k: v for k, v in campaign.items() if k not in NOT_CONTENT}
    images = {}
    for c in kept(campaign):
        f = c["image"].get("file") or ""
        p = inside(folder, f)
        images[f] = file_digest(p) if p and p.is_file() else None
    blob = json.dumps({"content": content, "images": images}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def is_packed(campaign, folder):
    return bool(campaign.get("packed_at")) and campaign.get("packed_hash") == content_hash(campaign, folder)


# ---------- checks ----------

def _date(s):
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _ids_ok(campaign):
    """Every id matches ID_RE and is unique in its list. Returns Hebrew errors."""
    errors = []
    angles = campaign.get("angles") or []
    if not isinstance(angles, list):
        return ["מבנה הזוויות פגום."]
    seen = set()
    for a in angles:
        if not isinstance(a, dict) or not ID_RE.match(str(a.get("id", ""))) or a["id"] in seen:
            errors.append("מזהה זווית חסר, כפול או לא תקין.")
            continue
        seen.add(a["id"])
        for lst in ("images", "headlines", "texts"):
            inner = set()
            for item in a.get(lst) or []:
                if not isinstance(item, dict) or not ID_RE.match(str(item.get("id", ""))) or item["id"] in inner:
                    errors.append(f"מזהה חסר, כפול או לא תקין בזווית «{a.get('name') or a['id']}».")
                    break
                inner.add(item["id"])
    return errors


def check(campaign, folder, today=None):
    """Hebrew messages for the board and the chat. Errors block packing; warnings do not."""
    today = today or date.today()
    errors, warnings = [], []
    errors += _ids_ok(campaign)
    if not str(campaign.get("name") or "").strip():
        errors.append("לקמפיין אין שם.")
    if campaign.get("goal") not in GOALS:
        errors.append("לא נבחר מה רוצים שיקרה (שיגיעו לאתר, או שיכירו אתכם).")
    parsed = urllib.parse.urlparse(str(campaign.get("link") or ""))
    if parsed.scheme != "https" or not parsed.netloc:
        errors.append("הקישור צריך להיות כתובת מלאה שמתחילה ב-https://")
    budget = campaign.get("budget_daily_ils")
    if not isinstance(budget, (int, float)) or isinstance(budget, bool) or not math.isfinite(budget) or budget <= 0:
        errors.append("תקציב יומי חסר או לא מספר.")
    elif budget > BUDGET_WARN_ILS:
        warnings.append(f"תקציב של {budget:g} ₪ ליום, בערך {budget * 30.4:,.0f} ₪ בחודש. לוודא שזה מה שהתכוונת.")
    if campaign.get("cta") not in CTAS:
        errors.append("לא נבחר כפתור למודעה.")
    if campaign.get("ai_images") not in (True, False):
        errors.append("צריך לסמן אם התמונות נוצרו ב-AI. Meta מבקש את ההצהרה, וההחלטה שלך.")
    start, end = _date(campaign.get("start")), _date(campaign.get("end"))
    if campaign.get("start") and not start or campaign.get("end") and not end:
        errors.append("תאריך לא תקין.")
    if start and start < today:
        warnings.append("תאריך ההתחלה כבר עבר. הקמפיין יתחיל ברגע שתפעיל אותו.")
    if end and (end <= today or (start and end <= start)):
        errors.append("תאריך הסיום צריך להיות אחרי היום ואחרי תאריך ההתחלה.")

    aud = campaign.get("audience") or {}
    if not aud.get("countries"):
        errors.append("לא נבחרה אף מדינה.")
    amin, amax = aud.get("age_min"), aud.get("age_max")
    if not isinstance(amin, int) or not isinstance(amax, int) or amin < 18 or amax > 65 or amin > amax:
        errors.append("טווח הגילים צריך להיות בין 18 ל-65, והמינימום לא גדול מהמקסימום.")
    if aud.get("gender") not in GENDERS:
        errors.append("מגדר לא תקין.")

    picks = campaign.get("picks") or {}
    if any(v not in ("keep", "drop") for v in picks.values()):
        errors.append("יש בחירה לא תקינה במודעות.")
    broken = any("מזהה" in e or "מבנה" in e for e in errors)
    ads = [] if broken else kept(campaign)
    if not ads:
        errors.append("עוד לא סימנת 👍 על אף מודעה.")
    seen = set()
    for c in ads:
        label = f"«{c['angle'].get('name') or 'זווית'}»"
        f = c["image"].get("file") or ""
        if f not in seen:
            seen.add(f)
            p = inside(folder, f)
            if not p or pathlib.Path(f).suffix.lower() not in IMAGE_TYPES:
                errors.append(f"התמונה {f or '(ריקה)'} בזווית {label} לא תקינה.")
            elif not p.is_file():
                errors.append(f"התמונה {f} בזווית {label} לא נמצאת בתיקייה.")
            else:
                try:
                    with open(p, "rb") as fh:
                        fh.read(1)
                except OSError:
                    errors.append(f"אי אפשר לקרוא את {f}. אולי היא שמורה רק בענן של Drive.")
        h, t = str(c["headline"].get("text") or "").strip(), str(c["text"].get("text") or "").strip()
        if not h or not t:
            errors.append(f"במודעה שסימנת בזווית {label} חסרים כותרת או טקסט.")
        if len(h) > HEADLINE_SOFT and ("h", h) not in seen:
            warnings.append(f"הכותרת «{h[:30]}…» ארוכה מ-{HEADLINE_SOFT} תווים, ובחלק מהמקומות היא תיחתך.")
            seen.add(("h", h))
        if len(t) > TEXT_SOFT and ("t", t) not in seen:
            warnings.append(f"הטקסט «{t[:30]}…» ארוך מ-{TEXT_SOFT} תווים. רואים רק את ההתחלה, עד «עוד».")
            seen.add(("t", t))
    if len(ads) > MANY_ADS:
        warnings.append(f"סימנת {len(ads)} מודעות. בתקציב קטן עדיפות 3 עד 6, אחרת Meta לומד לאט.")
    for name in conflict_copies(folder):
        warnings.append(f"נמצא עותק כפול {name}, כנראה מסנכרון Drive. הלוח עובד רק עם campaign.json.")
    return {"errors": errors, "warnings": warnings, "kept": len(ads), "total": 0 if broken else len(combos(campaign)),
            "packed": False if broken else is_packed(campaign, folder)}


# ---------- patch engine ----------

def _step(node, seg, path):
    if isinstance(seg, dict):
        if not isinstance(node, list) or set(seg) != {"id"}:
            raise PatchError(f"נתיב לא תקין: {path}")
        for item in node:
            if isinstance(item, dict) and item.get("id") == seg["id"]:
                return item
        raise PatchError(f"הפריט {seg['id']} לא קיים יותר (אולי נמחק בלוח).")
    if not isinstance(node, dict) or not isinstance(seg, str):
        raise PatchError(f"נתיב לא תקין: {path}")
    if seg not in node:
        raise PatchError(f"השדה {seg} לא קיים.")
    return node[seg]


def get_path(campaign, path):
    """The value at path, or None if it no longer exists."""
    node = campaign
    try:
        for seg in path:
            node = _step(node, seg, path)
    except PatchError:
        return None
    return node


def _guard(path, source):
    if not path or not isinstance(path[0], str):
        raise PatchError("נתיב ריק.")
    top = path[0]
    if top in FIXED:
        raise PatchError(f"«{top}» לא משתנה.")
    if source == "claude" and top in FOUNDER_ONLY:
        raise PatchError(f"רק המייסד משנה את «{top}», מהלוח.")
    if source == "board" and top in CLAUDE_ONLY + ("packed_at", "packed_hash"):
        raise PatchError(f"הלוח לא משנה את «{top}».")


def _str(v):
    return isinstance(v, str)


def _num_or_none(v):
    return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v))


def _int_or_none(v):
    return v is None or (isinstance(v, int) and not isinstance(v, bool))


def _picks(v):
    return isinstance(v, dict) and all(isinstance(k, str) and x in ("keep", "drop") for k, x in v.items())


def _countries(v):
    return isinstance(v, list) and all(isinstance(x, str) and re.fullmatch(r"[A-Z]{2}", x) for x in v)


# What a `set` may write, by the shape of its path ("*" is any {"id": ...} item).
SET_TYPES = {
    ("name",): _str, ("goal",): _str, ("link",): _str, ("business_name",): _str, ("start",): _str, ("end",): _str,
    ("cta",): _str, ("review_notes",): _str, ("budget_daily_ils",): _num_or_none,
    ("ai_images",): lambda v: v is None or isinstance(v, bool), ("picks",): _picks,
    ("audience", "countries"): _countries, ("audience", "age_min"): _int_or_none, ("audience", "age_max"): _int_or_none,
    ("audience", "gender"): _str, ("audience", "strict_age"): lambda v: isinstance(v, bool), ("audience", "who"): _str,
    ("meta", "ad_account_id"): _str, ("meta", "page_id"): _str, ("meta", "instagram_user_id"): _str,
    ("meta", "campaign_id"): _str,
    ("angles", "*", "name"): _str, ("angles", "*", "why"): _str,
    ("angles", "*", "headlines", "*", "text"): _str, ("angles", "*", "texts", "*", "text"): _str,
    ("angles", "*", "images", "*", "file"): _str,
}


def _shape(path):
    return tuple("*" if isinstance(seg, dict) else seg for seg in path)


def _item_ok(kind, item):
    """A new item for an id list: the right fields, the right types."""
    if not isinstance(item, dict) or not ID_RE.match(str(item.get("id", ""))):
        return False
    if kind in ("headlines", "texts"):
        return set(item) <= {"id", "text"} and _str(item.get("text", ""))
    if kind == "images":
        return set(item) == {"id", "file"} and _str(item["file"])
    if kind == "angles":
        if not set(item) <= {"id", "name", "why", "images", "headlines", "texts"}:
            return False
        if not (_str(item.get("name", "")) and _str(item.get("why", ""))):
            return False
        for sub in ("images", "headlines", "texts"):
            lst = item.get(sub, [])
            ids = [x.get("id") for x in lst if isinstance(x, dict)]
            if not isinstance(lst, list) or len(set(ids)) != len(lst) or not all(_item_ok(sub, x) for x in lst):
                return False
        return True
    return False


def apply_op(campaign, op, source):
    """One op on a campaign, in place. Raises PatchError (Hebrew) without changing anything it has not checked."""
    if not isinstance(op, dict) or op.get("op") not in ("set", "add", "remove") or not isinstance(op.get("path"), list):
        raise PatchError("בקשת שינוי פגומה.")
    path = op["path"]
    _guard(path, source)
    kind = op["op"]
    if kind == "set":
        check_type = SET_TYPES.get(_shape(path))
        if not check_type:
            raise PatchError(f"אי אפשר לשנות את {'/'.join(map(str, _shape(path)))} ישירות.")
        if not check_type(op.get("value")):
            raise PatchError(f"ערך מסוג לא נכון ל-{'/'.join(map(str, _shape(path)))}.")
        parent = campaign
        for seg in path[:-1]:
            parent = _step(parent, seg, path)
        if not isinstance(parent, dict):
            raise PatchError(f"נתיב לא תקין: {path}")
        parent[path[-1]] = copy.deepcopy(op["value"])
    elif kind == "add":
        target = campaign
        for seg in path:
            target = _step(target, seg, path)
        item = op.get("value")
        if not isinstance(target, list) or path[-1] not in ID_LISTS:
            raise PatchError("אפשר להוסיף רק לזוויות, תמונות, כותרות וטקסטים.")
        if not _item_ok(path[-1], item):
            raise PatchError("פריט חדש עם מזהה חסר או שדות לא נכונים.")
        if any(isinstance(x, dict) and x.get("id") == item["id"] for x in target):
            raise PatchError(f"המזהה {item['id']} כבר קיים.")
        if path[-1] == "angles":
            item = {"name": "", "why": "", "images": [], "headlines": [], "texts": [], **item}
        elif path[-1] in ("headlines", "texts"):
            item = {"text": "", **item}
        target.append(copy.deepcopy(item))
    else:
        if not isinstance(path[-1], dict):
            raise PatchError("מחיקה צריכה לכוון לפריט לפי מזהה.")
        parent = campaign
        for seg in path[:-1]:
            parent = _step(parent, seg, path)
        item = _step(parent, path[-1], path)
        if not isinstance(parent, list):
            raise PatchError(f"נתיב לא תקין: {path}")
        parent.remove(item)


def apply_ops(campaign, ops, source):
    """All or nothing: returns a new campaign and the list of paths it set, or raises PatchError.

    ops: [{"op": "set", "path": [...], "value": v}, {"op": "add", "path": [<id list>], "value": {"id": ...}},
          {"op": "remove", "path": [..., {"id": x}]}]. Path segments are keys, or {"id": x} for an item of an id list.
    """
    if not isinstance(ops, list) or not ops:
        raise PatchError("אין שינויים בבקשה.")
    out = copy.deepcopy(campaign)
    touched = []
    for op in ops:
        apply_op(out, op, source)
        touched.append(op["path"])
    return out, touched


def _new_campaign(payload):
    """A campaign from Claude's create file. Founder-only, pack and Meta fields are ignored."""
    c = empty_campaign()
    given = payload.get("campaign") or {}
    if not isinstance(given, dict):
        raise PatchError("תוכן הקמפיין פגום.")
    for k, v in given.items():
        if k in c and k not in FOUNDER_ONLY + FIXED + ("meta", "angles", "audience"):
            if SET_TYPES.get((k,), lambda _v: False)(v):
                c[k] = copy.deepcopy(v)
            else:
                raise PatchError(f"ערך מסוג לא נכון ל-{k}.")
    if not isinstance(given.get("audience") or {}, dict) or not isinstance(given.get("angles") or [], list):
        raise PatchError("מבנה הקהל או הזוויות פגום.")
    for k, v in (given.get("audience") or {}).items():
        if k in c["audience"]:
            if not SET_TYPES[("audience", k)](v):
                raise PatchError(f"ערך מסוג לא נכון ל-audience/{k}.")
            c["audience"][k] = copy.deepcopy(v)
    for a in given.get("angles") or []:
        if not _item_ok("angles", a) or any(x["id"] == a["id"] for x in c["angles"]):
            raise PatchError("זווית עם מזהה חסר, כפול או שדות לא נכונים.")
        c["angles"].append({"name": "", "why": "", "images": [], "headlines": [], "texts": [], **copy.deepcopy(a)})
    return c


def read_patch(path):
    """The parsed patch file, or None while it is still unreadable (a half-written file). Raises PatchError on a
    wrong format."""
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError, OSError):
        return None
    if not isinstance(data, dict) or data.get("format") != PATCH_FORMAT:
        raise PatchError(f"פורמט לא מוכר (צריך format: {PATCH_FORMAT}). אולי גרסה אחרת של התוסף כתבה אותו.")
    return data


def pending_content_ops(folder):
    """How many ops in the campaign's inbox touch content (anything but meta and review_notes)."""
    n = 0
    inbox = pathlib.Path(folder) / "inbox"
    if not inbox.is_dir():
        return 0
    for p in inbox.iterdir():
        if not p.is_file() or is_foreign(p.name) or p.suffix != ".json":
            continue
        try:
            data = read_patch(p)
        except PatchError:
            continue
        if data is None:
            n += 1
            continue
        for op in data.get("ops") or []:
            top = (op.get("path") or [None])[0] if isinstance(op, dict) else None
            if top not in NOT_CONTENT:
                n += 1
    return n


# ---------- the plan for Meta ----------

def tag(studio_id, key=None):
    """The stable marker in every Meta object's name. A launch finds what already exists by searching for it, so a
    repeated or interrupted launch creates only what is missing. The rest of the name is for the founder's eyes."""
    return f"[studio:{studio_id}|{key}]" if key else f"[studio:{studio_id}]"


def plan(campaign, folder, inbox_content_ops=None):
    """What to create in Meta. Refuses unless packed, unchanged since, clean, and nothing waits in the inbox."""
    pending = pending_content_ops(folder) if inbox_content_ops is None else inbox_content_ops
    if pending:
        return None, ["יש שינויים מהשיחה שעוד לא נכנסו ללוח. פתח את הלוח כדי שייכנסו, ואז ארוז שוב."]
    result = check(campaign, folder)
    if not result["packed"]:
        return None, ["הקמפיין לא ארוז, או שהשתנה אחרי שנארז (כולל תמונה שהתחלפה). ארוז אותו שוב בלוח."]
    if result["errors"]:
        return None, result["errors"]
    slug = campaign.get("studio_id")
    if not isinstance(slug, str) or not ID_RE.match(slug):
        return None, ["לקמפיין חסר מזהה סטודיו (studio_id). צור אותו מחדש דרך הסטודיו."]
    aud = campaign["audience"]
    targeting = {"geo_locations": {"countries": aud["countries"]}, "age_min": aud["age_min"], "age_max": aud["age_max"]}
    if aud["gender"] != "all":
        targeting["genders"] = [2 if aud["gender"] == "female" else 1]
    if aud.get("strict_age"):
        targeting["targeting_automation"] = {"advantage_audience": 0}
    ad_set = {"name": f"קהל {tag(slug)}", "optimization_goal": "LINK_CLICKS" if campaign["goal"] == "traffic" else "REACH",
              "billing_event": "IMPRESSIONS", "targeting": targeting}
    if campaign["goal"] == "traffic":
        ad_set["destination_type"] = "WEBSITE"
    ads = []
    for n, c in enumerate(kept(campaign), 1):
        p = inside(folder, c["image"]["file"])
        ads.append({"name": f"{c['angle'].get('name') or 'מודעה'} {n} {tag(slug, c['key'])}", "tag": tag(slug, c["key"]),
                    "key": c["key"], "angle": c["angle"].get("name") or "",
                    "image": str(p), "image_file": c["image"]["file"], "image_size": image_size(p),
                    "image_sha256": file_digest(p),
                    "headline": c["headline"]["text"].strip(), "text": c["text"]["text"].strip()})
    return {
        "tag": tag(slug),
        "packed_hash": campaign["packed_hash"],
        "campaign": {"name": f"{campaign['name']} {tag(slug)}", "objective": GOALS[campaign["goal"]], "buying_type": "AUCTION",
                     "campaign_daily_budget": int(round(campaign["budget_daily_ils"] * 100)), "budget_currency": "ILS",
                     "start": campaign.get("start") or None, "end": campaign.get("end") or None},
        "ad_set": ad_set,
        "creative": {"link_url": campaign["link"], "call_to_action_type": campaign["cta"],
                     "self_ai_disclosure": "OPT_IN" if campaign["ai_images"] else "OPT_OUT"},
        "ads": ads,
        "meta": campaign.get("meta") or {},
    }, []
