#!/usr/bin/env python3
"""Extra read-only cuts: per-request context buckets, cache-miss (rebuild) events,
repeated WebFetch URLs, largest Bash outputs, Hebrew token calibration, venture-folder reads."""
import json, glob, os, collections, re, statistics
HOME = os.path.expanduser('~')
BASE = os.path.join(HOME, '.claude', 'projects')
_P = HOME.replace('/', '-')  # Claude Code names project dirs after the path
DIRS = [d for d in os.environ.get('TELEMETRY_DIRS', '').split(',') if d] or [
    _P + '-repos-shahut-site', _P + '-ventures-digital-wellness-marketplace', _P + '-business-os-plugin']
CURRENT = os.environ.get('TELEMETRY_EXCLUDE_SESSION', '')
W = dict(inp=1.0, cw5=1.25, cw1h=2.0, cr=0.1, out=5.0)
HEB = re.compile(r'[֐-׿]')


def files():
    for d in DIRS:
        for f in glob.glob(f'{BASE}/{d}/*.jsonl'):
            if CURRENT and os.path.basename(f)[:-6] == CURRENT:  # a session still running: left out
                continue
            sid = os.path.basename(f)[:8]
            yield d, sid, 'main', f
            for sf in glob.glob(f'{BASE}/{d}/{os.path.basename(f)[:-6]}/subagents/**/agent-*.jsonl', recursive=True):
                mp = sf[:-6] + '.meta.json'
                try:
                    t = json.load(open(mp)).get('agentType', '?')
                except (OSError, ValueError):
                    t = '?'
                yield d, sid, t, sf


def wcost(u):
    cc = u.get('cache_creation') or {}
    cw = u.get('cache_creation_input_tokens', 0) or 0
    c5, c1 = cc.get('ephemeral_5m_input_tokens', 0) or 0, cc.get('ephemeral_1h_input_tokens', 0) or 0
    if c5 + c1 != cw:
        c5, c1 = cw, 0
    return ((u.get('input_tokens', 0) or 0) * W['inp'] + c5 * W['cw5'] + c1 * W['cw1h']
            + (u.get('cache_read_input_tokens', 0) or 0) * W['cr'] + (u.get('output_tokens', 0) or 0) * W['out'])


buckets = collections.defaultdict(lambda: [0, 0.0, 0])  # reqs, weighted, raw
misses = []
fetch = collections.defaultdict(list)
bash = []
heb_cal = []
venture_reads = collections.defaultdict(lambda: [0, 0])
total_w = 0.0
out_w = 0.0
for d, sid, who, f in files():
    seen = set(); tu = {}; pending = []; prev_ts = None
    for line in open(f, encoding='utf-8', errors='replace'):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get('type') == 'assistant':
            m = r['message']
            for c in m.get('content') or []:
                if isinstance(c, dict) and c.get('type') == 'tool_use':
                    tu[c['id']] = (c['name'], c.get('input') or {})
            mid = m.get('id')
            if mid in seen or not m.get('usage') or m.get('model') == '<synthetic>':
                continue
            seen.add(mid)
            u = m['usage']
            ctx = (u.get('input_tokens', 0) or 0) + (u.get('cache_creation_input_tokens', 0) or 0) + (u.get('cache_read_input_tokens', 0) or 0)
            w = wcost(u); total_w += w; out_w += (u.get('output_tokens', 0) or 0) * W['out']
            b = '<50K' if ctx < 50e3 else '50-100K' if ctx < 100e3 else '100-200K' if ctx < 200e3 else '200-400K' if ctx < 400e3 else '>=400K'
            buckets[b][0] += 1; buckets[b][1] += w; buckets[b][2] += ctx
            cw = u.get('cache_creation_input_tokens', 0) or 0
            cr = u.get('cache_read_input_tokens', 0) or 0
            # rebuild: large write while read covers < 50% of context, and not the first request
            if cw > 30000 and cr < 0.5 * ctx and len(seen) > 1:
                ts = r.get('timestamp')
                misses.append((sid, who, ctx, cw, cr, w, ts, prev_ts))
            new = (u.get('input_tokens', 0) or 0) + cw
            for p in pending:
                p['new'] = new; p['batch'] = len(pending)
            pending = []
            prev_ts = r.get('timestamp')
        elif r.get('type') == 'user':
            c = r['message'].get('content')
            if isinstance(c, list):
                for x in c:
                    if isinstance(x, dict) and x.get('type') == 'tool_result':
                        name, inp = tu.get(x.get('tool_use_id'), ('?', {}))
                        cont = x.get('content')
                        txt = cont if isinstance(cont, str) else ''.join(y.get('text', '') for y in (cont or []) if isinstance(y, dict))
                        rec = dict(name=name, inp=inp, chars=len(txt), heb=len(HEB.findall(txt)), new=None, batch=1)
                        pending.append(rec)
                        if name == 'WebFetch':
                            fetch[inp.get('url', '?').rstrip('/').replace('.md', '')].append((sid, who, len(txt)))
                        if name == 'Bash':
                            bash.append((len(txt), sid, who, inp.get('command', '')[:120].replace('\n', ' ')))
                        if name == 'Read':
                            fp = inp.get('file_path', '')
                            if 'יזמות' in fp or 'wellness' in fp:
                                venture_reads[(sid, fp)][0] += 1; venture_reads[(sid, fp)][1] += len(txt)
                            heb_cal.append(rec)
        if r.get('type') == 'user':
            prev_ts = prev_ts

print('# WEIGHTED COST BY CONTEXT SIZE OF THE REQUEST (excl. TELEMETRY_EXCLUDE_SESSION)')
total_w = total_w or 1  # no requests scanned: print zeros, not a crash
for b in ['<50K', '50-100K', '100-200K', '200-400K', '>=400K']:
    n, w, raw = buckets[b]
    print(f"{b}: reqs {n} | weighted {w:,.0f} ({w/total_w*100:.1f}%) | avg ctx {raw/max(n,1):,.0f}")
print(f"output share of weighted: {out_w/total_w*100:.1f}%")
print()
print('# CACHE REBUILD EVENTS (cache_write>30K and cache_read<50% of ctx, not first request)')
mw = sum(m[5] for m in misses)
print(f"events {len(misses)} | weighted {mw:,.0f} ({mw/total_w*100:.1f}% of all) | tokens re-written {sum(m[3] for m in misses):,}")
import datetime
def gap(a, b):
    try:
        return (datetime.datetime.fromisoformat(a.replace('Z', '+00:00')) - datetime.datetime.fromisoformat(b.replace('Z', '+00:00'))).total_seconds() / 60
    except Exception:
        return None
for m in sorted(misses, key=lambda x: -x[5])[:12]:
    g = gap(m[6], m[7]) if m[7] else None
    print(f"  {m[0]} {m[1]} ctx {m[2]:,} write {m[3]:,} read {m[4]:,} weighted {m[5]:,.0f} gap_since_prev_req {g:.0f} min" if g is not None else f"  {m[0]} {m[1]} ctx {m[2]:,} write {m[3]:,}")
print()
print('# WEBFETCH URLS FETCHED MORE THAN ONCE')
rep = [(u, v) for u, v in fetch.items() if len(v) > 1]
tot_rep = sum(sum(x[2] for x in v[1:]) for u, v in rep)
print(f"distinct urls {len(fetch)}, fetched >1: {len(rep)}, redundant chars {tot_rep:,}")
for u, v in sorted(rep, key=lambda kv: -sum(x[2] for x in kv[1]))[:12]:
    print(f"  {len(v)}x in {len(set(x[0] for x in v))} sessions by {sorted(set(x[1] for x in v))} | {sum(x[2] for x in v):,} ch | {u}")
print()
print('# LARGEST BASH OUTPUTS')
for n, sid, who, cmd in sorted(bash, reverse=True)[:12]:
    print(f"  {n:,} ch | {sid} {who} | {cmd}")
print()
print('# HEBREW TOKEN CALIBRATION (single-result Read batches, >4K chars)')
for lo, hi, lab in [(0.0, 0.1, 'mostly non-Hebrew'), (0.3, 1.01, 'Hebrew-heavy (>=30% Hebrew letters)')]:
    rs = [p['new'] / p['chars'] for p in heb_cal if p['batch'] == 1 and p['chars'] > 4000 and p['new'] and lo <= p['heb'] / p['chars'] < hi]
    if rs:
        print(f"  {lab}: n={len(rs)} tokens/char upper-bound median {statistics.median(rs):.3f} min {min(rs):.3f}")
print()
print('# VENTURE-FOLDER READS (Read tool, local transcripts only)')
tv = sum(v[1] for v in venture_reads.values())
print(f"  distinct (session,file) {len(venture_reads)} | total chars {tv:,}")
for (sid, fp), (n, ch) in sorted(venture_reads.items(), key=lambda kv: -kv[1][1])[:10]:
    print(f"  {sid} {n}x {ch:,} ch | {fp.split('wellness')[-1][-80:]}")
