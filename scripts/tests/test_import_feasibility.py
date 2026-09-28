"""plugins/supply-os/skills/import-feasibility: the calculation, its JS twin, and the CLI.

Run: python3 scripts/tests/test_import_feasibility.py
The JS parity part needs node; without it the suite fails (the page and the CLI must not drift unchecked).
"""
import json
import math
import os
import pathlib
import random
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
SKILL = pathlib.Path(os.environ.get("IMPORT_SIM_DIR", ROOT / "plugins/supply-os/skills/import-feasibility"))
sys.path.insert(0, str(SKILL))
import engine  # noqa: E402
import sim  # noqa: E402

CAT = sim.CAT
failures = []


def check(name, cond):
    if not cond:
        failures.append(name)
    print(("ok   " if cond else "FAIL ") + name)


def close(a, b, tol=1e-6):
    if a is None or b is None:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if math.isinf(a) or math.isinf(b):
        return a == b
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


# A worked case, checked by hand (see the numbers in each assert).
BASE = {
    "fx": 3.7, "dealer": "exempt", "target_margin": 20, "exempt_ceiling": 122833,
    "price": 190, "ship_paid": 0, "sales_pm": 20, "alt_price": 120,
    "qty": 100, "goods": (20, "usd", "unit"), "pay_fee": (3, "pct", "shipment"), "defect": 3,
    "freight_mode": "sea", "unit_kg": 0.9, "unit_l": 3, "air_rate": (8, "usd", "kg"), "vol_factor": 167,
    "sea_rate": (300, "usd", "cbm"), "sea_min_cbm": 1, "freight_fixed": (400, "usd", "shipment"), "freight_extra": (0, "usd", "shipment"),
    "dg_surcharge": (30, "pct", "shipment"), "insurance": (0.5, "pct", "shipment"),
    "duty": 6, "purchase_tax": 0, "vat": 18, "clearance": (600, "usd", "shipment"),
    "std_shipment": (0, "ils", "shipment"), "std_onetime": (5000, "ils", "once"), "compliance_unit": (2, "ils", "unit"),
    "pack": 6, "ship_out": 25, "clearing": (2.5, "pct", "sale"), "clearing_fixed": 1, "platform_fee": (0, "pct", "sale"),
    "cac": 45, "ret_rate": 8, "ret_ship": 25, "ret_loss": 15, "ret_fee": (5, "pct", "sale"), "ret_fee_cap": 100,
    "warranty": (4, "pct", "sale"), "onetime_other": 1500, "monthly": 300,
}


def make_F(spec):
    F = {}
    for k, c in CAT.items():
        v = spec[k]
        if isinstance(v, tuple):
            val, unit, per = v
        else:
            val, unit, per = v, (c.get("units") or ["ils"])[0], (c.get("pers") or ["unit"])[0]
        F[k] = {"value": val, "unit": unit, "per": per, "vat_domestic": c.get("vat_domestic", False)}
    return F


F0 = make_F(BASE)
r = engine.compute(F0)
# goods 20*100*3.7=7400; sea max(0.3,1)*300*3.7=1110, +30% dg=1443; ins .5% of 8843=44.215; pay 3% of 7400=222
# cif 8887.215; duty 6% 533.23; vat 18% of 9420.45=1695.68; clearance 600*3.7=2220; compliance 200
check("import total of the shipment", close(r["import_t"], 7400 + 1443 + 44.215 + 222 + 533.2329 + 1695.6812 + 2220 + 200, 1e-4))
check("landed cost per sellable unit (97 of 100)", close(r["landed"], r["import_t"] / 97))
k = 0.08 / 0.92
ret = k * (31 + (0.025 * 190 + 1) + 45 + 25 + 0.15 * r["landed"] - 0.05 * 190)
contrib = 190 - 31 - (0.025 * 190 + 1) - 45 - ret - 0.04 * r["landed"] - 300 / 20
check("returns spread over the sales that stay", close(r["returns"], ret))
check("repeat-order profit", close(r["profit_repeat"], contrib - r["landed"]))
check("first-order profit carries the one-time costs", close(r["profit_first"], contrib - r["landed"] - 6500 / 97))
check("cash out before the first sale", close(r["capital"], r["import_t"] + 6500))
check("sales to get the money back", close(r["be_units"], (r["import_t"] + 6500) / contrib))

h = engine.headline(F0, CAT)
check("break-even price zeroes the first-order profit", abs(engine.compute(engine.with_value(F0, "price", h["be_price"]))["profit_first"]) < 1e-6)
check("target price meets the target margin", close(engine.compute(engine.with_value(F0, "price", h["target_price"]))["margin_first"], 0.2, 1e-6))
cheap = engine.with_value(F0, "price", 400)
mg = engine.headline(cheap, CAT)["max_goods"]
check("walk-away buy price meets the target margin", mg is not None and close(engine.compute(engine.with_value(cheap, "goods", mg))["margin_first"], 0.2, 1e-6))
check("no walk-away price when even free goods miss the target", h["max_goods"] is None)

# licensed dealer: sale net of VAT, import VAT reclaimed but still cash out, domestic VAT-bearing costs net
lic = dict(F0, dealer=dict(F0["dealer"], value="licensed"))
for kk in ("pack", "ship_out", "clearance"):
    lic[kk] = dict(lic[kk], vat_domestic=True)
rl = engine.compute(lic)
check("licensed: revenue without VAT", close(rl["rev"], 190 / 1.18))
check("licensed: import VAT is not a cost", rl["ivat_cost"] == 0 and close(rl["capital"], rl["cash_out"] + rl["ivat"]))
check("licensed: VAT-bearing domestic cost counted net", close(rl["clear"], 600 * 3.7 / 1.18))

# converting a field between $, ₪, % and per-unit/per-shipment must not move any result
moved = []
for kk, c in CAT.items():
    if c["kind"] != "money":
        continue
    for u in c["units"]:
        for p in c["pers"]:
            fac = engine.conv_factor(r, 3.7, c, F0[kk]["unit"], F0[kk]["per"], u, p)
            if fac is None:
                continue
            G = dict(F0, **{kk: dict(F0[kk], value=F0[kk]["value"] * fac, unit=u, per=p)})
            if not close(engine.compute(G)["profit_first"], r["profit_first"], 1e-9):
                moved.append(f"{kk}->{u}/{p}")
check("unit and basis conversion keeps the result" + (f" (moved: {moved})" if moved else ""), not moved)

ex = engine.compute(dict(F0, freight_extra=dict(F0["freight_extra"], value=50)))
check("freight extras are part of CIF, not of the DG surcharge base",
      close(ex["freight_t"], r["freight_t"] + 50 * 3.7) and close(ex["dg"], r["dg"]))

# air: chargeable weight is the higher of real and volumetric
air = dict(F0, freight_mode=dict(F0["freight_mode"], value="air"), dg_surcharge=dict(F0["dg_surcharge"], value=0))
check("air freight by chargeable weight", close(engine.compute(air)["freight_t"], max(0.9, 3 / 1000 * 167) * 100 * 8 * 3.7))

th = engine.thresholds(engine.with_value(F0, "price", 400), CAT)
t_cac = next(t for t in th if t["id"] == "cac")
check("threshold: profit is zero at the limit", t_cac["state"] == "limit" and
      abs(engine.compute(engine.with_value(engine.with_value(F0, "price", 400), "cac", t_cac["limit"]))["profit_first"]) < 1e-6)
check("threshold: a lever that cannot save the product says so",
      any(t["state"] == "cannot_fix" for t in engine.thresholds(F0, CAT)))

# ---------- the same code in JS ----------
rng = random.Random(7)


def random_F():
    F = {}
    for kk, c in CAT.items():
        if c["kind"] == "choice":
            F[kk] = {"value": rng.choice(list(c["options"])), "unit": "ils", "per": "unit"}
            continue
        unit = rng.choice(c.get("units") or ["ils"])
        per = rng.choice(c.get("pers") or ["unit"])
        hi = c.get("max", 100)
        v = round(rng.uniform(0, hi), 3) if rng.random() > 0.1 else 0
        F[kk] = {"value": v, "unit": unit, "per": per, "vat_domestic": rng.random() < 0.5}
    F["qty"]["value"] = rng.randint(1, 800)
    F["fx"]["value"] = round(rng.uniform(2.5, 4.5), 3)
    F["price"]["value"] = rng.uniform(20, 900)
    F["ret_rate"]["value"] = rng.uniform(0, 30)
    return F


cases = [random_F() for _ in range(150)]
ranges = {kk: (0, CAT[kk].get("max", 100) / 2, cases[0][kk]["unit"], cases[0][kk]["per"])
          for kk in ("price", "cac", "goods", "ret_rate", "qty", "sea_rate", "insurance")}
node = shutil.which("node")
if not node:
    check("JS parity needs node — install it; the page and the CLI must not drift unchecked", False)
else:
    js = """
const E = require(process.argv[1]); const inp = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = inp.cases.map((F) => { const r = E.compute(F);
  return { r, h: E.headline(F, inp.cat), t: E.thresholds(F, inp.cat, []), s: E.sensitivity(F, inp.ranges),
    w: E.compute(E.scenario(F, inp.ranges, true)).profit_first,
    c: E.convFactor(r, F.fx.value, inp.cat.insurance, F.insurance.unit, F.insurance.per, 'usd', 'unit') }; });
process.stdout.write(JSON.stringify(out));"""
    p = subprocess.run([node, "-e", js, str(SKILL / "engine.js")], input=json.dumps({"cases": cases, "cat": CAT, "ranges": ranges}),
                       capture_output=True, text=True)
    if p.returncode:
        check("JS engine runs: " + p.stderr[-300:], False)
    else:
        got = json.loads(p.stdout)
        bad = []
        for i, (F, g) in enumerate(zip(cases, got)):
            r_py = engine.compute(F)
            for kk, v in r_py.items():
                if kk == "bases":
                    if any(not close(v[b], g["r"]["bases"][b], 1e-9) for b in v):
                        bad.append(f"{i}:bases")
                elif isinstance(v, float) and math.isinf(v):
                    if g["r"][kk] is not None:  # JSON has no Infinity; JS writes null
                        bad.append(f"{i}:{kk}")
                elif not close(v, g["r"][kk], 1e-9):
                    bad.append(f"{i}:{kk}")
            for kk, v in engine.headline(F, CAT).items():
                if not close(v, g["h"][kk], 1e-7):
                    bad.append(f"{i}:headline.{kk}")
            t_py = engine.thresholds(F, CAT)
            if [(t["id"], t["state"]) for t in t_py] != [(t["id"], t["state"]) for t in g["t"]] or \
                    any(not close(a["limit"], b["limit"], 1e-7) for a, b in zip(t_py, g["t"])):
                bad.append(f"{i}:thresholds")
            s_py = engine.sensitivity(F, ranges)
            if [s["id"] for s in s_py] != [s["id"] for s in g["s"]]:
                bad.append(f"{i}:sensitivity")
            if not close(engine.compute(engine.scenario(F, ranges, True))["profit_first"], g["w"], 1e-9):
                bad.append(f"{i}:scenario")
            c_py = engine.conv_factor(r_py, F["fx"]["value"], CAT["insurance"], F["insurance"]["unit"], F["insurance"]["per"], "usd", "unit")
            if not close(c_py, g["c"], 1e-9):
                bad.append(f"{i}:conv")
        check(f"JS engine equals the Python engine on {len(cases)} random inputs" + (f" ({bad[:6]})" if bad else ""), not bad)

# ---------- catalogs ----------
T = sim.TRAITS
ids = [c["id"] for c in T["base"]] + [c["id"] for t in T["traits"].values() for c in t["checks"]]
check("check ids are unique", len(ids) == len(set(ids)))
check("checks point at real fields", all(a in CAT for c in T["base"] + [c for t in T["traits"].values() for c in t["checks"]] for a in c["affects"]))
check("checks use known areas", all(c["area"] in T["areas"] for c in T["base"] + [c for t in T["traits"].values() for c in t["checks"]]))
check("na_unless names real fields and traits", all(k in CAT and all(t in T["traits"] for t in v) for k, v in T["na_unless"].items()))
check("every field says who fills it", all(c.get("owner") in ("main", "checks", "costs") for c in CAT.values()))
check("check scope is product or unset", all(c.get("scope") in (None, "product") for c in T["base"] + [c for t in T["traits"].values() for c in t["checks"]]))
check("every field is in a group", all(c["group"] in {g["id"] for g in sim.CATALOG["groups"]} for c in CAT.values()))
words = lambda f: set(re.findall(r"[a-z_]+", (SKILL / f).read_text())) & set(CAT)
check("engine.py and engine.js read the same fields" + f" ({words('engine.py') ^ words('engine.js')})",
      words("engine.py") == words("engine.js"))

# ---------- the CLI ----------
tmp = pathlib.Path(tempfile.mkdtemp())
run = lambda *a: subprocess.run([sys.executable, str(SKILL / "sim.py"), *a], capture_output=True, text=True)
facts = tmp / "facts.json"
p = run("init", "--name", "בדיקה/קופסה", "--traits", "lithium_builtin,electronic", "--dir", str(tmp), "--facts", str(facts))
jpath = tmp / "סימולציית כדאיות - בדיקה-קופסה.json"
check("init writes the data file (bad file-name characters replaced)", p.returncode == 0 and jpath.exists())
data = json.loads(jpath.read_text(encoding="utf-8"))
check("init adds the trait checks", {"li_standard", "weee"} <= {c["id"] for c in data["checks"]})
check("init marks fields not relevant to the traits", data["fields"]["dg_surcharge"]["status"] == "missing")
p0 = run("init", "--name", "עץ", "--traits", "wood_processed", "--dir", str(tmp))
check("no battery: the hazardous-goods surcharge is not relevant",
      json.loads((tmp / "סימולציית כדאיות - עץ.json").read_text(encoding="utf-8"))["fields"]["dg_surcharge"]["status"] == "na")
check("init refuses an unknown trait", run("init", "--name", "x", "--traits", "nope", "--dir", str(tmp)).returncode != 0)
check("check fails while fields are missing", run("check", str(jpath)).returncode == 1)

frag = {"verdict": "בדיקה", "fields": {}, "hypotheses": [{"text": "h", "test": "t", "cost": "חינם"}],
        "open": [{"id": "1", "check": c, "text": "t", "who": "המייסד", "blocking": True} for c in ("import_regime", "brand_ip", "direct_competition", "li_standard", "li_docs")],
        "checks": [{"id": c["id"], "applies": "unknown", "finding": "f", "status": "estimate"} for c in data["checks"]]}
for kk, c in CAT.items():
    spec = BASE[kk]
    val, unit, per = spec if isinstance(spec, tuple) else (spec, (c.get("units") or ["ils"])[0], (c.get("pers") or ["unit"])[0])
    f = {"value": val, "status": "estimate", "note": "fixture </script><b>x</b>"}
    if c["kind"] != "choice":
        f.update(low=val * 0.8 if val else 0, high=val * 1.2 if val else 1)
    if c["kind"] == "money":
        f.update(unit=unit, per=per)
    frag["fields"][kk] = f
frag["fields"]["fx"].update(status="verified", source="בנק ישראל", date=sim.today())
frag["fields"]["vat"].update(status="verified", source="רשות המסים", date=sim.today())
(tmp / "frag.json").write_text(json.dumps(frag, ensure_ascii=False), encoding="utf-8")
check("merge a research fragment", run("merge", str(jpath), str(tmp / "frag.json")).returncode == 0)
p = run("check", str(jpath))
check("check passes on a complete file: " + p.stdout[-200:], p.returncode == 0)
nod = json.loads(jpath.read_text(encoding="utf-8"))
for kk in ("price", "qty", "ship_paid", "target_margin"):
    nod["fields"][kk].update(low=None, high=None)
check("decisions need no range", not sim.validate(nod)[0])
less = json.loads(jpath.read_text(encoding="utf-8"))
del less["fields"]["freight_extra"]
(tmp / "old.json").write_text(json.dumps(less, ensure_ascii=False), encoding="utf-8")
check("a file from before a new field loads, and the new field is missing", sim.load(tmp / "old.json")["fields"]["freight_extra"]["status"] == "missing")
p = run("render", str(jpath), "--facts", str(facts))
html_path = jpath.with_suffix(".html")
check("render writes the page and the summary", p.returncode == 0 and html_path.exists() and "רווח למכירה" in p.stdout)
check("summary compares the freight modes and names the blocking open rows", "לפי דרך השילוח" in p.stdout and "פתוח וחוסם" in p.stdout)
page = html_path.read_text(encoding="utf-8")
check("page has no leftover placeholders", "__DATA__" not in page and "/*__ENGINE__*/" not in page and "__TITLE__" not in page)
blob = page.split('<script id="sim-data" type="application/json">')[1].split("</script>")[0]
check("data in the page parses, and a note cannot close the script tag", json.loads(blob)["fields"]["fx"]["value"] == 3.7 and "</script><b>" not in blob)
check("render saves sourced facts for the next product", json.loads(facts.read_text(encoding="utf-8"))["facts"]["fx"]["value"] == 3.7)
p = run("init", "--name", "שני", "--dir", str(tmp), "--facts", str(facts))
d2 = json.loads((tmp / "סימולציית כדאיות - שני.json").read_text(encoding="utf-8"))
check("init groups what is missing by who fills it", "[אתה" in p.stdout and "[מחקר המספרים]" in p.stdout)
check("the next product reuses fresh facts", d2["fields"]["fx"]["value"] == 3.7 and "fx" in p.stdout)
fj = json.loads(facts.read_text(encoding="utf-8"))
fj["facts"]["vat"]["date"] = "2020-01-01"
facts.write_text(json.dumps(fj), encoding="utf-8")
p = run("init", "--name", "שלישי", "--dir", str(tmp), "--facts", str(facts))
check("a stale fact is flagged for re-checking", "ישנות" in p.stdout and "vat" in p.stdout.split("ישנות")[1])

# a second product in its own folder next to this one reuses the general checks, not the product ones
prod = tmp / "מוצרים"
(prod / "א").mkdir(parents=True)
src = json.loads(jpath.read_text(encoding="utf-8"))
for c in src["checks"]:
    c.update(status="verified", source="משפטי/7", date=sim.today(), applies="yes", finding="נמצא")
src["open"] = [{"id": "7.1", "check": "li_standard", "text": "אישור דגם", "who": "המייסד", "blocking": True}]
(prod / "א" / "סימולציית כדאיות - א.json").write_text(json.dumps(src, ensure_ascii=False), encoding="utf-8")
p = run("init", "--name", "ב", "--traits", "lithium_builtin", "--dir", str(prod / "ב"))
db = json.loads((prod / "ב" / "סימולציית כדאיות - ב.json").read_text(encoding="utf-8"))
byid = {c["id"]: c for c in db["checks"]}
check("reuse: a general check comes from the earlier product", byid["li_standard"]["finding"] == "נמצא" and byid["li_standard"]["reused_from"] == "א")
check("reuse: a product check is researched again", byid["direct_competition"]["status"] == "missing")
check("reuse: the open row of a reused check comes along", [o["id"] for o in db["open"]] == ["7.1"])
for name in ("א", "ב"):  # ב copied its answers from א with their original date, so both have to age
    f = prod / name / f"סימולציית כדאיות - {name}.json"
    old_src = json.loads(f.read_text(encoding="utf-8"))
    for c in old_src["checks"]:
        c["date"] = "2020-01-01"
    f.write_text(json.dumps(old_src, ensure_ascii=False), encoding="utf-8")
run("init", "--name", "ג", "--traits", "lithium_builtin", "--dir", str(prod / "ג"))
dc = json.loads((prod / "ג" / "סימולציית כדאיות - ג.json").read_text(encoding="utf-8"))
check("reuse: an old answer is not reused", all(c["status"] == "missing" for c in dc["checks"]))

rg = sim.ranges_of(json.loads(jpath.read_text(encoding="utf-8")))
check("decisions are not uncertainties: no range for price, qty, ship_paid", not {"price", "qty", "ship_paid"} & set(rg))

cur = sim.load(jpath)
page_ref = {"product": cur["product"]["id"], "stamp": sim.page_data(cur)["stamp"]}
vals = dict(page_ref, values={"goods": {"value": 74, "unit": "ils", "per": "unit"}, "cac": {"value": 60, "unit": "ils", "per": "sale"}})
(tmp / "vals.json").write_text(json.dumps(vals), encoding="utf-8")
(tmp / "vals-other.json").write_text(json.dumps(dict(vals, product="x")), encoding="utf-8")
check("founder values from another product's page are refused", run("merge", str(jpath), str(tmp / "vals-other.json")).returncode != 0)
(tmp / "vals-old.json").write_text(json.dumps(dict(vals, stamp="0000000000")), encoding="utf-8")
check("founder values from an older page are refused", run("merge", str(jpath), str(tmp / "vals-old.json")).returncode != 0)
check("merge the founder's values from the page", run("merge", str(jpath), str(tmp / "vals.json")).returncode == 0)
d3 = json.loads(jpath.read_text(encoding="utf-8"))
g = d3["fields"]["goods"]
check("founder value: status, and the range converted to the new unit",
      g["status"] == "founder" and g["unit"] == "ils" and close(g["low"], 16 * 3.7) and close(g["high"], 24 * 3.7))
check("founder value outside the range widens it", d3["fields"]["cac"]["high"] == 60)
check("the history keeps what the value was", "goods: 20→74" in d3["history"][-1])
check("check still passes after the founder's values", run("check", str(jpath)).returncode == 0)
fd = json.loads(jpath.read_text(encoding="utf-8"))
fd["fields"]["fx"].update(status="founder", source="המייסד, בסימולציה", date=sim.today())
check("decision: the founder's page value is not a source for the exchange rate", any(e.startswith("fx") for e in sim.decision_errors(fd)))
(tmp / "facts-f.json").write_text(json.dumps({"facts": {}}), encoding="utf-8")
check("the founder's page value never becomes a shared fact", "fx" not in sim.sync_facts(fd, tmp / "facts-f.json"))
bogus = json.loads(jpath.read_text(encoding="utf-8"))
bogus["fields"]["fx"]["date"] = "2099-01-01"
check("a date in the future is an error", any("בעתיד" in e for e in sim.validate(bogus)[0]))
bogus = json.loads(jpath.read_text(encoding="utf-8"))
bogus["fields"]["monthly"].update(value=1500, status="estimate", note="n", low=1000, high=2000)
bogus["fields"]["sales_pm"].update(value=0, low=0, high=0)
check("fixed monthly costs with no sales pace are an error, not free", any(e.startswith("sales_pm") for e in sim.validate(bogus)[0]))
(tmp / "badfrag.json").write_text(json.dumps({"fields": {"goods": 20}}), encoding="utf-8")
check("a malformed fragment is refused before it is saved", run("merge", str(jpath), str(tmp / "badfrag.json")).returncode != 0
      and sim.load(jpath)["fields"]["goods"]["value"] == 74)

# ---------- compare offers (the department's calculation) ----------
cmp = json.loads(jpath.read_text(encoding="utf-8"))
cmp["fields"]["price"].update(value=600, low=None, high=None, status="verified", source="החלטת מייסד", date=sim.today())
cmp["fields"]["target_margin"].update(status="verified", source="החלטת מייסד", date=sim.today())
cmp["fields"]["clearance"]["by_mode"] = {"air": {"value": 300, "low": 200, "high": 400}}
cmp["fields"]["onetime_other"].update(value=3000, low=3000, high=3000, unit="ils", per="once", status="estimate", note="n")
(tmp / "cmp.json").write_text(json.dumps(cmp, ensure_ascii=False), encoding="utf-8")
usd = lambda v: {"value": v, "unit": "usd", "per": "unit"}
offers = {"offers": [
    {"name": "A100", "supplier": "A", "set": {"qty": {"value": 100}, "goods": usd(20)}},
    {"name": "A300", "supplier": "A", "set": {"qty": {"value": 300}, "goods": usd(17)}},
    {"name": "B100", "supplier": "B", "set": {"qty": {"value": 100}, "goods": usd(18)}},
    {"name": "air", "supplier": "C", "set": {"freight_mode": "air", "goods": usd(20)}},
]}
(tmp / "offers.json").write_text(json.dumps(offers, ensure_ascii=False), encoding="utf-8")
p = run("compare", str(tmp / "cmp.json"), str(tmp / "offers.json"), "--prices", "500,600",
        "--margins", "15,20", "--csv", str(tmp / "sheet.csv"))
check("compare runs every offer and writes the sheet: " + (p.stdout + p.stderr)[:300], p.returncode == 0 and "A300" in p.stdout and (tmp / "sheet.csv").exists())
import csv as _csv
lines_ = open(tmp / "sheet.csv", encoding="utf-8-sig").read().split("\n\n")[0].splitlines() if (tmp / "sheet.csv").exists() else []
sheet = list(_csv.DictReader(lines_))
cd = sim.load(tmp / "cmp.json")
row = {r["offer"]: r for r in sheet}
F300 = sim.engine_fields(sim.with_set(cd, offers["offers"][1]["set"]))
check("an offer's price is read in the offer's own currency (the file's goods is in ₪)", close(F300["goods"]["value"], 17) and F300["goods"]["unit"] == "usd")
check("the sheet holds the same numbers as the engine", sheet and close(float(row["A300"]["landed_ils"]), engine.compute(F300)["landed"], 1e-3))
check("tiers of one supplier are not each other's alternative", sheet and row["A300"]["alternative_ils"] == "")
spec_offers = {"offers": [{"name": "metal", "supplier": "A", "spec": "metal", "set": {"goods": usd(15)}},
                          {"name": "wood", "supplier": "B", "spec": "wood", "set": {"goods": usd(17)}}]}
(tmp / "spec.json").write_text(json.dumps(spec_offers), encoding="utf-8")
run("compare", str(tmp / "cmp.json"), str(tmp / "spec.json"), "--csv", str(tmp / "spec.csv"))
spec_rows = list(_csv.DictReader(open(tmp / "spec.csv", encoding="utf-8-sig").read().split("\n\n")[0].splitlines())) if (tmp / "spec.csv").exists() else []
check("a different product is not an alternative", spec_rows and all(r["alternative_ils"] == "" for r in spec_rows))
F100 = sim.engine_fields(sim.with_set(cd, offers["offers"][0]["set"]))
FB = sim.engine_fields(sim.with_set(cd, offers["offers"][2]["set"]))
check("the alternative is another supplier at the same quantity, first order with its one-time costs",
      sheet and close(float(row["A100"]["alternative_ils"]), engine.compute(FB)["landed_first"], 1e-3))
tm = F100["target_margin"]["value"] / 100
walk = float(row["A100"]["walk_away"]) if sheet and row["A100"]["walk_away"] else None
alt = engine.compute(FB)["landed_first"]  # exact; the sheet rounds to agorot
at = engine.compute(engine.with_value(F100, "goods", walk)) if walk is not None else None
check("walk-away: every repeat order still makes the target margin", at and at["margin_repeat"] >= tm - 1e-6)
check("walk-away: our first order per unit, one-time costs in, is not above the alternative" + (f" ({at['landed_first']:.6f} vs {alt:.6f})" if at else ""), at and at["landed_first"] <= alt + 1e-3)  # walk is rounded to 4 decimals in the sheet
check("walk-away: one of the two limits binds", at and (close(at["margin_repeat"], tm, 1e-5) or close(at["landed_first"], alt, 1e-3)))
w0, _, _, why = sim.buy_ceiling(F100, tm, 1.0)
check("an alternative cheaper even when our goods are free: don't buy, not the margin cap", w0 is None and "החלופה" in why)
lose = sim.engine_fields(sim.with_set(cd, {"goods": usd(20), "price": {"value": 50, "unit": "ils", "per": "sale"}}))
check("no walk-away price when the target cannot be met at any buy price", sim.buy_ceiling(lose, tm, None)[0] is None)
tbl = open(tmp / "sheet.csv", encoding="utf-8-sig").read() if (tmp / "sheet.csv").exists() else ""
check("the price x margin table is in the sheet", "table: offer" in tbl)
Gt = engine.with_value(engine.with_value(F100, "price", 600), "target_margin", 15)
cell = sim.buy_ceiling(Gt, 0.15, alt)[0]
check("a table cell respects the alternative too", cell is not None and engine.compute(engine.with_value(Gt, "goods", cell))["landed_first"] <= alt + 1e-6)
check("an offer that changes the freight mode takes that mode's values", sim.with_set(cd, {"freight_mode": "air"})["fields"]["clearance"]["value"] == 300)
check("a per-mode value without its own source is an estimate", sim.with_set(cd, {"freight_mode": "air"})["fields"]["clearance"]["status"] == "estimate")
check("switching back restores the chosen mode's value", sim.with_set(sim.with_set(cd, {"freight_mode": "air"}), {"freight_mode": "sea"})["fields"]["clearance"]["value"] == cd["fields"]["clearance"]["value"])
(tmp / "nounit.json").write_text(json.dumps({"offers": [{"name": "x", "set": {"goods": {"value": 15}}}]}), encoding="utf-8")
check("an offer's money value without unit and basis is refused", run("compare", str(tmp / "cmp.json"), str(tmp / "nounit.json")).returncode != 0)
und = json.loads(json.dumps(cmp)); und["fields"]["price"].update(status="estimate", note="הצעה")
(tmp / "und.json").write_text(json.dumps(und, ensure_ascii=False), encoding="utf-8")
p = run("compare", str(tmp / "und.json"), str(tmp / "offers.json"), "--csv", str(tmp / "und.csv"))
und_rows = list(_csv.DictReader(open(tmp / "und.csv", encoding="utf-8-sig").read().split("\n\n")[0].splitlines())) if (tmp / "und.csv").exists() else []
check("an undecided sale price gives no single walk-away number", "חסרה החלטה" in p.stdout and und_rows and all(r["walk_away"] == "" for r in und_rows))
mis = json.loads(json.dumps(cmp)); mis["fields"]["price"].update(value=None, status="missing")
(tmp / "mis.json").write_text(json.dumps(mis, ensure_ascii=False), encoding="utf-8")
p = run("compare", str(tmp / "mis.json"), str(tmp / "offers.json"), "--prices", "400,500")
check("no sale price at all: --prices still gives the table", p.returncode == 0 and "לפי מחיר מכירה" in p.stdout)
p = run("compare", str(tmp / "cmp.json"), str(tmp / "offers.json"), "--alt-price", "300")
check("an outside alternative's price goes through the engine's VAT rule", p.returncode == 0 and "300" in p.stdout)
check("--alt-landed 0 is refused, not ignored", run("compare", str(tmp / "cmp.json"), str(tmp / "offers.json"), "--alt-landed", "0").returncode != 0)
check("a spreadsheet formula in supplier text is written as text",
      sim.csv_safe("=HYPERLINK(1)") == "'=HYPERLINK(1)" and sim.csv_safe("-12.5") == "-12.5" and sim.csv_safe("\t=1").startswith("'"))
play = json.loads(json.dumps(cmp)); play["fields"]["price"].update(status="founder", source="המייסד, בסימולציה")
(tmp / "play.json").write_text(json.dumps(play, ensure_ascii=False), encoding="utf-8")
p = run("compare", str(tmp / "play.json"), str(tmp / "offers.json"))
check("a price the founder only played with on the page is not a decision", "חסרה החלטה" in p.stdout)
(tmp / "ownm.json").write_text(json.dumps({"offers": [{"name": "m", "supplier": "A", "set": {"goods": usd(15), "target_margin": {"value": 10}}}]}), encoding="utf-8")
p = run("compare", str(tmp / "cmp.json"), str(tmp / "ownm.json"))
check("an offer with its own unsourced margin gets no single number", "חסרה החלטה" in p.stdout)
p = run("compare", str(tmp / "und.json"), str(tmp / "offers.json"), "--prices", "400,500", "--csv", str(tmp / "und2.csv"))
u2 = list(_csv.DictReader(open(tmp / "und2.csv", encoding="utf-8-sig").read().split("\n\n")[0].splitlines())) if (tmp / "und2.csv").exists() else []
check("undecided price: no price-dependent number in the sheet's offer rows",
      u2 and all(r["max_goods_margin_repeat"] == "" and r["profit_first_ils"] == "" and r["be_units"] == "" for r in u2))
two = {"offers": [{"name": "A", "supplier": "A", "set": {"goods": dict(usd(15), status="verified", source="quote A", date=sim.today())}},
                  {"name": "B", "supplier": "B", "set": {"goods": usd(12)}}]}
(tmp / "two.json").write_text(json.dumps(two), encoding="utf-8")
dcmp = json.loads(json.dumps(cmp))
for kk in ("fx", "sea_rate", "dg_surcharge", "freight_extra"):
    dcmp["fields"][kk].update(status="verified", source="x", date=sim.today(), checked=sim.today())
for kk in ("clearance",):
    dcmp["fields"][kk].update(source="מחירון")
dcmp["fields"]["qty"].update(status="verified", source="החלטת מייסד", date=sim.today())
(tmp / "dcmp.json").write_text(json.dumps(dcmp, ensure_ascii=False), encoding="utf-8")
p = run("compare", "--decision", str(tmp / "dcmp.json"), str(tmp / "two.json"))
check("a refused offer stops every ceiling, it doesn't silently leave the comparison",
      p.returncode != 0 and "לא חושבה" in p.stdout and "הצעה אחרת לא עברה" in p.stdout)
same = {"offers": [{"name": "A", "supplier": "A", "set": {"goods": dict(dcmp["fields"]["goods"], status=None) if False else {"value": dcmp["fields"]["goods"]["value"], "unit": dcmp["fields"]["goods"]["unit"], "per": dcmp["fields"]["goods"]["per"]}}}]}
(tmp / "same.json").write_text(json.dumps(same), encoding="utf-8")
p = run("compare", "--decision", str(tmp / "dcmp.json"), str(tmp / "same.json"))
check("an unsourced offer equal to the file's number does not borrow the file's source", p.returncode != 0 and "goods" in p.stdout)

dec = json.loads(jpath.read_text(encoding="utf-8"))
err = sim.decision_errors(dec)
check("decision: a freight price without a checked source is refused", any(e.startswith("sea_rate") for e in err))
dec["fields"]["fx"]["date"] = "2026-01-01"
dec["fields"]["clearance"].update(source="משפטי 7")
err = sim.decision_errors(dec)
check("decision: a stale rate and a market number taken from a ruling are refused",
      any(e.startswith("fx") for e in err) and any(e.startswith("clearance") for e in err))
dec["fields"]["fx"].update(date="2026-01-01", checked=sim.today())
check("decision: re-checked today, but the source itself is old — refused", any(e.startswith("fx") and "ישן" in e for e in sim.decision_errors(dec)))
check("check --decision exits 1 on such a file", run("check", "--decision", str(jpath)).returncode == 1)
(tmp / "dec-offers.json").write_text(json.dumps({"offers": [{"name": "A", "set": {"goods": usd(15)}}]}), encoding="utf-8")
p = run("compare", "--decision", str(tmp / "cmp.json"), str(tmp / "dec-offers.json"))
check("decision: an offer's price without a source is refused", p.returncode != 0 and "goods" in p.stdout)

bad = json.loads(jpath.read_text(encoding="utf-8"))
bad["fields"]["price"].update(low=500, high=600)
bad["fields"]["fx"].update(source="")
bad["fields"]["cac"].update(status="estimate", note="")
bad["fields"]["goods"]["unit"] = "pct"
bad["fields"]["duty"]["by_mode"] = {"air": {"value": 1}}
bad["fields"]["clearance"]["by_mode"] = {"sea": {"value": 1}, "ship": {"value": 2}}
err, _ = sim.validate(bad)
check("check: per-mode values only on fields that depend on the freight mode", any(e.startswith("duty: by_mode") for e in err))
check("check: per-mode value of the chosen mode must equal the main value", any("by_mode.sea" in e for e in err))
check("check: unknown freight mode in by_mode", any("by_mode.ship" in e for e in err))
check("check catches a range without the value", any("price" in e for e in err))
check("check catches a verified value without a source", any(e.startswith("fx") for e in err))
check("check catches an estimate without a reason", any(e.startswith("cac") for e in err))
check("check catches a unit the field does not allow", any(e.startswith("goods") for e in err))
bad2 = json.loads(jpath.read_text(encoding="utf-8"))
bad2["fields"]["clearance"]["by_mode"] = {"air": 300}
check("check: a malformed by_mode is an error, not a crash", any("by_mode" in e for e in sim.validate(bad2)[0]))
(tmp / "bad2.json").write_text(json.dumps(bad2, ensure_ascii=False), encoding="utf-8")
(tmp / "tomode.json").write_text(json.dumps({"offers": [{"name": "x", "supplier": "A", "set": {"freight_mode": "air"}}]}), encoding="utf-8")
p = run("compare", str(tmp / "bad2.json"), str(tmp / "tomode.json"))
check("compare on a malformed by_mode reports it, no traceback", "Traceback" not in p.stderr and p.returncode != 0)
bm = json.loads(jpath.read_text(encoding="utf-8"))
bm["fields"]["freight_extra"].update(value=100, low=80, high=120, unit="usd", per="shipment", status="estimate", note="n",
                                     by_mode={"sea": {"value": 100, "low": 80, "high": 120}, "air": {"value": 275, "low": 200, "high": 300}})
(tmp / "bm.json").write_text(json.dumps(bm, ensure_ascii=False), encoding="utf-8")
(tmp / "tounit.json").write_text(json.dumps({"fields": {"freight_extra": {"value": 370, "low": 296, "high": 444, "unit": "ils", "per": "shipment",
    "by_mode": {"sea": {"value": 370, "low": 296, "high": 444}}}}}), encoding="utf-8")
p = run("merge", str(tmp / "bm.json"), str(tmp / "tounit.json"))
air = sim.load(tmp / "bm.json")["fields"]["freight_extra"]["by_mode"]["air"]
check("a research merge that changes a field's unit keeps each mode's value in its own unit" + (": " + p.stdout + p.stderr if p.returncode else ""),
      air.get("unit") == "usd" and air["value"] == 275)
Fair = sim.engine_fields(sim.load(tmp / "bm.json"), "air")
check("the air value is read as dollars", Fair["freight_extra"]["unit"] == "usd" and Fair["freight_extra"]["value"] == 275)
(tmp / "badunit.json").write_text(json.dumps({"fields": {"goods": {"unit": "pct"}}}), encoding="utf-8")
check("a merge that creates a new error is not saved", run("merge", str(jpath), str(tmp / "badunit.json")).returncode != 0
      and sim.load(jpath)["fields"]["goods"]["unit"] != "pct")
for src_, hit in (("משפטי-15", True), ("משפטי: 15", True), ("משפטי\u200f 15", True), ("legal/15", True),
                  ("הספק הפסיק לענות", False), ("מחירון 2026", False)):
    check(f"ruling source pattern: {src_!r} -> {hit}", bool(sim.RULING.search(sim.BIDI.sub("", src_))) == hit)
rf = json.loads(jpath.read_text(encoding="utf-8")); rf["fields"]["ret_fee"].update(source="משפטי/3")
check("a cancellation fee from a ruling is a legal point, not a market number", not any(e.startswith("ret_fee") for e in sim.decision_errors(rf)))
huge = sim.engine_fields(json.loads(jpath.read_text(encoding="utf-8")))
huge = engine.with_value(huge, "price", 1e9)
w_, cm_, ca_, why_ = sim.buy_ceiling(huge, 0.2, None)
check("a margin cap that never binds is reported, not the search bound as a ceiling", w_ is None and why_)
dr = json.loads(jpath.read_text(encoding="utf-8")); dr["fields"]["target_margin"].update(value=None, status="missing")
check("a draft with a missing margin says its dependent lines are not conclusions", "⚠ חסר" in sim.summary(dr, "x.html"))
check("clearance is not a shared fact (it depends on the freight mode)", "fact" not in CAT["clearance"])
page2 = html_path.read_text(encoding="utf-8")
blob2 = page2.split('<script id="sim-data" type="application/json">')[1].split("</script>")[0]
check("no '<' from the data reaches the page's markup", "<" not in blob2)

shutil.rmtree(tmp)
print(f"\n{len(failures)} failures" if failures else "\nall passed")
sys.exit(1 if failures else 0)
