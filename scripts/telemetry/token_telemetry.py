#!/usr/bin/env python3
"""Read-only token telemetry over Claude Code .jsonl transcripts.

Usage: python3 token_telemetry.py > report.txt
Parses assistant messages (deduped by message.id), attributes usage to
session / main vs subagent, and measures tool_result payloads, repeated reads,
and subagent prompt/result sizes.
"""
import json, glob, os, collections, statistics, re

HOME = os.path.expanduser('~')
BASE = os.path.join(HOME, '.claude', 'projects')
_P = HOME.replace('/', '-')  # Claude Code names project dirs after the path
DIRS = [d for d in os.environ.get('TELEMETRY_DIRS', '').split(',') if d] or [
    _P + '-repos-shahut-site', _P + '-ventures-digital-wellness-marketplace', _P + '-business-os-plugin']
CURRENT = os.environ.get('TELEMETRY_EXCLUDE_SESSION', '')  # a session still running, reported apart

# relative price weights (input = 1). Opus-class pricing ratios:
# cache write 5m 1.25x, cache write 1h 2x, cache read 0.1x, output 5x
W = dict(inp=1.0, cw5=1.25, cw1h=2.0, cr=0.1, out=5.0)


def text_len(content):
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content)
    n = 0
    for c in content:
        if isinstance(c, dict):
            if c.get('type') == 'text':
                n += len(c.get('text', ''))
            elif c.get('type') == 'image':
                n += 0
            elif 'content' in c:
                n += text_len(c['content'])
        elif isinstance(c, str):
            n += len(c)
    return n


def load(path):
    out = []
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


class Agg:
    def __init__(self):
        self.inp = self.cw = self.cw5 = self.cw1h = self.cr = self.out = 0
        self.reqs = 0
        self.first_ctx = None
        self.max_ctx = 0
        self.models = collections.Counter()

    def add(self, u, model):
        i = u.get('input_tokens', 0) or 0
        cw = u.get('cache_creation_input_tokens', 0) or 0
        cr = u.get('cache_read_input_tokens', 0) or 0
        o = u.get('output_tokens', 0) or 0
        cc = u.get('cache_creation') or {}
        c5 = cc.get('ephemeral_5m_input_tokens', 0) or 0
        c1 = cc.get('ephemeral_1h_input_tokens', 0) or 0
        if c5 + c1 != cw:  # unknown split -> treat as 5m
            c5, c1 = cw, 0
        self.inp += i; self.cw += cw; self.cw5 += c5; self.cw1h += c1
        self.cr += cr; self.out += o; self.reqs += 1
        ctx = i + cw + cr
        if self.first_ctx is None:
            self.first_ctx = ctx
        self.max_ctx = max(self.max_ctx, ctx)
        self.models[model] += 1

    def merge(self, o):
        for k in ('inp', 'cw', 'cw5', 'cw1h', 'cr', 'out', 'reqs'):
            setattr(self, k, getattr(self, k) + getattr(o, k))
        self.max_ctx = max(self.max_ctx, o.max_ctx)
        self.models.update(o.models)

    @property
    def total(self):
        return self.inp + self.cw + self.cr + self.out

    @property
    def weighted(self):
        return (self.inp * W['inp'] + self.cw5 * W['cw5'] + self.cw1h * W['cw1h']
                + self.cr * W['cr'] + self.out * W['out'])

    @property
    def hit(self):
        d = self.inp + self.cw + self.cr
        return self.cr / d if d else 0


def parse_file(path):
    recs = load(path)
    agg = Agg()
    seen = set()
    tool_uses = {}      # id -> (name, input)
    results = []        # (tool_use_id, chars, name, input, next_req_new_tokens)
    agent_calls = []    # (prompt_len, subagent_type, result_len, id)
    compactions = 0
    pending = []        # tool_result idx awaiting next request's new tokens
    first_user_prompt = None
    last_assistant_text = ''
    for d in recs:
        if d.get('isCompactSummary'):
            compactions += 1
        t = d.get('type')
        if t == 'assistant':
            m = d.get('message', {})
            mid = m.get('id') or d.get('requestId')
            for c in m.get('content') or []:
                if isinstance(c, dict) and c.get('type') == 'tool_use':
                    tool_uses[c['id']] = (c['name'], c.get('input') or {})
                    if c['name'] in ('Agent', 'Task'):
                        inp = c.get('input') or {}
                        agent_calls.append(dict(id=c['id'], prompt_len=len(inp.get('prompt', '') or ''),
                                                type=inp.get('subagent_type') or 'general-purpose',
                                                result_len=None))
                if isinstance(c, dict) and c.get('type') == 'text' and c.get('text', '').strip():
                    last_assistant_text = c['text']
            if mid in seen:
                continue
            seen.add(mid)
            u = m.get('usage')
            if u and m.get('model') != '<synthetic>':
                agg.add(u, m.get('model'))
                new_tokens = (u.get('input_tokens', 0) or 0) + (u.get('cache_creation_input_tokens', 0) or 0)
                for r in pending:
                    r['next_new_tokens'] = new_tokens
                    r['batch'] = len(pending)
                pending = []
        elif t == 'user':
            m = d.get('message', {})
            c = m.get('content')
            if first_user_prompt is None and not d.get('isMeta'):
                first_user_prompt = text_len(c)
            if isinstance(c, list):
                for x in c:
                    if isinstance(x, dict) and x.get('type') == 'tool_result':
                        tid = x.get('tool_use_id')
                        name, inp = tool_uses.get(tid, ('?', {}))
                        ln = text_len(x.get('content'))
                        r = dict(tid=tid, chars=ln, name=name, input=inp, next_new_tokens=None, batch=1)
                        results.append(r); pending.append(r)
                        for a in agent_calls:
                            if a['id'] == tid:
                                a['result_len'] = ln
                                tur = d.get('toolUseResult')
                                if isinstance(tur, dict) and tur.get('status') == 'async_launched':
                                    a['async'] = True
    # async agents: result arrives as <task-notification> in a user message
    return dict(agg=agg, results=results, agent_calls=agent_calls, compactions=compactions,
                first_user_prompt=first_user_prompt, last_text_len=len(last_assistant_text))


def main():
    sessions = []  # dicts
    for d in DIRS:
        root = os.path.join(BASE, d)
        for f in sorted(glob.glob(root + '/*.jsonl')):
            sid = os.path.basename(f)[:-6]
            main_p = parse_file(f)
            subs = []
            for sf in sorted(glob.glob(os.path.join(root, sid, 'subagents', '**', 'agent-*.jsonl'), recursive=True)):
                meta_p = sf[:-6] + '.meta.json'
                meta = {}
                if os.path.exists(meta_p):
                    try:
                        meta = json.load(open(meta_p))
                    except Exception:
                        pass
                p = parse_file(sf)
                p['type'] = meta.get('agentType', '?')
                p['desc'] = meta.get('description', '')
                p['workflow'] = '/workflows/' in sf
                p['file'] = sf
                subs.append(p)
            sessions.append(dict(dir=d, sid=sid, main=main_p, subs=subs))
    return sessions


def fmt(n):
    return f'{n:,.0f}'


if __name__ == '__main__':
    S = main()
    P = print
    pct = lambda a, b: a / b * 100 if b else 0
    P('# SESSIONS (main + subagents)')
    P('dir | sid | reqs | total tok | weighted | main tok | sub tok | sub% | sub w% | nsubs | cache hit | first ctx | max ctx | compactions')
    grand = Agg(); grand_main = Agg(); grand_sub = Agg()
    grand_ex = Agg(); grand_ex_sub = Agg()
    by_dir = collections.defaultdict(lambda: [Agg(), Agg()])
    for s in S:
        ma = s['main']['agg']
        sa = Agg()
        for p in s['subs']:
            sa.merge(p['agg'])
        tot = Agg(); tot.merge(ma); tot.merge(sa)
        grand.merge(tot); grand_main.merge(ma); grand_sub.merge(sa)
        if s['sid'] != CURRENT:  # exact id; unset -> nothing excluded
            grand_ex.merge(tot); grand_ex_sub.merge(sa)
        by_dir[s['dir']][0].merge(ma); by_dir[s['dir']][1].merge(sa)
        subpct = sa.total / tot.total * 100 if tot.total else 0
        subw = sa.weighted / tot.weighted * 100 if tot.weighted else 0
        P(f"{s['dir'][12:32]} | {s['sid'][:8]} | {tot.reqs} | {fmt(tot.total)} | {fmt(tot.weighted)} | {fmt(ma.total)} | {fmt(sa.total)} | {subpct:.0f}% | {subw:.0f}% | {len(s['subs'])} | {tot.hit*100:.1f}% | {fmt(ma.first_ctx or 0)} | {fmt(ma.max_ctx)} | {s['main']['compactions']}")
    P()
    P('# PER DIR')
    for d, (m, sa) in by_dir.items():
        t = Agg(); t.merge(m); t.merge(sa)
        P(f"{d}: total {fmt(t.total)} weighted {fmt(t.weighted)} sub share {sa.total/t.total*100 if t.total else 0:.1f}% (weighted {sa.weighted/t.weighted*100 if t.weighted else 0:.1f}%) hit {t.hit*100:.1f}% models {dict(t.models)}")
    P()
    for label, g in (('ALL', grand), ('ALL excl. current study session', grand_ex)):
        P(f"# GRAND {label}: reqs {g.reqs} total {fmt(g.total)} | input {fmt(g.inp)} cache_write {fmt(g.cw)} (5m {fmt(g.cw5)} / 1h {fmt(g.cw1h)}) cache_read {fmt(g.cr)} output {fmt(g.out)} | hit {g.hit*100:.1f}% | weighted {fmt(g.weighted)}")
        P(f"   weighted shares: input {pct(g.inp*W['inp'], g.weighted):.1f}% cw5 {pct(g.cw5*W['cw5'], g.weighted):.1f}% cw1h {pct(g.cw1h*W['cw1h'], g.weighted):.1f}% cread {pct(g.cr*W['cr'], g.weighted):.1f}% output {pct(g.out*W['out'], g.weighted):.1f}%")
    P(f"# subagent share (all): raw {pct(grand_sub.total, grand.total):.1f}% weighted {pct(grand_sub.weighted, grand.weighted):.1f}%")
    P(f"# subagent share (excl current): raw {pct(grand_ex_sub.total, grand_ex.total):.1f}% weighted {pct(grand_ex_sub.weighted, grand_ex.weighted):.1f}%")
    P()

    P('# PER SUBAGENT TYPE')
    bt = collections.defaultdict(list)
    for s in S:
        for p in s['subs']:
            key = p['type'] + (' [workflow]' if p['workflow'] else '')
            bt[key].append(p)
    P('type | n | total tok | weighted | avg tok/run | avg reqs | median first ctx | avg max ctx | hit | out')
    for k, ps in sorted(bt.items(), key=lambda kv: -sum(p['agg'].total for p in kv[1])):
        a = Agg()
        for p in ps:
            a.merge(p['agg'])
        fc = statistics.median([p['agg'].first_ctx or 0 for p in ps])
        mc = statistics.mean([p['agg'].max_ctx for p in ps])
        P(f"{k} | {len(ps)} | {fmt(a.total)} | {fmt(a.weighted)} | {fmt(a.total/len(ps))} | {a.reqs/len(ps):.0f} | {fmt(fc)} | {fmt(mc)} | {a.hit*100:.1f}% | {fmt(a.out)}")
    P()

    P('# SUBAGENT PROMPT / RESULT SIZES (from subagent transcripts: first user msg, last assistant text)')
    pl = [p['first_user_prompt'] or 0 for s in S for p in s['subs']]
    rl = [p['last_text_len'] for s in S for p in s['subs']]
    if pl:
        P(f"n={len(pl)} prompt chars: median {statistics.median(pl):.0f} mean {statistics.mean(pl):.0f} min {min(pl)} max {max(pl)}")
        P(f"         result chars: median {statistics.median(rl):.0f} mean {statistics.mean(rl):.0f} min {min(rl)} max {max(rl)}")
    P('# PARENT-SIDE Agent/Task calls (input.prompt length and tool_result length)')
    calls = [a for s in S for a in s['main']['agent_calls']]
    sync = [a for a in calls if not a.get('async')]
    asy = [a for a in calls if a.get('async')]
    P(f"agent calls in main transcripts: {len(calls)} (async {len(asy)}, sync {len(sync)})")
    if calls:
        pls = [a['prompt_len'] for a in calls]
        P(f"prompt chars: median {statistics.median(pls):.0f} mean {statistics.mean(pls):.0f} max {max(pls)}")
        rs = [a['result_len'] for a in sync if a['result_len'] is not None]
        if rs:
            P(f"sync result chars: median {statistics.median(rs):.0f} mean {statistics.mean(rs):.0f} max {max(rs)}")
        tc = collections.Counter(a['type'] for a in calls)
        P('types called:', dict(tc))
    P()

    P('# LARGEST TOOL RESULTS (all transcripts). next_new = input+cache_write of the next request (upper bound on tokens it added)')
    allr = []
    for s in S:
        for r in s['main']['results']:
            allr.append((s['sid'][:8], 'main', r))
        for p in s['subs']:
            for r in p['results']:
                allr.append((s['sid'][:8], p['type'], r))
    allr.sort(key=lambda x: -x[2]['chars'])
    for sid, who, r in allr[:30]:
        inp = r['input']
        tgt = inp.get('file_path') or inp.get('command') or inp.get('url') or inp.get('query') or inp.get('prompt') or json.dumps(inp, ensure_ascii=False)
        tgt = str(tgt).replace('\n', ' ')[:110]
        P(f"{sid} | {who} | {r['name']} | {fmt(r['chars'])} ch | next_new {fmt(r['next_new_tokens'] or 0)} (batch {r['batch']}) | {tgt}")
    P()
    P('# TOOL RESULT CHARS BY TOOL')
    bytool = collections.defaultdict(lambda: [0, 0])
    for sid, who, r in allr:
        n = r['name']
        if n.startswith('mcp__'):
            n = 'mcp:' + n.split('__')[-1]
        bytool[n][0] += 1; bytool[n][1] += r['chars']
    tot_chars = sum(v[1] for v in bytool.values())
    for n, (c, ch) in sorted(bytool.items(), key=lambda kv: -kv[1][1])[:20]:
        P(f"{n} | calls {c} | chars {fmt(ch)} | {ch/tot_chars*100:.1f}% | avg {ch/c:.0f}")
    P()
    # tokens-per-char calibration from single-result batches of Read
    cal = [(r['chars'], r['next_new_tokens']) for _, _, r in allr
           if r['name'] == 'Read' and r['batch'] == 1 and r['chars'] > 8000 and r['next_new_tokens']]
    if cal:
        ratios = [t / c for c, t in cal]
        P(f"# calibration: single large Read results n={len(cal)}: next_new_tokens/chars median {statistics.median(ratios):.3f} (upper bound; includes prior assistant turn)")
    heb = [(r['chars'], r['next_new_tokens']) for _, _, r in allr
           if r['name'] == 'Read' and r['batch'] == 1 and r['chars'] > 8000 and r['next_new_tokens']
           and re.search(r'[֐-׿]', r['input'].get('file_path', '') + 'x') and 'CEO-PERSONA' not in r['input'].get('file_path', '')]
    P()

    P('# FILES READ REPEATEDLY WITHIN A SESSION (main + its subagents)')
    for s in S:
        cnt = collections.defaultdict(lambda: [0, set(), 0])
        for who, rs in [('main', s['main']['results'])] + [(p['type'] + ':' + os.path.basename(p['file'])[6:12], p['results']) for p in s['subs']]:
            for r in rs:
                if r['name'] == 'Read':
                    fp = r['input'].get('file_path', '?')
                    cnt[fp][0] += 1; cnt[fp][1].add(who); cnt[fp][2] += r['chars']
        rep = [(fp, v) for fp, v in cnt.items() if v[0] > 1]
        rep.sort(key=lambda kv: -kv[1][2])
        if rep:
            P(f"-- {s['sid'][:8]} ({s['dir'][12:32]})")
            for fp, (n, who, ch) in rep[:8]:
                P(f"   {n}x by {len(who)} agents, {fmt(ch)} ch total | {fp.replace(HOME + '/', '~/')[-100:]}")
    P()
    # cross-session: most-read files overall
    P('# MOST-READ FILES OVERALL (count, total chars)')
    allc = collections.defaultdict(lambda: [0, 0, set()])
    for sid, who, r in allr:
        if r['name'] == 'Read':
            fp = r['input'].get('file_path', '?')
            allc[fp][0] += 1; allc[fp][1] += r['chars']; allc[fp][2].add(sid)
    for fp, (n, ch, ss) in sorted(allc.items(), key=lambda kv: -kv[1][1])[:20]:
        P(f"{n}x in {len(ss)} sessions, {fmt(ch)} ch | {fp.replace(HOME + '/', '~/')[-110:]}")
