/* The import-and-sale calculation for the page. Same code as engine.py; test_import_feasibility.py runs
   both on random inputs and fails on any difference, so change them together. */
(function (root) {
  const RATE_HI = { ret_rate: 90, defect: 95 };

  function compute(F) {
    const val = (k) => +F[k].value;
    const fx = val('fx');
    const lic = F.dealer.value === 'licensed';
    const vat = val('vat') / 100;
    const Q = Math.max(1, val('qty'));
    const cur = (k) => +F[k].value * (F[k].unit === 'usd' ? fx : 1);
    const dom = (k, a) => (lic && F[k].vat_domestic ? a / (1 + vat) : a);
    const ship = (k, base = 0) => (F[k].unit === 'pct' ? +F[k].value / 100 * base : cur(k) * (F[k].per === 'unit' ? Q : 1));
    const sale = (k, base = 0) => (F[k].unit === 'pct' ? +F[k].value / 100 * base : cur(k));

    const goods_t = ship('goods');
    const mode = F.freight_mode.value;
    let freight;
    if (mode === 'air') {
      const chargeable = Math.max(val('unit_kg'), val('unit_l') / 1000 * val('vol_factor'));
      freight = chargeable * Q * cur('air_rate');
    } else if (mode === 'sea') {
      freight = Math.max(val('unit_l') / 1000 * Q, val('sea_min_cbm')) * cur('sea_rate');
    } else {
      freight = ship('freight_fixed');
    }
    const dg = ship('dg_surcharge', freight);
    const extra = ship('freight_extra');
    const freight_t = freight + dg + extra;
    const ins = ship('insurance', goods_t + freight_t);
    const pay = ship('pay_fee', goods_t);
    const cif = goods_t + freight_t + ins;
    const duty = val('duty') / 100 * cif;
    const ptax = val('purchase_tax') / 100 * (cif + duty);
    const ivat = vat * (cif + duty + ptax);
    const clear = dom('clearance', ship('clearance'));
    const std_ship = dom('std_shipment', ship('std_shipment'));
    const compl = dom('compliance_unit', ship('compliance_unit'));
    const import_t = goods_t + freight_t + ins + pay + duty + ptax + (lic ? 0 : ivat) + clear + std_ship + compl;
    const sellable = Q * (1 - Math.min(Math.max(val('defect'), 0), RATE_HI.defect) / 100);
    const landed = import_t / sellable;
    const onetime = dom('std_onetime', cur('std_onetime')) + dom('onetime_other', cur('onetime_other'));

    const P = cur('price');
    const shp = cur('ship_paid');
    const charged = P + shp;
    const div = lic ? 1 + vat : 1;
    const r = Math.min(Math.max(val('ret_rate'), 0), RATE_HI.ret_rate) / 100;
    const k = r / (1 - r);
    const rev = charged / div;
    const vat_out = charged - rev;
    const fee_kept = Math.min(sale('ret_fee', P), cur('ret_fee_cap')) / div;
    const fulfil = dom('pack', sale('pack')) + dom('ship_out', sale('ship_out'));
    const fees = dom('clearing', sale('clearing', charged)) + dom('clearing_fixed', sale('clearing_fixed'));
    const platform = dom('platform_fee', sale('platform_fee', P));
    const mkt = dom('cac', sale('cac', P));
    const ret_ship = dom('ret_ship', sale('ret_ship'));
    const ret_loss = val('ret_loss') / 100 * landed;
    const returns = k * (fulfil + fees + mkt + ret_ship + ret_loss - fee_kept);
    const warranty = dom('warranty', sale('warranty', landed));
    const spm = val('sales_pm');
    const monthly = dom('monthly', cur('monthly'));
    const monthly_u = spm > 0 ? monthly / spm : 0;
    const onetime_u = onetime / sellable;

    const contrib = rev - fulfil - fees - platform - mkt - returns - warranty - monthly_u;
    const profit_repeat = contrib - landed;
    const profit_first = profit_repeat - onetime_u;
    const cash_out = import_t + onetime;
    const alt = cur('alt_price');
    const annual = charged * spm * 12;
    return {
      Q, sellable, goods_t, freight_t, dg, extra, ins, pay, cif, duty, ptax, ivat, ivat_cost: lic ? 0 : ivat, clear,
      std_ship, compl, import_t, landed, landed_first: landed + onetime_u, onetime, P, charged, rev, vat_out, fulfil, fees, platform, mkt, returns,
      warranty, monthly_u, onetime_u, contrib, profit_repeat, profit_first,
      margin_first: charged > 0 ? profit_first / charged : -Infinity,
      margin_repeat: charged > 0 ? profit_repeat / charged : -Infinity,
      cash_out, capital: cash_out + (lic ? ivat : 0),
      be_units: contrib > 0 ? cash_out / contrib : null,
      months: spm > 0 ? sellable / spm : null,
      mkt_order: (mkt + k * mkt) * sellable,
      order_profit: profit_first * sellable,
      annual, over_ceiling: !lic && annual > cur('exempt_ceiling'),
      vs_alt: alt > 0 ? P / alt : null,
      bases: { price: P, charged, goods: goods_t, goods_freight: goods_t + freight_t, freight, landed },
    };
  }

  function convFactor(r, fx, c, fu, fp, tu, tp) {
    const m = (u, p) => (u === 'pct' ? r.bases[c.pct_of] / 100
      : (u === 'usd' ? fx : 1) * (p === 'unit' && (c.pers || []).includes('shipment') ? r.Q : 1));
    const mt = m(tu, tp);
    return mt ? m(fu, fp) / mt : null;
  }

  function withValue(F, k, v) {
    const G = Object.assign({}, F);
    G[k] = Object.assign({}, F[k], { value: v });
    return G;
  }

  function bisect(F, k, name, target, lo, hi, iters = 60) {
    const f = (x) => compute(withValue(F, k, x))[name] - target;
    let flo = f(lo);
    const fhi = f(hi);
    if (flo === 0) return lo;
    if ((flo > 0) === (fhi > 0)) return null;
    for (let i = 0; i < iters; i++) {
      const mid = (lo + hi) / 2;
      const fm = f(mid);
      if ((fm > 0) === (flo > 0)) { lo = mid; flo = fm; } else { hi = mid; }
    }
    return (lo + hi) / 2;
  }

  function searchRange(F, cat, k) {
    const kind = cat[k].kind;
    const v = Math.abs(+F[k].value);
    if (kind === 'rate' || F[k].unit === 'pct') return [0, k in RATE_HI ? RATE_HI[k] : Math.max(300, v * 10)];
    if (kind === 'count') return [k === 'qty' ? 1 : 0.01, Math.max(v * 100, 1e5)];
    return [0, Math.max(v * 100, 1e6)];
  }

  function headline(F, cat) {
    const tm = +F.target_margin.value / 100;
    const [lo, hi] = searchRange(F, cat, 'price');
    const [glo, ghi] = searchRange(F, cat, 'goods');
    return {
      be_price: bisect(F, 'price', 'profit_first', 0, lo, hi),
      target_price: bisect(F, 'price', 'margin_first', tm, lo, hi),
      max_goods: bisect(F, 'goods', 'margin_first', tm, glo, ghi),
      max_goods_repeat: bisect(F, 'goods', 'margin_repeat', tm, glo, ghi),
    };
  }

  const THRESHOLD_FIELDS = ['price', 'goods', 'cac', 'ret_rate', 'qty', 'sales_pm', 'ship_out', 'air_rate', 'sea_rate',
    'freight_fixed', 'duty', 'clearance', 'std_onetime', 'defect', 'platform_fee', 'warranty'];

  function thresholds(F, cat, hidden = []) {
    const mode = F.freight_mode.value;
    const inactive = { air: ['sea_rate', 'freight_fixed'], sea: ['air_rate', 'freight_fixed'], fixed: ['air_rate', 'sea_rate'] }[mode];
    const base = compute(F).profit_first;
    const out = [];
    for (const k of THRESHOLD_FIELDS) {
      if (hidden.includes(k) || inactive.includes(k)) continue;
      const v = +F[k].value;
      const [lo, hi] = searchRange(F, cat, k);
      const step = Math.max(Math.abs(v) * 0.01, 1e-3);
      const slope = compute(withValue(F, k, v + step)).profit_first - base;
      if (Math.abs(slope) < 1e-9) continue;
      const t = bisect(F, k, 'profit_first', 0, lo, hi);
      const state = t !== null ? 'limit' : (base < 0 ? 'cannot_fix' : 'safe');
      out.push({ id: k, value: v, limit: t, state, hurts_when: slope < 0 ? 'up' : 'down', headroom: t !== null && v ? (t - v) / v : null });
    }
    const order = { limit: 0, cannot_fix: 1, safe: 2 };
    const room = (x) => (x.headroom === null ? Infinity : Math.abs(x.headroom));
    out.sort((a, b) => order[a.state] - order[b.state] || room(a) - room(b));
    return out;
  }

  function sensitivity(F, ranges) {
    const out = [];
    for (const k of Object.keys(ranges)) {
      const [low, high, unit, per] = ranges[k];
      if (low === null || high === null || low === high) continue;
      const at = (v) => compute(Object.assign({}, F, { [k]: Object.assign({}, F[k], { value: v, unit, per }) })).profit_first;
      const a = at(low), b = at(high);
      if (Math.abs(b - a) < 0.005) continue;
      out.push({ id: k, at_low: a, at_high: b, swing: Math.abs(b - a) });
    }
    out.sort((x, y) => y.swing - x.swing);
    return out;
  }

  function scenario(F, ranges, worst = true) {
    const G = Object.assign({}, F);
    for (const s of sensitivity(F, ranges)) {
      const [low, high, unit, per] = ranges[s.id];
      const pickLow = (s.at_low < s.at_high) === worst;
      G[s.id] = Object.assign({}, F[s.id], { value: pickLow ? low : high, unit, per });
    }
    return G;
  }

  const api = { compute, convFactor, bisect, searchRange, headline, thresholds, sensitivity, scenario, THRESHOLD_FIELDS };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.SimEngine = api;
})(this);
