#!/usr/bin/env node
// ביקורת מקומית של אתר מ-commit מסוים, לפני שהוא באוויר.
// מייצא את ה-commit (git archive — לא תיקיית העבודה), מגיש אותו על 127.0.0.1,
// חוסם כל בקשה החוצה ורושם אותה, ומחזיר: טקסט מוצג, טקסט מוסתר ובקוד, שדות טופס,
// קישורים, עוגיות, צילומים בגובה מסך אחד, ומה שהשתנה אחרי לחיצה או שליחת טופס.
// אפס עלות: playwright-core + Chromium מקומי, קוד פתוח.
//
// שימוש:
//   node site_review.mjs <repo> [--commit <sha|ref>] [--out <dir>] [--page <file.html>]...
//                               [--click "<selector>"]... [--submit-forms] [--no-shots]

import { spawnSync, execFileSync } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const DEPS_DIR = path.join(os.homedir(), '.cache', 'business-os', 'screen-check');
const VIEWPORTS = { mobile: { width: 390, height: 844, isMobile: true, hasTouch: true }, desktop: { width: 1440, height: 900 } };
const HEBREW = /[֐-׿]/;

function die(code, msg) { console.error(msg); process.exit(code); }

// ---------- ארגומנטים ----------
const args = process.argv.slice(2);
if (!args.length || args[0].startsWith('--')) die(1, 'שימוש: node site_review.mjs <repo> [--commit <ref>] [--out <dir>] [--page <file>] [--click <selector>] [--submit-forms] [--no-shots]');
const repo = path.resolve(args[0]);
const opt = { commit: 'HEAD', out: null, pages: [], clicks: [], submit: false, shots: true };
for (let i = 1; i < args.length; i++) {
  const a = args[i];
  if (a === '--commit') opt.commit = args[++i];
  else if (a === '--out') opt.out = args[++i];
  else if (a === '--page') opt.pages.push(args[++i]);
  else if (a === '--click') opt.clicks.push(args[++i]);
  else if (a === '--submit-forms') opt.submit = true;
  else if (a === '--no-shots') opt.shots = false;
  else die(1, `ארגומנט לא מוכר: ${a}`);
}

// ---------- ה-commit ----------
let sha;
try {
  sha = execFileSync('git', ['-C', repo, 'rev-parse', '--verify', `${opt.commit}^{commit}`], { encoding: 'utf8' }).trim();
} catch { die(1, `לא נמצא commit '${opt.commit}' ב-${repo}.`); }
const dirty = execFileSync('git', ['-C', repo, 'status', '--porcelain'], { encoding: 'utf8' }).trim();
const outDir = path.resolve(opt.out || path.join(os.tmpdir(), 'site-review', sha.slice(0, 12)));
fs.rmSync(outDir, { recursive: true, force: true });
fs.mkdirSync(outDir, { recursive: true });
const siteDir = fs.mkdtempSync(path.join(os.tmpdir(), 'site-review-src-'));
const tar = spawnSync('sh', ['-c', `git -C "$1" archive "$2" | tar -x -C "$3"`, 'sh', repo, sha, siteDir]);
if (tar.status !== 0) die(1, `ייצוא ה-commit נכשל: ${tar.stderr}`);

// ---------- דפדפן ותלויות ----------
function findBrowser() {
  const root = path.join(os.homedir(), '.cache', 'ms-playwright');
  if (fs.existsSync(root)) {
    for (const d of fs.readdirSync(root).filter((x) => x.startsWith('chromium')).sort().reverse()) {
      for (const rel of ['chrome-linux/chrome', 'chrome-headless-shell-linux64/chrome-headless-shell', 'chrome-linux64/chrome',
        'chrome-mac/Chromium.app/Contents/MacOS/Chromium', 'chrome-win/chrome.exe']) {
        const p = path.join(root, d, rel);
        if (fs.existsSync(p)) return p;
      }
    }
  }
  for (const b of ['chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable']) {
    const r = spawnSync('command', ['-v', b], { shell: true, encoding: 'utf8' });
    if (r.status === 0 && r.stdout.trim()) return r.stdout.trim();
  }
  return null;
}
const browserPath = findBrowser();
if (!browserPath) die(2, 'אין דפדפן בסביבה — הביקורת לא רצה. כותבים "לא נבדק: אין דפדפן בסביבה", לא "תקין". להתקנה: npx playwright install chromium');

const probe = path.join(DEPS_DIR, 'node_modules', 'playwright-core', 'index.mjs');
if (!fs.existsSync(probe)) {
  fs.mkdirSync(DEPS_DIR, { recursive: true });
  if (!fs.existsSync(path.join(DEPS_DIR, 'package.json'))) {
    fs.writeFileSync(path.join(DEPS_DIR, 'package.json'), JSON.stringify({ name: 'screen-check', private: true, type: 'module' }));
  }
  console.error('מתקין playwright-core (פעם אחת, קוד פתוח, ללא עלות)...');
  const r = spawnSync('npm', ['install', '--no-audit', '--no-fund', 'playwright-core', 'axe-core'], { cwd: DEPS_DIR, encoding: 'utf8' });
  if (r.status !== 0 || !fs.existsSync(probe)) die(2, 'לא הצלחתי להתקין את התלויות. הביקורת לא רצה — "לא נבדק".');
}
const { chromium } = await import(pathToFileURL(probe).href);

// ---------- שרת מקומי ----------
const TYPES = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp',
  '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.woff2': 'font/woff2', '.woff': 'font/woff', '.mp4': 'video/mp4' };
const server = http.createServer((req, res) => {
  let rel = decodeURIComponent((req.url || '/').split('?')[0]);
  if (rel.endsWith('/')) rel += 'index.html';
  const file = path.join(siteDir, path.normalize(rel));
  if (!file.startsWith(siteDir) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.statusCode = 404; return res.end(); }
  res.setHeader('Content-Type', TYPES[path.extname(file).toLowerCase()] || 'application/octet-stream');
  fs.createReadStream(file).pipe(res);
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const origin = `http://127.0.0.1:${server.address().port}`;

const pages = opt.pages.length ? opt.pages
  : fs.readdirSync(siteDir).filter((f) => f.toLowerCase().endsWith('.html')).sort().slice(0, 10);
if (!pages.length) die(1, 'אין קובץ HTML בשורש ה-commit. לתת --page <נתיב>.');

// ---------- מה קוראים מתוך הדף ----------
const READ_PAGE = `() => {
  const visible = (el) => { const s = getComputedStyle(el); const r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity) > 0 && (r.width > 0 || r.height > 0); };
  const clean = (t) => (t || '').replace(/\\s+/g, ' ').trim();
  const labelOf = (el) => {
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) return clean(l.innerText || l.textContent); }
    const p = el.closest('label'); if (p) return clean(p.innerText || p.textContent);
    return clean(el.getAttribute('aria-label') || '');
  };
  const hidden = new Set();
  for (const el of document.body.querySelectorAll('*')) {
    if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'SVG', 'PATH'].includes(el.tagName)) continue;
    const own = clean([...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(' '));
    if (own.length > 1 && !visible(el)) hidden.add(own);
  }
  for (const t of document.querySelectorAll('template')) { const s = clean(t.content.textContent); if (s) hidden.add(s); }
  const inlineScripts = [...document.querySelectorAll('script:not([src])')].map((s) => s.textContent).join('\\n');
  return {
    title: document.title,
    lang: document.documentElement.lang || null, dir: document.documentElement.dir || null,
    meta: [...document.querySelectorAll('meta[name], meta[property]')].map((m) => ({ key: m.getAttribute('name') || m.getAttribute('property'), value: m.getAttribute('content') })),
    visibleText: document.body.innerText,
    hiddenText: [...hidden].slice(0, 200),
    images: [...document.images].map((i) => ({ src: i.getAttribute('src'), alt: i.getAttribute('alt') })),
    forms: [...document.forms].map((f) => ({
      action: f.getAttribute('action'), method: f.getAttribute('method'),
      fields: [...f.elements].filter((e) => e.name || e.id).map((e) => ({ tag: e.tagName.toLowerCase(), type: e.type || null, name: e.name || e.id,
        required: !!e.required, label: labelOf(e), placeholder: e.getAttribute('placeholder'), checkedByDefault: e.type === 'checkbox' ? e.defaultChecked : undefined })),
      buttons: [...f.querySelectorAll('button, input[type=submit]')].map((b) => clean(b.innerText || b.value)),
    })),
    links: [...document.querySelectorAll('a[href]')].map((a) => ({ href: a.getAttribute('href'), text: clean(a.innerText).slice(0, 60) }))
      .filter((l) => !l.href.startsWith('#')),
    externalScripts: [...document.querySelectorAll('script[src]')].map((s) => s.getAttribute('src')),
    inlineScripts,
    localStorageKeys: Object.keys(localStorage),
  };
}`;

function scriptStrings(code) {
  const out = new Set();
  const re = /(["'`])((?:\\.|(?!\1)[^\\\n]){4,400})\1/g;
  let m;
  while ((m = re.exec(code))) if (HEBREW.test(m[2])) out.add(m[2].replace(/\s+/g, ' ').trim());
  return [...out];
}

function textDiff(before, after) {
  const seen = new Set(before.split('\n').map((l) => l.trim()).filter(Boolean));
  return after.split('\n').map((l) => l.trim()).filter((l) => l && !seen.has(l));
}

// ---------- ריצה ----------
const browser = await chromium.launch({ executablePath: browserPath });
const outbound = new Map();
const report = { commit: sha, repo, date: new Date().toISOString(), dirtyWorkingTree: !!dirty, pages: [], outbound: [], notChecked: [] };

for (const page of pages) {
  const entry = { page, viewports: {}, scriptStrings: [], clicks: [], submits: [], cookies: [] };
  for (const [vpName, vp] of Object.entries(VIEWPORTS)) {
    const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, isMobile: !!vp.isMobile, hasTouch: !!vp.hasTouch, locale: 'he-IL' });
    await ctx.route('**/*', (route) => {
      const u = new URL(route.request().url());
      if (u.hostname === '127.0.0.1') return route.continue();
      if (u.protocol === 'data:' || u.protocol === 'blob:') return route.continue();
      const key = `${route.request().method()} ${u.protocol}//${u.host}${u.pathname}`;
      outbound.set(key, route.request().resourceType());
      return route.abort();
    });
    const p = await ctx.newPage();
    const url = `${origin}/${page}`;
    try { await p.goto(url, { waitUntil: 'networkidle', timeout: 20000 }); }
    catch { await p.goto(url, { waitUntil: 'load', timeout: 20000 }).catch(() => {}); }
    const data = await p.evaluate(`(${READ_PAGE})()`);
    const shot = (name) => opt.shots ? p.screenshot({ path: path.join(outDir, name), type: 'jpeg', quality: 70 }).then(() => name) : null;
    entry.viewports[vpName] = { screenshot: await shot(`${page.replace(/\W+/g, '_')}-${vpName}.jpg`) };

    if (vpName === 'mobile') {
      Object.assign(entry, { title: data.title, lang: data.lang, dir: data.dir, meta: data.meta, visibleText: data.visibleText,
        hiddenText: data.hiddenText, images: data.images, forms: data.forms, links: data.links, externalScripts: data.externalScripts,
        localStorageKeys: data.localStorageKeys });
      entry.scriptStrings = scriptStrings(data.inlineScripts).filter((s) => !data.visibleText.includes(s));

      for (const [i, sel] of opt.clicks.entries()) {
        const before = await p.evaluate(() => document.body.innerText);
        try {
          await p.click(sel, { timeout: 5000 });
          await p.waitForTimeout(600);
          const after = await p.evaluate(() => document.body.innerText);
          entry.clicks.push({ selector: sel, newText: textDiff(before, after), screenshot: await shot(`${page.replace(/\W+/g, '_')}-click${i + 1}-mobile.jpg`) });
        } catch (e) { entry.clicks.push({ selector: sel, error: String(e.message || e).split('\n')[0] }); }
      }

      if (opt.submit) {
        for (let f = 0; f < data.forms.length; f++) {
          await p.goto(url, { waitUntil: 'load', timeout: 20000 }).catch(() => {});
          const before = await p.evaluate(() => document.body.innerText);
          const filled = await p.evaluate((fi) => {
            const form = document.forms[fi]; const done = [];
            for (const e of form.elements) {
              if (e.disabled || ['submit', 'button', 'hidden', 'file'].includes(e.type)) continue;
              if (e.type === 'checkbox' || e.type === 'radio') { if (e.required) { e.checked = true; done.push(e.name || e.id); } continue; }
              if (e.tagName === 'SELECT') { if (e.options.length > 1) e.selectedIndex = 1; continue; }
              const v = e.type === 'email' ? 'test@example.com' : e.type === 'tel' ? '0500000000' : e.type === 'number' ? '1' : 'בדיקה';
              e.value = v; e.dispatchEvent(new Event('input', { bubbles: true })); done.push(e.name || e.id);
            }
            const btn = form.querySelector('button[type=submit], input[type=submit], button:not([type])');
            if (btn) btn.click(); else form.requestSubmit();
            return done;
          }, f).catch((e) => ({ error: String(e.message || e) }));
          await p.waitForTimeout(1200);
          const after = await p.evaluate(() => document.body.innerText).catch(() => '');
          entry.submits.push({ form: f, filled, newText: textDiff(before, after), screenshot: await shot(`${page.replace(/\W+/g, '_')}-submit${f + 1}-mobile.jpg`) });
        }
      }
      entry.cookies = (await ctx.cookies()).map((c) => ({ name: c.name, domain: c.domain, expires: c.expires }));
    }
    await ctx.close();
  }
  report.pages.push(entry);
}
await browser.close();
server.close();
fs.rmSync(siteDir, { recursive: true, force: true });

report.outbound = [...outbound].map(([request, type]) => ({ request, type }));
report.notChecked = [
  'התנהגות באתר החי: קוד שנטען רק עם משתני סביבה של production (פיקסלים, אנליטיקס), כותרות HTTP, הפניות — נבדק רק בבדיקת אתר חי.',
  'בקשות החוצה נחסמו, אז תגובה של שירות חיצוני (טופס, סליקה, צ\'אט) לא נראתה.',
  'מצבים שלא ביקשו ללחוץ עליהם (--click) לא צולמו.',
  'Safari אמיתי ומכשיר אמיתי — הרצה ב-Chromium בלבד.',
  'נגישות — לא כאן. לעיצוב: design-os:check-screens. גם אז זו לא אישור עמידה בתקן.',
];
if (!opt.submit) report.notChecked.push('טפסים לא נשלחו (לא ניתן --submit-forms), אז הודעות אחרי שליחה לא נקראו.');

// ---------- דוח ----------
const md = [];
md.push(`# ביקורת מקומית — commit ${sha}`, '', `- repo: \`${repo}\``, `- תאריך: ${report.date}`, `- **נבדק מול: commit ${sha} (טיוטה)**`);
if (dirty) md.push('- ⚠️ בתיקיית העבודה יש שינויים שלא נכנסו ל-commit — **הם לא נבדקו**. נבדק רק ה-commit.');
md.push('', '## בקשות החוצה (נחסמו)', report.outbound.length ? report.outbound.map((o) => `- \`${o.request}\` (${o.type})`).join('\n') : 'אין.');
for (const e of report.pages) {
  md.push('', `## ${e.page}`, '', `- title: ${e.title || '—'} · lang: ${e.lang || '—'} · dir: ${e.dir || '—'}`);
  md.push(`- צילומים: ${Object.values(e.viewports).map((v) => v.screenshot).filter(Boolean).join(', ') || 'לא צולם'}`);
  md.push('', '### טקסט מוצג (נייד)', '', '```', e.visibleText || '', '```');
  md.push('', '### טקסט שלא מוצג בטעינה', e.hiddenText.length ? e.hiddenText.map((t) => `- ${t}`).join('\n') : 'אין.');
  md.push('', '### טקסט בתוך קוד הדף שלא מוצג בטעינה', e.scriptStrings.length ? e.scriptStrings.map((t) => `- ${t}`).join('\n') : 'אין.');
  md.push('', '### טפסים');
  if (!e.forms.length) md.push('אין.');
  for (const [i, f] of e.forms.entries()) {
    md.push(`- טופס ${i + 1} · action: \`${f.action || '—'}\` · method: ${f.method || '—'} · כפתורים: ${f.buttons.join(' / ') || '—'}`);
    for (const fl of f.fields) md.push(`  - ${fl.label || fl.name} (${fl.tag}${fl.type ? '/' + fl.type : ''})${fl.required ? ' · חובה' : ''}${fl.checkedByDefault ? ' · **מסומן מראש**' : ''}`);
  }
  md.push('', '### קישורים', e.links.length ? e.links.map((l) => `- ${l.text || '(בלי טקסט)'} → \`${l.href}\``).join('\n') : 'אין.');
  md.push('', '### סקריפטים חיצוניים', e.externalScripts.length ? e.externalScripts.map((s) => `- \`${s}\``).join('\n') : 'אין.');
  md.push('', '### עוגיות ו-localStorage', e.cookies.length ? e.cookies.map((c) => `- ${c.name} @ ${c.domain}`).join('\n') : 'אין עוגיות.', e.localStorageKeys.length ? `localStorage: ${e.localStorageKeys.join(', ')}` : '');
  if (e.clicks.length) {
    md.push('', '### אחרי לחיצה');
    for (const c of e.clicks) md.push(`- \`${c.selector}\`: ${c.error ? 'נכשל — ' + c.error : (c.newText.length ? c.newText.join(' | ') : 'לא נוסף טקסט')}${c.screenshot ? ' · ' + c.screenshot : ''}`);
  }
  if (e.submits.length) {
    md.push('', '### אחרי שליחת טופס (בקשות החוצה חסומות)');
    for (const s of e.submits) md.push(`- טופס ${s.form + 1}: ${s.newText.length ? s.newText.join(' | ') : 'לא נוסף טקסט'}${s.screenshot ? ' · ' + s.screenshot : ''}`);
  }
}
md.push('', '## מה לא נבדק', ...report.notChecked.map((n) => `- ${n}`));
fs.writeFileSync(path.join(outDir, 'report.md'), md.join('\n'));
fs.writeFileSync(path.join(outDir, 'report.json'), JSON.stringify(report, null, 2));
console.log(`נבדק מול: commit ${sha}\nדוח: ${path.join(outDir, 'report.md')}`);
