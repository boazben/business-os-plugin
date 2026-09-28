#!/usr/bin/env python3
"""Import-and-sale feasibility simulation: the data file, its checks, and the page.

  sim.py traits                                   product traits, and how to tell each one
  sim.py init --name N --traits a,b --dir D [--facts F] [--desc TEXT]
                                                  new data file + the research list (fields and checks)
  sim.py check FILE.json [--draft]                what is missing or inconsistent; exit 1 on errors
  sim.py merge FILE.json FRAGMENT.json... [--founder]
                                                  research fragments into the data file; --founder = the
                                                  "copy values" JSON from the page (the founder's numbers)
  sim.py render FILE.json [--out HTML] [--facts F] [--draft]
                                                  the page, and the summary lines for the chat
  sim.py compare FILE.json OFFERS.json [--alt-landed ILS] [--alt-price ILS] [--prices P,P] [--margins M,M]
                 [--csv OUT] [--decision]         offers on one basis: landed cost, one-time costs per unit, profit,
                                                  cash, break-even and the walk-away buy price; --csv = the sheet, from
                                                  the same code (with the price x margin table).
                                                  OFFERS: {"offers": [{"name", "supplier", "spec", "note", "set": {field: {...}}}]}
                                                  — an alternative is another supplier, same quantity, same "spec"
                                                  — a money value in "set" carries its "unit" and "per"
  --decision (check, compare): a purchase decision, not a simulation — the exchange rate and the freight prices
                                                  of the chosen mode verified or partial, re-checked today ("checked"),
                                                  their source recent; no market number from a ruling; each offer's
                                                  supplier price and quantity sourced

Everything is computed here (engine.py, and engine.js on the page). The model researches and writes
values with a source; it does not calculate.

A fragment (for merge) holds any of these keys; ids are the ones in fields.json and in the data file's checks:
  {"fields": {"duty": {"value": 6, "low": 0, "high": 12, "status": "partial", "source": "משפטי/NN",
                       "date": "2026-01-01", "checked": "2026-01-02", "note": "שני פרטי מכס אפשריים — לא הוכרע"}},
   "checks": [{"id": "hs_duty", "applies": "yes", "finding": "...", "impact": "...", "status": "partial",
               "source": "...", "date": "..."}],
   "open": [{"id": "NN.3", "check": "hs_duty", "text": "מה פתוח", "who": "מי סוגר ואיך", "blocking": false}],
   "hypotheses": [{"text": "ההנחה", "test": "הבדיקה הזולה", "cost": "כמה עולה"}],
   "verdict": "השורה התחתונה"}
Money fields also take "unit" (usd/ils/pct) and "per" (as allowed in fields.json). Statuses: verified, partial,
estimate (needs "note"), founder, na. "date" = the value's own date per its source; "checked" = the day someone
looked at that source (a decision needs it to be today). "founder" = a value the founder set on the page: fine for a
simulation, never a source for a decision or for the shared facts.
"""
import argparse
import datetime as dt
import hashlib
import html
import json
import math
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engine  # noqa: E402

CATALOG = json.loads((HERE / "fields.json").read_text(encoding="utf-8"))
CAT = CATALOG["fields"]
TRAITS = json.loads((HERE / "traits.json").read_text(encoding="utf-8"))
FIELD_STATUS = {"verified": "אומת", "partial": "אומת חלקית", "estimate": "הערכה", "founder": "מהמייסד",
                "na": "לא רלוונטי", "missing": "חסר"}
CHECK_STATUS = {"verified", "partial", "estimate", "missing"}
APPLIES = {"yes": "חל", "no": "לא חל", "unknown": "לא ידוע"}
SOURCED = {"verified", "partial", "founder"}
EVIDENCED = {"verified", "partial"}  # a source someone checked: decisions and shared facts need this, not "founder"
PER_LABEL = {"unit": "ליחידה", "shipment": "למשלוח", "sale": "להזמנה", "once": "חד־פעמי", "month": "לחודש",
             "year": "לשנה", "kg": "לק״ג", "cbm": "לקוב"}
BAD_NAME = re.compile(r'[\\/:*?"<>|]')


TODAY = dt.date.today()
REUSE_DAYS = 90


def today():
    return TODAY.isoformat()


def parse_date(s):
    try:
        return dt.date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def files_for(name, folder):
    base = BAD_NAME.sub("-", f"סימולציית כדאיות - {name}").strip()
    folder = pathlib.Path(folder)
    return folder / f"{base}.json", folder / f"{base}.html"


def load(path):
    """The data file; a field the catalog gained since the file was made comes in as missing, to be researched."""
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if isinstance(data.get("fields"), dict):
        for k in CAT:
            data["fields"].setdefault(k, dict(empty_field(k), note="שדה חדש בסקיל — לחקור"))
    return data


def save(path, data):
    data["updated"] = today()
    pathlib.Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def all_checks(traits):
    out = [dict(c) for c in TRAITS["base"]]
    for t in traits:
        out += [dict(c, trait=t) for c in TRAITS["traits"][t]["checks"]]
    return out


def empty_field(k):
    c = CAT[k]
    f = {"value": None, "status": "missing", "source": "", "date": "", "note": ""}
    if c["kind"] != "choice":
        f.update(low=None, high=None)
    if c["kind"] == "money":
        f.update(unit=c["units"][0], per=c["pers"][0])
    return f


# ---------- facts shared by the products of one venture ----------

def load_facts(path):
    p = pathlib.Path(path) if path else None
    if p and p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"_doc": "עובדות שחוזרות בכל סימולציה (שער, מע״מ, סליקה, שחרור...). מתעדכן מ-sim.py render --facts.", "facts": {}}


def fact_age(f):
    d = parse_date(f.get("date"))
    return (TODAY - d).days if d else None


def sync_facts(data, path):
    facts = load_facts(path)
    changed = []
    for k, f in data["fields"].items():
        key = CAT[k].get("fact")
        if not key or f["status"] not in EVIDENCED or CAT[k].get("mode_dependent"):
            continue
        if parse_date(f.get("date")) and parse_date(f.get("date")) > TODAY:
            continue
        old = facts["facts"].get(key)
        new_d, old_d = parse_date(f.get("date")), parse_date(old.get("date")) if old else None
        if old is None or (new_d and (not old_d or new_d > old_d)):
            facts["facts"][key] = {x: f.get(x) for x in ("value", "unit", "per", "low", "high", "status", "source", "date", "note")
                                   if f.get(x) is not None}
            changed.append(k)
    if changed:
        pathlib.Path(path).write_text(json.dumps(facts, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return changed


# ---------- init ----------

def cmd_traits(_):
    for t, v in TRAITS["traits"].items():
        print(f"{t} — {v['label']}: {v['tell']}")


def cmd_init(a):
    traits = [t.strip() for t in a.traits.split(",") if t.strip()] if a.traits else []
    unknown = [t for t in traits if t not in TRAITS["traits"]]
    if unknown:
        sys.exit(f"תכונה לא מוכרת: {', '.join(unknown)} (sim.py traits)")
    jpath, _ = files_for(a.name, a.dir)
    if jpath.exists() and not a.force:
        sys.exit(f"כבר קיים: {jpath}\nלעדכן: merge (הקובץ חי — ההיסטוריה שלו בפנים)")
    fields = {k: empty_field(k) for k in CAT}
    for k, need in TRAITS["na_unless"].items():
        if not set(need) & set(traits):
            fields[k].update(value=0, status="na", note="לא רלוונטי למוצר הזה")
            if CAT[k]["kind"] == "money":
                fields[k]["unit"] = CAT[k]["units"][0]
    facts = load_facts(a.facts)["facts"]
    reused, stale = [], []
    for k, c in CAT.items():
        f = facts.get(c.get("fact") or "")
        if not f or fields[k]["status"] == "na":
            continue
        fields[k].update({x: f[x] for x in f})
        age = fact_age(f)
        (stale if age is None or age > c.get("max_age", 90) else reused).append((k, age))
    checks = [dict(c, applies="unknown", finding="", status="missing", source="", date="") for c in all_checks(traits)]
    copied, open_rows = reuse_checks(checks, jpath)
    data = {
        "schema": 1,
        "product": {"id": hashlib.sha1(a.name.encode()).hexdigest()[:10], "name": a.name,
                    "description": a.desc or "", "traits": traits},
        "created": today(), "updated": today(), "verdict": "",
        "fields": fields,
        "checks": checks,
        "hypotheses": [], "open": open_rows,
        "history": [f"{today()} — נוצר (תכונות: {', '.join(traits) or 'אין'})"],
    }
    jpath.parent.mkdir(parents=True, exist_ok=True)
    save(jpath, data)
    print(f"נוצר: {jpath}")
    if reused:
        print("מהעובדות המשותפות (בתוקף): " + ", ".join(f"{k} ({d} ימים)" for k, d in reused))
    if stale:
        print("עובדות ישנות — לבדוק שוב: " + ", ".join(f"{k} ({d if d is not None else '?'} ימים)" for k, d in stale))
    if copied:
        print("בדיקות שהועתקו מסימולציה קודמת (לעבור עליהן, לא לחקור שוב): " + ", ".join(f"{c} ({src})" for c, src in copied))
    todo = [k for k, f in fields.items() if f["status"] == "missing"]
    print(f"\nמה חסר ({len(todo)} שדות), לפי מי ממלא:")
    for owner, who in (("main", "אתה — החלטות ועובדות של העסק"), ("checks", "מחקר המשמעויות, יחד עם הבדיקות"),
                       ("costs", "מחקר המספרים")):
        ks = [k for k in todo if CAT[k]["owner"] == owner]
        if ks:
            print(f"  [{who}] " + " · ".join(f"{k} ({CAT[k]['label']})" for k in ks))
    left = [c for c in data["checks"] if c["status"] == "missing"]
    print(f"\nבדיקות משמעות לחקור ({len(left)}; מה לברר ואיפה — ב-verify וב-where בקובץ), ⚠ = יכולה לעצור:")
    print("  " + " · ".join(f"{'⚠' if c['can_kill'] else ''}{c['id']}" for c in left))


def reuse_checks(checks, jpath):
    """Copy the answers of general checks (not scope product) from the other simulations in the products
    folder, when they have a source and are younger than REUSE_DAYS; the open rows tied to them come along."""
    best = {}
    for other in sorted(jpath.parent.parent.glob("*/סימולציית כדאיות - *.json")):
        if other.resolve() == jpath.resolve():
            continue
        try:
            od = load(other)
        except (OSError, ValueError):
            continue
        for c in od.get("checks", []):
            d, age = parse_date(c.get("date")), fact_age(c)
            if c.get("status") in ("verified", "partial") and d and age is not None and age <= REUSE_DAYS:
                if c["id"] not in best or d > parse_date(best[c["id"]][0].get("date")):
                    best[c["id"]] = (c, od, other.parent.name)
    copied, open_rows = [], []
    for c in checks:
        if c.get("scope") == "product" or c["id"] not in best:
            continue
        src, od, name = best[c["id"]]
        c.update({x: src[x] for x in ("applies", "finding", "impact", "status", "source", "date") if x in src},
                 reused_from=name)
        copied.append((c["id"], name))
        open_rows += [o for o in od.get("open", []) if o.get("check") == c["id"] and o not in open_rows]
    return copied, open_rows


# ---------- check ----------

def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def validate(data, draft=False):
    err, warn = [], []
    miss = warn if draft else err
    traits = data.get("product", {}).get("traits", [])
    for t in traits:
        if t not in TRAITS["traits"]:
            err.append(f"תכונה לא מוכרת: {t}")
    fields = data.get("fields", {})
    for k, c in CAT.items():
        f = fields.get(k)
        if f is None:
            err.append(f"{k}: חסר בקובץ")
            continue
        st = f.get("status")
        if st not in FIELD_STATUS:
            err.append(f"{k}: סטטוס לא מוכר '{st}'")
            continue
        if st == "missing" or f.get("value") is None:
            miss.append(f"{k} ({c['label']}): אין ערך")
            continue
        v = f["value"]
        if c["kind"] == "choice":
            if v not in c["options"]:
                err.append(f"{k}: '{v}' לא אחת מ-{list(c['options'])}")
        elif not is_num(v):
            err.append(f"{k}: הערך '{v}' אינו מספר")
            continue
        if c["kind"] == "money":
            if f.get("unit") not in c["units"]:
                err.append(f"{k}: יחידה '{f.get('unit')}' לא מותרת ({c['units']})")
            if f.get("per") not in c["pers"]:
                err.append(f"{k}: בסיס '{f.get('per')}' לא מותר ({c['pers']})")
        if is_num(v) and v < 0:
            err.append(f"{k}: ערך שלילי")
        if st in SOURCED and not (f.get("source") and f.get("date")):
            err.append(f"{k}: '{FIELD_STATUS[st]}' בלי מקור ותאריך")
        if st == "estimate" and not f.get("note"):
            err.append(f"{k}: הערכה בלי נימוק (note)")
        for dk in ("date", "checked"):
            if f.get(dk) and not parse_date(f[dk]):
                err.append(f"{k}: {dk} '{f[dk]}' לא בפורמט YYYY-MM-DD")
            elif f.get(dk) and parse_date(f[dk]) > TODAY:
                err.append(f"{k}: {dk} {f[dk]} בעתיד")
        if st != "na" and c["kind"] != "choice" and is_num(v):
            lo, hi = f.get("low"), f.get("high")
            if lo is None or hi is None:
                if not c.get("decision"):
                    warn.append(f"{k}: אין טווח סביר (low/high) — לא ייכנס לרגישות ולתרחישים")
            elif not (is_num(lo) and is_num(hi) and lo <= v <= hi):
                err.append(f"{k}: הטווח {lo}–{hi} לא מכיל את הערך {v}")
        bm = f.get("by_mode")
        if bm is not None and (not isinstance(bm, dict) or not all(isinstance(e, dict) for e in bm.values())):
            err.append(f"{k}: by_mode חייב להיות {{דרך: {{value, low, high}}}}")
            bm = None
        if bm:
            modes = CAT["freight_mode"]["options"]
            if not c.get("mode_dependent"):
                err.append(f"{k}: by_mode רק בשדות שתלויים בדרך השילוח")
            for m, e in bm.items():
                if m not in modes or not is_num(e.get("value")):
                    err.append(f"{k}: by_mode.{m} — דרך לא מוכרת או ערך לא מספרי")
                elif is_num(e.get("low")) and is_num(e.get("high")) and not e["low"] <= e["value"] <= e["high"]:
                    err.append(f"{k}: by_mode.{m} — הטווח לא מכיל את הערך")
                elif c["kind"] == "money" and (e.get("unit", f.get("unit")) not in c["units"] or e.get("per", f.get("per")) not in c["pers"]):
                    err.append(f"{k}: by_mode.{m} — יחידה או בסיס לא מותרים")
            chosen = fields.get("freight_mode", {}).get("value")
            if chosen in bm and is_num(bm[chosen].get("value")) and (bm[chosen]["value"] != v or
                    (bm[chosen].get("unit", f.get("unit")), bm[chosen].get("per", f.get("per"))) != (f.get("unit"), f.get("per"))):
                err.append(f"{k}: by_mode.{chosen} שונה מהערך הראשי, והדרך שנבחרה היא {chosen}")
        age = fact_age(f)
        if c.get("max_age") and st in SOURCED and age is not None and age > c["max_age"]:
            warn.append(f"{k}: נבדק לפני {age} ימים (בתוקף {c['max_age']}) — לבדוק שוב")
    if is_num(fields.get("fx", {}).get("value")) and fields["fx"]["value"] <= 0:
        err.append("fx: שער חייב להיות חיובי")
    if is_num(fields.get("ret_rate", {}).get("value")) and fields["ret_rate"]["value"] >= engine.RATE_HI["ret_rate"]:
        err.append("ret_rate: שיעור החזרות לא סביר")
    if is_num(fields.get("defect", {}).get("value")) and fields["defect"]["value"] > engine.RATE_HI["defect"]:
        err.append("defect: שיעור פגומים לא סביר")
    if is_num(fields.get("qty", {}).get("value")) and fields["qty"]["value"] < 1:
        err.append("qty: כמות חייבת להיות לפחות 1")
    mo, spm = fields.get("monthly", {}), fields.get("sales_pm", {})
    if is_num(mo.get("value")) and mo["value"] > 0 and mo.get("status") != "na" and not (is_num(spm.get("value")) and spm["value"] > 0):
        err.append("sales_pm: יש הוצאות קבועות בחודש, וקצב מכירה 0 — הן לא נכנסות לחישוב")
    open_refs = {o.get("check") for o in data.get("open", [])}
    for c in data.get("checks", []):
        cid = c.get("id", "?")
        if c.get("applies") not in APPLIES:
            err.append(f"בדיקה {cid}: applies חייב להיות yes/no/unknown")
        if c.get("status") not in CHECK_STATUS:
            err.append(f"בדיקה {cid}: סטטוס לא מוכר")
        if not c.get("finding"):
            miss.append(f"בדיקה {cid} ({c.get('title')}): אין ממצא")
        elif c.get("status") in ("verified", "partial") and not c.get("source"):
            err.append(f"בדיקה {cid}: '{c['status']}' בלי מקור")
        if c.get("can_kill") and c.get("applies") != "no" and cid not in open_refs and c.get("status") not in ("verified",):
            warn.append(f"בדיקה {cid} יכולה לעצור ולא סגורה — אין לה שורה ב-open (check: {cid})")
    if not data.get("verdict"):
        miss.append("verdict: אין שורה תחתונה")
    if not data.get("hypotheses"):
        warn.append("hypotheses: אין השערות לבדיקה")
    if not err:
        try:
            r = engine.compute(engine_fields(data))
            if not math.isfinite(r["profit_first"]):
                err.append("החישוב לא נותן מספר — בדוק כמות, שער ומחיר")
        except (ZeroDivisionError, KeyError, TypeError, ValueError) as e:
            err.append(f"החישוב נכשל: {e!r}")
    return err, warn


DECISION_DAY = {"air": ["air_rate"], "sea": ["sea_rate"], "fixed": ["freight_fixed"]}
DECISION_MAX_AGE = {"fx": 5}  # days between the value's own date and the decision; freight: 30
LEGAL_POINTS = {"duty", "purchase_tax", "vat", "exempt_ceiling", "dealer", "ret_fee", "ret_fee_cap"}  # a ruling decides these; the rest is market
RULING = re.compile(r"(?:משפט(?:י|ים)?|legal)\s*[-–:/\\／⁄]?\s*\d+|פסיק(?:ה|ת|ות)", re.I)
BIDI = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def decision_errors(data):
    """A purchase decision needs today's exchange rate and freight prices, and market numbers from their own source."""
    fields = data["fields"]
    mode = fields["freight_mode"]["value"]
    need = ["fx"] + DECISION_DAY.get(mode, []) + [k for k in ("freight_extra", "dg_surcharge")
                                                  if fields[k]["status"] != "na" and fields[k].get("value")]
    err = []
    for k in need:
        f = fields[k]
        d, checked = parse_date(f.get("date")), parse_date(f.get("checked") or f.get("date"))
        max_age = DECISION_MAX_AGE.get(k, 30)
        if f["status"] not in EVIDENCED:
            err.append(f"{k} ({CAT[k]['label']}): בהחלטת קנייה — ממקור שנבדק (עכשיו: {FIELD_STATUS.get(f['status'])})")
        elif checked != TODAY:
            err.append(f"{k} ({CAT[k]['label']}): בהחלטת קנייה — לבדוק את המקור היום ולרשום checked (עכשיו: {f.get('checked') or f.get('date') or 'בלי תאריך'})")
        elif not d or (TODAY - d).days > max_age:
            err.append(f"{k} ({CAT[k]['label']}): המקור מ-{f.get('date') or '?'} — ישן מ-{max_age} ימים להחלטת קנייה")
    for k, c in CAT.items():
        f = fields[k]
        if k in LEGAL_POINTS or c["kind"] == "choice" or f["status"] == "na":
            continue
        if RULING.search(BIDI.sub("", f.get("source") or "")):
            err.append(f"{k} ({c['label']}): מספר שוק מתוך פסיקה — לבדוק מחדש במקור שלו")
    return err


def cmd_check(a):
    data = load(a.file)
    err, warn = validate(data, a.draft)
    if a.decision:
        err += decision_errors(data)
    for w in warn:
        print("אזהרה: " + w)
    for e in err:
        print("שגיאה: " + e)
    print(f"{len(err)} שגיאות, {len(warn)} אזהרות")
    sys.exit(1 if err else 0)


# ---------- merge ----------

def cmd_merge(a):
    data = load(a.file)
    before_err = set(validate(data, draft=True)[0])
    notes, changes = [], []
    for frag_path in a.fragments:
        frag = json.loads(sys.stdin.read() if frag_path == "-" else pathlib.Path(frag_path).read_text(encoding="utf-8"))
        if a.founder or "values" in frag:
            stamp = page_data(data)["stamp"]
            if not a.any_page and (frag.get("product") != data["product"].get("id") or frag.get("stamp") != stamp):
                sys.exit(f"{frag_path}: הערכים מדף אחר (מוצר {frag.get('product')!r}, גרסה {frag.get('stamp')!r}; "
                         f"כאן {data['product'].get('id')!r}, {stamp!r}). הדף ישן או של מוצר אחר — לפתוח את הדף העדכני "
                         "ולהעתיק שוב, או --any-page אם זה בכוונה")
            F0 = engine_fields(data)
            r0 = engine.compute(F0)
            n = 0
            for k, v in frag.get("values", {}).items():
                if k not in data["fields"]:
                    continue
                f, c = data["fields"][k], CAT[k]
                before = (f.get("unit"), f.get("per"))
                stamp_by_mode(f)
                changes.append(f"{k}: {f.get('value')}→{v.get('value')}")
                f.update({x: v[x] for x in ("value", "unit", "per") if x in v},
                         status="founder", source="המייסד, בסימולציה", date=today())
                f.pop("checked", None)
                if c["kind"] != "choice" and is_num(f["value"]):
                    lo, hi = f.get("low"), f.get("high")
                    if (f.get("unit"), f.get("per")) != before and is_num(lo) and is_num(hi):
                        fac = engine.conv_factor(r0, F0["fx"]["value"], c, *before, f.get("unit"), f.get("per"))
                        lo, hi = (lo * fac, hi * fac) if fac else (None, None)

                    f["low"] = min(lo, f["value"]) if is_num(lo) else f["value"]
                    f["high"] = max(hi, f["value"]) if is_num(hi) else f["value"]
                n += 1
            notes.append(f"{n} ערכים מהמייסד")
            continue
        if not isinstance(frag.get("fields", {}), dict):
            sys.exit(f"{frag_path}: fields חייב להיות {{שדה: {{value, ...}}}}")
        for k, v in frag.get("fields", {}).items():
            if k not in CAT:
                sys.exit(f"שדה לא מוכר בקטע: {k}")
            if not isinstance(v, dict):
                sys.exit(f"{frag_path}: {k} חייב להיות {{value, unit, per, status, source, date, ...}}, לא {v!r}")
            bm = v.get("by_mode")
            if bm is not None and (not isinstance(bm, dict) or not all(isinstance(e, dict) for e in bm.values())):
                sys.exit(f"{frag_path}: {k}.by_mode חייב להיות {{דרך: {{value, low, high, ...}}}}")
            old = data["fields"][k]
            stamp_by_mode(old)
            for e in (v.get("by_mode") or {}).values():
                e.setdefault("unit", v.get("unit", old.get("unit")))
                e.setdefault("per", v.get("per", old.get("per")))
            if v.get("by_mode") and isinstance(old.get("by_mode"), dict):  # per mode: a fragment about sea keeps air
                v = dict(v, by_mode=dict(old["by_mode"], **v["by_mode"]))
            for x in ("value", "date", "checked", "source", "status"):
                if x in v and v[x] != old.get(x):
                    changes.append(f"{k}.{x}: {old.get(x)}→{v[x]}")
            old.update(v)
        by_id = {c["id"]: c for c in data["checks"]}
        checks = frag.get("checks", [])
        for c in (checks.values() if isinstance(checks, dict) else checks):
            if c.get("id") in by_id:
                by_id[c["id"]].update(c)
            else:
                data["checks"].append(dict({"applies": "unknown", "status": "missing", "finding": "", "source": "",
                                            "date": "", "can_kill": False, "affects": []}, **c))
        for key in ("hypotheses", "open"):
            have = {json.dumps(x, sort_keys=True, ensure_ascii=False) for x in data[key]}
            data[key] += [x for x in frag.get(key, []) if json.dumps(x, sort_keys=True, ensure_ascii=False) not in have]
        for key in ("verdict",):
            if frag.get(key):
                data[key] = frag[key]
        if frag.get("product"):
            data["product"].update({k: v for k, v in frag["product"].items() if k in ("description", "traits")})
        notes.append(f"{pathlib.Path(frag_path).name}: {len(frag.get('fields', {}))} שדות, {len(checks)} בדיקות")
    new_err = [e for e in validate(data, draft=True)[0] if e not in before_err]
    if new_err:
        sys.exit("לא נשמר — הקטע יוצר שגיאות חדשות:\n  " + "\n  ".join(new_err))
    data["history"].append(f"{today()} — מוזג: " + "; ".join(notes) + (" | " + ", ".join(changes) if changes else ""))
    save(a.file, data)
    print("עודכן: " + "; ".join(notes))


# ---------- render ----------

def engine_fields(data, mode=None):
    """Engine input from the data file; with mode, the freight mode and its by_mode values instead of the chosen ones."""
    F = {}
    for k, c in CAT.items():
        f = data["fields"][k]
        v, unit, per = f.get("value"), f.get("unit", "ils"), f.get("per", (c.get("pers") or ["unit"])[0])
        if mode and k == "freight_mode":
            v = mode
        elif mode and c.get("mode_dependent") and isinstance((f.get("by_mode") or {}).get(mode), dict):
            e = f["by_mode"][mode]
            v, unit, per = e.get("value"), e.get("unit", unit), e.get("per", per)
        if v is None:
            v = next(iter(c["options"])) if c["kind"] == "choice" else 0
        vd = f.get("vat_domestic")
        F[k] = {"value": v, "unit": unit, "per": per,
                "vat_domestic": c.get("vat_domestic", False) if vd is None else bool(vd)}
    return F


def stamp_by_mode(f):
    """Give every per-mode value the unit and basis it was written in, before the field's own may change."""
    for e in (f.get("by_mode") or {}).values():
        if isinstance(e, dict):
            e.setdefault("unit", f.get("unit"))
            e.setdefault("per", f.get("per"))


def ranges_of(data):
    """The plausible range of each uncertain field — for a mode-dependent field, the chosen mode's (as the page does)."""
    out = {}
    mode = data["fields"]["freight_mode"].get("value")
    for k, c in CAT.items():
        f = data["fields"][k]
        if c["kind"] == "choice" or c.get("compare_only") or c.get("decision") or f["status"] in ("na", "missing"):
            continue
        m = (f.get("by_mode") or {}).get(mode) if c.get("mode_dependent") else None
        m = m if isinstance(m, dict) and "low" in m and "high" in m else f
        out[k] = (m.get("low"), m.get("high"), m.get("unit", f.get("unit", "ils")), m.get("per", f.get("per", (c.get("pers") or ["unit"])[0])))
    return out


def ils(x):
    if x is None or not math.isfinite(x):
        return "—"
    sign = "−" if x < 0 else ""
    x = abs(x)
    return f"{sign}₪{x:,.0f}" if x >= 100 else f"{sign}₪{x:,.1f}"


def pct(x):
    return "—" if x is None or not math.isfinite(x) else f"{'−' if x < 0 else ''}{abs(x) * 100:.1f}%"


def fval(k, v, unit=None, per=None):
    c = CAT[k]
    if v is None:
        return "—"
    if c["kind"] == "rate" or unit == "pct":
        s = f"{v:.1f}%"
    elif c["kind"] == "money":
        s = (f"${v:,.2f}" if abs(v) < 100 else f"${v:,.0f}") if unit == "usd" else ils(v)
    elif c["kind"] == "count":
        s = f"{v:,.0f}"
    else:
        s = f"{v:g} {c.get('suffix', '')}".strip()
    return s + (f" {PER_LABEL[per]}" if per in ("unit", "shipment") and c["kind"] == "money" and unit != "pct" else "")


def summary(data, path):
    F = engine_fields(data)
    r = engine.compute(F)
    h = engine.headline(F, CAT)
    hidden = [k for k, f in data["fields"].items() if f["status"] == "na"]
    th = engine.thresholds(F, CAT, hidden)
    rg = ranges_of(data)
    sens = engine.sensitivity(F, rg)
    worst = engine.compute(engine.scenario(F, rg, True))["profit_first"]
    best = engine.compute(engine.scenario(F, rg, False))["profit_first"]
    fg = data["fields"]["goods"]
    tm = F["target_margin"]["value"]
    lines = [f"נוצר: {path}"]
    gap = [CAT[k]["label"] for k in ("price", "target_margin", "qty") if data["fields"][k].get("status") == "missing"]
    if gap:
        lines.append("⚠ חסר: " + ", ".join(gap) + " — מה שתלוי בהם למטה מחושב מערך זמני (0), לא מסקנה")
    lines.append(f"רווח למכירה, הזמנה ראשונה: {ils(r['profit_first'])} ({pct(r['margin_first'])} מהסכום שהלקוח משלם)"
                 f" · בהזמנה חוזרת: {ils(r['profit_repeat'])}")
    alt = f" · הלקוח מזמין לבד ב-{ils(r['P'] / r['vs_alt'])} (המחיר שלנו פי {r['vs_alt']:.1f})" if r["vs_alt"] else ""
    lines.append(f"מחיר איזון {ils(h['be_price'])} · מחיר לרווח {tm:g}%: {ils(h['target_price'])} (היום {ils(r['P'])}){alt}")
    mgv = lambda v: fval('goods', v, fg.get('unit'), fg.get('per')) if v is not None else "אין — גם בחינם לא מגיעים ליעד"
    lines.append(f"מחיר קנייה מקסימלי לרווח {tm:g}%: {mgv(h['max_goods_repeat'])} בהזמנה חוזרת · "
                 f"{mgv(h['max_goods'])} בהזמנה הראשונה, עם החד-פעמי"
                 f" (היום {fval('goods', fg['value'], fg.get('unit'), fg.get('per'))})")
    be = r["be_units"]
    if not be:
        be_s = "לא חוזר — כל מכירה מפסידה"
    elif be > r["sellable"]:
        be_s = f"לא חוזר בהזמנה הזו: צריך {math.ceil(be):,} מכירות, ויש {r['sellable']:,.0f} יחידות"
    else:
        be_s = f"חוזר אחרי {math.ceil(be):,} מכירות מתוך {r['sellable']:,.0f} ({be / r['sellable'] * 100:.0f}%)"
    months = f" · מכירת ההזמנה: {r['months']:.1f} חודשים" if r["months"] else ""
    lines.append(f"כסף שיוצא לפני המכירה הראשונה: {ils(r['capital'])} · {be_s}{months}")
    if r["over_ceiling"]:
        lines.append(f"⚠ בקצב הזה המחזור השנתי ({ils(r['annual'])}) עובר את תקרת עוסק פטור")
    if sens:
        lines.append("הכי משפיע: " + ", ".join(f"{CAT[s['id']]['label']} ({ils(min(s['at_low'], s['at_high']))} עד "
                                                f"{ils(max(s['at_low'], s['at_high']))})" for s in sens[:4]))
    lim = [t for t in th if t["state"] == "limit"]
    cannot = [CAT[t["id"]]["label"] for t in th if t["state"] == "cannot_fix"]
    if lim:
        lines.append("עד כאן לא מפסידים: " + ", ".join(
            f"{CAT[t['id']]['label']} {'≤' if t['hurts_when'] == 'up' else '≥'} "
            f"{fval(t['id'], t['limit'], F[t['id']]['unit'], F[t['id']]['per'])}"
            f" (היום {fval(t['id'], t['value'], F[t['id']]['unit'], F[t['id']]['per'])})" for t in lim[:4]))
    if cannot:
        more = f" ועוד {len(cannot) - 5}" if len(cannot) > 5 else ""
        lines.append("לא מצילים לבד (גם בערך הכי טוב עדיין הפסד): " + ", ".join(cannot[:5]) + more)
    lines.append(f"תרחיש גרוע (כל ההנחות בקצה הרע): {ils(worst)} למכירה · תרחיש טוב: {ils(best)}")
    modes = CAT["freight_mode"]["options"]
    per_mode = {m: engine.compute(engine_fields(data, m)) for m in modes}
    chosen = F["freight_mode"]["value"]
    best_mode = max(per_mode, key=lambda m: per_mode[m]["profit_first"])
    lines.append("רווח למכירה לפי דרך השילוח: " + " · ".join(
        f"{modes[m]} {ils(per_mode[m]['profit_first'])}{' (נבחר)' if m == chosen else ''}" for m in modes)
        + ("" if best_mode == chosen else f" — ⚠ {modes[best_mode]} משתלם יותר"))
    blocking = [o for o in data.get("open", []) if o.get("blocking")]
    if blocking:
        cut = lambda t, n: t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "…"
        lines.append("פתוח וחוסם: " + "; ".join(f"{o.get('id', '')} {cut(o.get('text', ''), 80)}"
                                                + (f" ({cut(o['who'], 40)})" if o.get("who") else "") for o in blocking))
    unknown = [c["title"] for c in data["checks"] if c.get("can_kill") and c.get("applies") == "unknown"]
    if unknown:
        lines.append("יכול לעצור ועוד לא ידוע: " + "; ".join(unknown))
    est = [k for k, f in data["fields"].items() if f["status"] in ("estimate", "missing") and not CAT[k].get("compare_only")]
    lines.append(f"שדות בלי מקור (הערכה/חסר): {len(est)} מתוך {len(CAT)}")
    return "\n".join(lines)


def page_data(data):
    stamp = hashlib.sha1(json.dumps(data["fields"], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:10]
    traits = {t: TRAITS["traits"][t]["label"] for t in data["product"].get("traits", []) if t in TRAITS["traits"]}
    return {"catalog": CATALOG, "product": data["product"], "traits": traits, "fields": data["fields"],
            "checks": data["checks"], "hypotheses": data.get("hypotheses", []), "open": data.get("open", []),
            "verdict": data.get("verdict", ""), "updated": data.get("updated", today()), "stamp": stamp,
            "areas": TRAITS["areas"], "labels": {"status": FIELD_STATUS, "applies": APPLIES, "per": PER_LABEL}}


def cmd_render(a):
    data = load(a.file)
    err, warn = validate(data, a.draft)
    if err:
        for e in err:
            print("שגיאה: " + e)
        sys.exit("לא נוצר דף — לתקן קודם (או --draft לטיוטה)")
    out = pathlib.Path(a.out) if a.out else pathlib.Path(a.file).with_suffix(".html")
    payload = json.dumps(page_data(data), ensure_ascii=False).replace("<", "\\u003c")
    page = (HERE / "template.html").read_text(encoding="utf-8")
    page = page.replace("/*__ENGINE__*/", (HERE / "engine.js").read_text(encoding="utf-8"))
    page = page.replace("__DATA__", payload).replace("__TITLE__", html.escape(data["product"]["name"]))
    out.write_text(page, encoding="utf-8")
    if a.facts:
        changed = sync_facts(data, a.facts)
        if changed:
            print("עודכנו עובדות משותפות: " + ", ".join(changed))
    if warn:
        print(f"{len(warn)} אזהרות (sim.py check לפירוט)")
    print(summary(data, out))


# ---------- compare offers ----------

def switch_mode(d, mode):
    """Change the freight mode the way the page does: fields that depend on it take that mode's by_mode values."""
    fields = d["fields"]
    old = fields["freight_mode"]["value"]
    if mode == old:
        return
    for k, c in CAT.items():
        bm = fields[k].get("by_mode")
        if c.get("mode_dependent") and isinstance(bm, dict) and isinstance(bm.get(mode), dict):
            f = fields[k]
            keys = ("value", "low", "high", "unit", "per", "status", "source", "date", "checked", "note")
            bm.setdefault(old, {x: f[x] for x in keys if x in f})
            e = bm[mode]
            f.update({x: e[x] for x in ("value", "low", "high", "unit", "per") if x in e})
            if e.get("status"):
                f.update({x: e.get(x, "") for x in ("status", "source", "date", "checked", "note")})
            else:  # a per-mode value without its own source is an estimate, whatever the main value's status
                f.update(status="estimate", source="", date="", note="ערך לדרך השילוח הזו, בלי מקור משלו")
                f.pop("checked", None)
    fields["freight_mode"]["value"] = mode


def with_set(data, sets):
    """The data file with one offer's values. A money value carries its unit and basis — never the file's current one."""
    d = json.loads(json.dumps(data))
    sets = dict(sets or {})
    mode = sets.pop("freight_mode", None)
    if mode is not None:
        switch_mode(d, mode["value"] if isinstance(mode, dict) else mode)
    for k, v in sets.items():
        if k not in CAT:
            sys.exit(f"שדה לא מוכר בהצעה: {k}")
        v = dict(v) if isinstance(v, dict) else {"value": v}
        f = d["fields"][k]
        if "value" in v and CAT[k]["kind"] == "money" and not ("unit" in v and "per" in v):
            sys.exit(f"בהצעה, {k}: ערך כסף בלי unit ו-per — כתוב במה הספק נקב (למשל \"unit\": \"usd\", \"per\": \"unit\")")
        if "value" in v and CAT[k]["kind"] != "choice" and "low" not in v and "high" not in v:
            # an offer is one price, not a range; a decision has no range at all
            v.update(low=None, high=None) if CAT[k].get("decision") else v.update(low=v["value"], high=v["value"])
        if "value" in v and "status" not in v:
            v.update(status="estimate", source="", date="", note="ערך של ההצעה, בלי מקור בקובץ ההצעות")
            f.pop("checked", None)
        f.update(v)
    return d


def csv_safe(x):
    """A text cell a spreadsheet would run as a formula (= + - @ at the start) is written as text."""
    s = "" if x is None else str(x)
    if s[:1] in ("=", "+", "-", "@", "\t", "\r") and not re.fullmatch(r"-?\d+(\.\d+)?", s):
        return "'" + s
    return s


DECIDED = {"verified", "partial"}  # a decision on record ("החלטת מייסד <date>"); page play ("founder") is not one


def buy_ceiling(F, tm, alt):
    """The walk-away supplier price, as the department defines it: the highest supplier price at which
    (1) every repeat order still makes the target margin, and (2) our first order's full cost per unit,
    one-time costs included, stays under the best alternative's landed cost.
    -> (walk, margin cap, alternative cap, reason); walk None with a reason when no price works."""
    glo, ghi = engine.search_range(F, CAT, "goods")
    cap_m = engine.bisect(F, "goods", "margin_repeat", tm, glo, ghi)
    free = engine.compute(engine.with_value(F, "goods", 0))
    if free["margin_repeat"] < tm:
        return None, None, None, "לא משתלם בשום מחיר — גם בחינם לא מגיעים לרווח"
    cap_a = None
    if alt is not None:
        if free["landed_first"] >= alt:
            return None, cap_m, None, "לא קונים — החלופה זולה יותר גם כשהסחורה בחינם"
        cap_a = engine.bisect(F, "goods", "landed_first", alt, 0.0, ghi)
    caps = [v for v in (cap_m, cap_a) if v is not None]
    if not caps:
        return None, None, None, "אין תקרה בטווח הבדיקה — בדוק את הקלטים (רווח, מחיר, כמות)"
    return min(caps), cap_m, cap_a, ""


def cmd_compare(a):
    data = load(a.file)
    offers = json.loads(pathlib.Path(a.offers).read_text(encoding="utf-8"))["offers"]
    def numbers(flag, s):
        out = []
        for v in (s or "").split(","):
            v = v.strip().strip("₪$%").replace("_", "")
            if not v:
                continue
            try:
                x = float(v)
            except ValueError:
                sys.exit(f"{flag}: '{v}' אינו מספר (למשל 149,199)")
            if not math.isfinite(x) or x <= 0:
                sys.exit(f"{flag}: '{v}' חייב להיות מספר חיובי")
            out.append(x)
        return out
    prices, margins = numbers("--prices", a.prices), numbers("--margins", a.margins)
    for flag, v in (("--alt-landed", a.alt_landed), ("--alt-price", a.alt_price)):
        if v is not None and (not math.isfinite(v) or v <= 0):
            sys.exit(f"{flag} חייב להיות מחיר חיובי")
    base = json.loads(json.dumps(data))
    # an undecided price or margin: the table still works, from the values it is given
    for k, vals in (("price", prices), ("target_margin", margins)):
        f = base["fields"][k]
        if vals and (f.get("value") is None or f.get("status") == "missing"):
            f.update(value=vals[0], status="estimate", source="", date="", note="ערך זמני — רק לטבלה, לא החלטה",
                     low=None, high=None)
    rows, failed = [], False
    for o in offers:
        d = with_set(base, o.get("set"))
        err, _ = validate(d)
        if a.decision:
            err += decision_errors(d)
            for k in ("goods", "qty"):
                if d["fields"][k].get("status") not in EVIDENCED:
                    err.append(f"{k} ({CAT[k]['label']}): בהחלטת קנייה — ערך ההצעה עם מקור (status, source, date)")
        if err:
            failed = True
            print(f"הצעה '{o.get('name')}' — לא חושבה:\n  " + "\n  ".join(err))
            continue
        F = engine_fields(d)
        key = lambda s: str(s or "").strip().casefold()
        rows.append({"offer": o, "supplier": key(o.get("supplier") or o.get("name")), "spec": key(o.get("spec")),
                     "decided": all(d["fields"][k].get("status") in DECIDED for k in ("price", "target_margin")),
                     "data": d, "F": F, "r": engine.compute(F), "h": engine.headline(F, CAT)})
    if not rows:
        sys.exit(1)
    if any(not (o.get("supplier")) for o in offers):
        print("⚠ הצעה בלי supplier — השם שלה משמש כספק. מדרגות של אותו ספק צריכות אותו supplier, אחרת הן חלופה זו לזו.\n")
    lic = base["fields"]["dealer"]["value"] == "licensed"
    vat = float(base["fields"]["vat"]["value"] or 0) / 100
    outside = [v for v in (a.alt_landed, (a.alt_price / (1 + vat) if lic else a.alt_price) if a.alt_price else None)
               if v is not None]
    for x in rows:
        # alternatives: other suppliers at the same quantity (their first order, one-time costs in), and outside ones
        alts = [y["r"]["landed_first"] for y in rows if y["supplier"] != x["supplier"] and abs(y["r"]["Q"] - x["r"]["Q"]) < 1e-9
                and y["spec"] == x["spec"]]
        x["alt"] = min(alts + outside) if alts + outside else None
        x["tm"] = x["F"]["target_margin"]["value"] / 100
        x["walk"], x["cap_m"], x["cap_a"], x["why"] = buy_ceiling(x["F"], x["tm"], x["alt"])
        if failed:  # a refused offer might have been the best alternative: no ceiling is final until every offer passes
            x["walk"], x["why"] = None, "לא חושב — הצעה אחרת לא עברה (למעלה); בלעדיה החלופה לא ידועה"
    decided = all(x["decided"] for x in rows)
    g = lambda x, v: fval("goods", v, x["F"]["goods"]["unit"], x["F"]["goods"]["per"]) if v is not None else "—"
    tms = sorted({x["tm"] for x in rows})
    print("רווח = אחוז מהסכום שהלקוח משלם, אחרי פרסום, בהזמנה חוזרת (החד-פעמי — בעמודה נפרדת). יעד "
          + " / ".join(f"{v * 100:g}%" for v in tms)
          + ". החלופה = עלות ההזמנה הראשונה ליחידה (עם החד-פעמי) של ספק אחר באותה כמות"
          + (f", או {' / '.join(ils(v) for v in outside)} מבחוץ" if outside else "")
          + ". מחיר שמעליו לא קונים = הנמוך מבין מקסימום לרווח לבין מקסימום מול החלופה.\n")
    if not decided:
        print("⚠ מחיר המכירה או הרווח המינימלי לא הוחלטו (אין החלטת מייסד רשומה — verified/partial עם מקור) — אין מספר אחד,"
              " ורווח ונקודת איזון תלויים במחיר; הטבלה למטה היא התוצר"
              + ("" if prices or margins else ", וצריך --prices ו/או --margins כדי לקבל אותה") + ".\n")
    print("| הצעה | ספק | כמות | מחיר ספק | עלות נחיתה ליחידה | חד-פעמי ליחידה | הזמנה ראשונה ליחידה | "
          "רווח למכירה (ראשונה / חוזרת) | כסף שנקשר | הכסף חוזר אחרי | מקסימום לרווח (חוזרת / ראשונה) | מקסימום מול החלופה | "
          "**מחיר שמעליו לא קונים** |")
    print("|" + "---|" * 13)
    for x in rows:
        r, ok = x["r"], x["decided"]
        be = (f"{math.ceil(r['be_units']):,} מ-{r['sellable']:,.0f}" if r["be_units"] else "לא חוזר") if ok else "לפי הטבלה"
        walk = (g(x, x["walk"]) if ok else "חסרה החלטה: מחיר מכירה / רווח") if x["walk"] is not None else x["why"]
        profit = f"{ils(r['profit_first'])} / {ils(r['profit_repeat'])}" if ok else "לפי הטבלה"
        caps = f"{g(x, x['cap_m'])} / {g(x, x['h']['max_goods'])}" if ok else "לפי הטבלה"
        print(f"| {x['offer'].get('name')} | {x['offer'].get('supplier') or x['offer'].get('name')} | {r['Q']:,.0f} | "
              f"{g(x, x['F']['goods']['value'])} | {ils(r['landed'])} | {ils(r['onetime_u'])} | {ils(r['landed_first'])} | "
              f"{profit} | {ils(r['capital'])} | {be} | {caps} | {g(x, x['cap_a'])} | **{walk}** |")
    table = []
    if prices or margins:
        for x in rows:
            ps = prices or [x["F"]["price"]["value"]]
            ms = margins or [x["tm"] * 100]
            print(f"\n**{x['offer'].get('name')}** — מחיר שמעליו לא קונים לפי מחיר מכירה (עמודות) ורווח (שורות), כולל החלופה:\n")
            print("| רווח \\ מחיר | " + " | ".join(fval("price", pv, x["F"]["price"]["unit"]) for pv in ps) + " |")
            print("|" + "---|" * (len(ps) + 1))
            for m in ms:
                cells = []
                for pv in ps:
                    G = engine.with_value(engine.with_value(x["F"], "price", pv), "target_margin", m)
                    w, _, _, why = buy_ceiling(G, m / 100, x["alt"])
                    cells.append(g(x, w) if w is not None else "אין")
                    table.append([x["offer"].get("name"), m, pv, round(w, 4) if w is not None else "",
                                  f"{x['F']['goods']['unit']}/{x['F']['goods']['per']}", why])
                print(f"| {m:g}% | " + " | ".join(cells) + " |")
    if a.csv:
        import csv
        with open(a.csv, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["offer", "supplier", "note"] + [f"{k} ({CAT[k]['label']})" for k in CAT] +
                       ["landed_ils", "onetime_per_unit_ils", "first_order_per_unit_ils", "profit_first_ils",
                        "profit_repeat_ils", "margin_repeat", "capital_ils", "be_units", "alternative_ils",
                        "max_goods_margin_repeat", "max_goods_margin_first", "max_goods_alt", "walk_away",
                        "walk_away_note", "goods_unit"])
            for x in rows:
                fl = x["data"]["fields"]
                cell = lambda k: " ".join(str(fl[k].get(y)) for y in ("value", "unit", "per") if fl[k].get(y) is not None)
                r = x["r"]
                ok = x["decided"]
                note = x["why"] or ("" if ok else "חסרה החלטה: מחיר מכירה / רווח — ראו הטבלה")
                walk = round(x["walk"], 4) if x["walk"] is not None and ok else ""
                dep = lambda v, n: (round(v, n) if v is not None else "") if ok else ""
                w.writerow([csv_safe(x["offer"].get("name")), csv_safe(x["offer"].get("supplier") or x["offer"].get("name")),
                            csv_safe(x["offer"].get("note", ""))]
                           + [csv_safe(cell(k)) for k in CAT] +
                           [round(r["landed"], 2), round(r["onetime_u"], 2), round(r["landed_first"], 2),
                            dep(r["profit_first"], 2), dep(r["profit_repeat"], 2), dep(r["margin_repeat"], 4),
                            round(r["capital"], 2), (math.ceil(r["be_units"]) if r["be_units"] else "") if ok else "",
                            round(x["alt"], 2) if x["alt"] is not None else "",
                            dep(x["cap_m"], 4), dep(x["h"]["max_goods"], 4),
                            round(x["cap_a"], 4) if x["cap_a"] is not None else "", walk, csv_safe(note),
                            f"{x['F']['goods']['unit']}/{x['F']['goods']['per']}"])
            if table:
                w.writerow([])
                w.writerow(["table: offer", "margin_pct", "price", "walk_away", "goods_unit", "note"])
                for row in table:
                    w.writerow([csv_safe(row[0])] + row[1:])
        print(f"\nגיליון: {a.csv}")
    sys.exit(1 if failed else 0)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("traits").set_defaults(fn=cmd_traits)
    s = sub.add_parser("init")
    s.add_argument("--name", required=True)
    s.add_argument("--traits", default="")
    s.add_argument("--dir", required=True)
    s.add_argument("--facts")
    s.add_argument("--desc")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_init)
    s = sub.add_parser("check")
    s.add_argument("file")
    s.add_argument("--draft", action="store_true")
    s.add_argument("--decision", action="store_true")
    s.set_defaults(fn=cmd_check)
    s = sub.add_parser("merge")
    s.add_argument("file")
    s.add_argument("fragments", nargs="+")
    s.add_argument("--founder", action="store_true")
    s.add_argument("--any-page", action="store_true", dest="any_page",
                   help="founder values from another product or an older page, on purpose")
    s.set_defaults(fn=cmd_merge)
    s = sub.add_parser("render")
    s.add_argument("file")
    s.add_argument("--out")
    s.add_argument("--facts")
    s.add_argument("--draft", action="store_true")
    s.set_defaults(fn=cmd_render)
    s = sub.add_parser("compare")
    s.add_argument("file")
    s.add_argument("offers")
    s.add_argument("--alt-landed", type=float, dest="alt_landed")
    s.add_argument("--alt-price", type=float, dest="alt_price",
                   help="what an alternative (e.g. a local importer) charges us per unit, in ILS incl. VAT")
    s.add_argument("--prices")
    s.add_argument("--margins")
    s.add_argument("--csv")
    s.add_argument("--decision", action="store_true")
    s.set_defaults(fn=cmd_compare)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
