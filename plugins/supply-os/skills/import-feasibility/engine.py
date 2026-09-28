"""The import-and-sale calculation. engine.js is the same code for the page; test_import_feasibility.py
runs both on random inputs and fails on any difference, so change them together.

Input F: {field_id: {"value", "unit", "per", "vat_domestic"}} with every field of fields.json.
Money: unit usd/ils/pct, per unit/shipment (import side) or sale/once/month/year/kg/cbm. Rates in percent.
All results in ILS. "Per sale" means per completed sale: returns are spread over the sales that stay.
"""
import math

RATE_HI = {"ret_rate": 90.0, "defect": 95.0}


def compute(F):
    val = lambda k: float(F[k]["value"])
    fx = val("fx")
    lic = F["dealer"]["value"] == "licensed"
    vat = val("vat") / 100
    Q = max(1.0, val("qty"))

    def cur(k):
        x = F[k]
        return float(x["value"]) * (fx if x["unit"] == "usd" else 1.0)

    def dom(k, amount):
        return amount / (1 + vat) if lic and F[k].get("vat_domestic") else amount

    def ship(k, base=0.0):
        x = F[k]
        if x["unit"] == "pct":
            return float(x["value"]) / 100 * base
        return cur(k) * (Q if x.get("per") == "unit" else 1.0)

    def sale(k, base=0.0):
        x = F[k]
        if x["unit"] == "pct":
            return float(x["value"]) / 100 * base
        return cur(k)

    goods_t = ship("goods")
    mode = F["freight_mode"]["value"]
    if mode == "air":
        chargeable = max(val("unit_kg"), val("unit_l") / 1000 * val("vol_factor"))
        freight = chargeable * Q * cur("air_rate")
    elif mode == "sea":
        freight = max(val("unit_l") / 1000 * Q, val("sea_min_cbm")) * cur("sea_rate")
    else:
        freight = ship("freight_fixed")
    dg = ship("dg_surcharge", freight)
    extra = ship("freight_extra")
    freight_t = freight + dg + extra
    ins = ship("insurance", goods_t + freight_t)
    pay = ship("pay_fee", goods_t)
    cif = goods_t + freight_t + ins
    duty = val("duty") / 100 * cif
    ptax = val("purchase_tax") / 100 * (cif + duty)
    ivat = vat * (cif + duty + ptax)
    clear = dom("clearance", ship("clearance"))
    std_ship = dom("std_shipment", ship("std_shipment"))
    compl = dom("compliance_unit", ship("compliance_unit"))
    import_t = goods_t + freight_t + ins + pay + duty + ptax + (0.0 if lic else ivat) + clear + std_ship + compl
    sellable = Q * (1 - min(max(val("defect"), 0.0), RATE_HI["defect"]) / 100)
    landed = import_t / sellable
    onetime = dom("std_onetime", cur("std_onetime")) + dom("onetime_other", cur("onetime_other"))

    P = cur("price")
    shp = cur("ship_paid")
    charged = P + shp
    div = 1 + vat if lic else 1.0
    r = min(max(val("ret_rate"), 0.0), RATE_HI["ret_rate"]) / 100
    k = r / (1 - r)
    rev = charged / div
    vat_out = charged - rev
    fee_kept = min(sale("ret_fee", P), cur("ret_fee_cap")) / div
    fulfil = dom("pack", sale("pack")) + dom("ship_out", sale("ship_out"))
    fees = dom("clearing", sale("clearing", charged)) + dom("clearing_fixed", sale("clearing_fixed"))
    platform = dom("platform_fee", sale("platform_fee", P))
    mkt = dom("cac", sale("cac", P))
    ret_ship = dom("ret_ship", sale("ret_ship"))
    ret_loss = val("ret_loss") / 100 * landed
    returns = k * (fulfil + fees + mkt + ret_ship + ret_loss - fee_kept)
    warranty = dom("warranty", sale("warranty", landed))
    spm = val("sales_pm")
    monthly = dom("monthly", cur("monthly"))
    monthly_u = monthly / spm if spm > 0 else 0.0
    onetime_u = onetime / sellable

    contrib = rev - fulfil - fees - platform - mkt - returns - warranty - monthly_u
    profit_repeat = contrib - landed
    profit_first = profit_repeat - onetime_u
    cash_out = import_t + onetime
    alt = cur("alt_price")
    annual = charged * spm * 12
    return {
        "Q": Q, "sellable": sellable, "goods_t": goods_t, "freight_t": freight_t, "dg": dg, "extra": extra, "ins": ins, "pay": pay,
        "cif": cif, "duty": duty, "ptax": ptax, "ivat": ivat, "ivat_cost": 0.0 if lic else ivat, "clear": clear,
        "std_ship": std_ship, "compl": compl, "import_t": import_t, "landed": landed, "onetime": onetime,
        "landed_first": landed + onetime_u,
        "P": P, "charged": charged, "rev": rev, "vat_out": vat_out, "fulfil": fulfil, "fees": fees,
        "platform": platform, "mkt": mkt, "returns": returns, "warranty": warranty, "monthly_u": monthly_u,
        "onetime_u": onetime_u, "contrib": contrib, "profit_repeat": profit_repeat, "profit_first": profit_first,
        "margin_first": profit_first / charged if charged > 0 else -math.inf,
        "margin_repeat": profit_repeat / charged if charged > 0 else -math.inf,
        "cash_out": cash_out, "capital": cash_out + (ivat if lic else 0.0),
        "be_units": cash_out / contrib if contrib > 0 else None,
        "months": sellable / spm if spm > 0 else None,
        "mkt_order": (mkt + k * mkt) * sellable,
        "order_profit": profit_first * sellable,
        "annual": annual, "over_ceiling": (not lic) and annual > cur("exempt_ceiling"),
        "vs_alt": P / alt if alt > 0 else None,
        "bases": {"price": P, "charged": charged, "goods": goods_t, "goods_freight": goods_t + freight_t,
                  "freight": freight, "landed": landed},
    }


def conv_factor(r, fx, c, fu, fp, tu, tp):
    """new value = old value * factor, when a field moves between $, ₪ and % or between per-unit and per-shipment.
    r = compute() of the current inputs (the bases of the % fields); None when the target base is zero."""
    def m(u, p):
        if u == "pct":
            return r["bases"][c["pct_of"]] / 100
        return (fx if u == "usd" else 1.0) * (r["Q"] if p == "unit" and "shipment" in c.get("pers", []) else 1.0)
    mt = m(tu, tp)
    return m(fu, fp) / mt if mt else None


def with_value(F, k, v):
    G = dict(F)
    G[k] = dict(F[k], value=v)
    return G


def metric_of(name):
    return lambda res: res[name]


def bisect(F, k, name, target, lo, hi, iters=60):
    """The value of field k at which result[name] == target, searching [lo, hi]; None if it never crosses."""
    f = lambda x: compute(with_value(F, k, x))[name] - target
    flo, fhi = f(lo), f(hi)
    if flo == 0:
        return lo
    if (flo > 0) == (fhi > 0):
        return None
    for _ in range(iters):
        mid = (lo + hi) / 2
        fm = f(mid)
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return (lo + hi) / 2


def search_range(F, cat, k):
    kind = cat[k]["kind"]
    v = abs(float(F[k]["value"]))
    if kind == "rate" or F[k].get("unit") == "pct":
        return 0.0, RATE_HI.get(k, max(300.0, v * 10))
    if kind == "count":
        return (1.0 if k == "qty" else 0.01), max(v * 100, 1e5)
    return 0.0, max(v * 100, 1e6)


def headline(F, cat):
    """Break-even price, price for the target margin, and the highest buy price that still meets it —
    max_goods on the first order (one-time costs in), max_goods_repeat on every later order."""
    tm = float(F["target_margin"]["value"]) / 100
    lo, hi = search_range(F, cat, "price")
    glo, ghi = search_range(F, cat, "goods")
    return {
        "be_price": bisect(F, "price", "profit_first", 0.0, lo, hi),
        "target_price": bisect(F, "price", "margin_first", tm, lo, hi),
        "max_goods": bisect(F, "goods", "margin_first", tm, glo, ghi),
        "max_goods_repeat": bisect(F, "goods", "margin_repeat", tm, glo, ghi),
    }


THRESHOLD_FIELDS = ["price", "goods", "cac", "ret_rate", "qty", "sales_pm", "ship_out", "air_rate", "sea_rate",
                    "freight_fixed", "duty", "clearance", "std_onetime", "defect", "platform_fee", "warranty"]


def thresholds(F, cat, hidden=()):
    """For each lever: the value where the first order stops making money, and the direction that hurts."""
    mode = F["freight_mode"]["value"]
    inactive = {"air": {"sea_rate", "freight_fixed"}, "sea": {"air_rate", "freight_fixed"},
                "fixed": {"air_rate", "sea_rate"}}[mode]
    base = compute(F)["profit_first"]
    out = []
    for k in THRESHOLD_FIELDS:
        if k in hidden or k in inactive:
            continue
        v = float(F[k]["value"])
        lo, hi = search_range(F, cat, k)
        step = max(abs(v) * 0.01, 1e-3)
        slope = compute(with_value(F, k, v + step))["profit_first"] - base
        if abs(slope) < 1e-9:
            continue
        t = bisect(F, k, "profit_first", 0.0, lo, hi)
        state = "limit" if t is not None else ("cannot_fix" if base < 0 else "safe")
        out.append({"id": k, "value": v, "limit": t, "state": state, "hurts_when": "up" if slope < 0 else "down",
                    "headroom": (t - v) / v if t is not None and v else None})
    order = {"limit": 0, "cannot_fix": 1, "safe": 2}
    out.sort(key=lambda x: (order[x["state"]], abs(x["headroom"]) if x["headroom"] is not None else math.inf))
    return out


def sensitivity(F, ranges):
    """ranges: {id: (low, high, unit, per)}. Profit of the first order at each end, widest swing first."""
    out = []
    for k, (low, high, unit, per) in ranges.items():
        if low is None or high is None or low == high:
            continue
        a = compute(dict(F, **{k: dict(F[k], value=low, unit=unit, per=per)}))["profit_first"]
        b = compute(dict(F, **{k: dict(F[k], value=high, unit=unit, per=per)}))["profit_first"]
        if abs(b - a) < 0.005:
            continue
        out.append({"id": k, "at_low": a, "at_high": b, "swing": abs(b - a)})
    out.sort(key=lambda x: -x["swing"])
    return out


def scenario(F, ranges, worst=True):
    """Every field at the end of its plausible range that hurts (worst) or helps (best) profit."""
    G = dict(F)
    for s in sensitivity(F, ranges):
        low, high, unit, per = ranges[s["id"]]
        pick_low = (s["at_low"] < s["at_high"]) == worst
        G[s["id"]] = dict(F[s["id"]], value=low if pick_low else high, unit=unit, per=per)
    return G
