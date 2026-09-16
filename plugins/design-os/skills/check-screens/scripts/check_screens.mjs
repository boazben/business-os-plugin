#!/usr/bin/env node
// בודק מסכי HTML בדפדפן אמיתי: axe, ניגודיות מרונדרת, זום, עוגנים,
// כללי CSS לפי תכונה, סדר Tab, וצילומי מסך שהסוכן מסתכל עליהם.
// אפס עלות: Chromium של Playwright + axe-core, שניהם קוד פתוח.

import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { pathToFileURL } from 'node:url';

const DEPS_DIR = path.join(os.homedir(), '.cache', 'business-os', 'screen-check');
const VIEWPORTS = [
  { name: 'mobile', width: 375, height: 812 },
  { name: 'tablet', width: 768, height: 1024 },
  { name: 'desktop', width: 1280, height: 900 },
];
const ZOOMS = [2, 4]; // WCAG 1.4.4 (200%) ו-1.4.10 (400%)
const TAP_MIN = 44;

function die(code, msg) {
  console.error(msg);
  process.exit(code);
}

// ---------- ארגומנטים ----------
const args = process.argv.slice(2);
if (!args.length || args.includes('--help')) {
  console.log(`שימוש:
  node check_screens.mjs <קובץ.html | תיקייה> [...] [--out <תיקייה>] [--no-shots]

מחזיר דוח טקסט + report.json + צילומי מסך. קוד יציאה 2 = לא רץ (אין דפדפן),
ואז כותבים במסירה "לא נבדק" — לא "תקין".`);
  process.exit(0);
}
let outDir = null;
let shots = true;
const targets = [];
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--out') outDir = args[++i];
  else if (args[i] === '--no-shots') shots = false;
  else targets.push(args[i]);
}

const files = [];
for (const t of targets) {
  const abs = path.resolve(t);
  if (!fs.existsSync(abs)) die(2, `לא נמצא: ${abs}`);
  if (fs.statSync(abs).isDirectory()) {
    for (const f of fs.readdirSync(abs).sort()) if (f.endsWith('.html')) files.push(path.join(abs, f));
  } else files.push(abs);
}
if (!files.length) die(2, 'לא נמצאו קובצי HTML לבדיקה.');
outDir = outDir ? path.resolve(outDir) : path.join(path.dirname(files[0]), 'screen-check');

// ---------- דפדפן ----------
function findBrowser() {
  const root = path.join(os.homedir(), '.cache', 'ms-playwright');
  if (fs.existsSync(root)) {
    for (const d of fs.readdirSync(root).filter((x) => x.startsWith('chromium')).sort().reverse()) {
      for (const rel of ['chrome-linux/chrome', 'chrome-mac/Chromium.app/Contents/MacOS/Chromium', 'chrome-win/chrome.exe']) {
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
if (!browserPath) {
  die(2, `אין דפדפן בסביבה הזו — הבדיקה לא רצה.
במסירה כותבים "לא נבדק: אין דפדפן בסביבה", לא "תקין".
להתקנה מקומית: npx playwright install chromium`);
}

// ---------- תלויות ----------
function ensureDeps() {
  const probe = path.join(DEPS_DIR, 'node_modules', 'axe-core', 'axe.min.js');
  if (fs.existsSync(probe)) return;
  fs.mkdirSync(DEPS_DIR, { recursive: true });
  fs.writeFileSync(path.join(DEPS_DIR, 'package.json'), JSON.stringify({ name: 'screen-check', private: true, type: 'module' }));
  console.error('מתקין playwright-core ו-axe-core (פעם אחת, קוד פתוח, ללא עלות)...');
  const r = spawnSync('npm', ['install', '--no-audit', '--no-fund', 'playwright-core', 'axe-core'], {
    cwd: DEPS_DIR, encoding: 'utf8', stdio: ['ignore', 'ignore', 'pipe'],
  });
  if (r.status !== 0 || !fs.existsSync(probe)) {
    die(2, `לא הצלחתי להתקין את התלויות (אין רשת?). הבדיקה לא רצה — "לא נבדק".\n${(r.stderr || '').split('\n').slice(0, 3).join('\n')}`);
  }
}
ensureDeps();

const { chromium } = await import(pathToFileURL(path.join(DEPS_DIR, 'node_modules', 'playwright-core', 'index.mjs')).href);
const AXE = fs.readFileSync(path.join(DEPS_DIR, 'node_modules', 'axe-core', 'axe.min.js'), 'utf8');

// ---------- בדיקות שרצות בתוך הדף ----------
const IN_PAGE = `() => {
  const out = { anchors: [], attrRules: [], taps: [], tabOrder: [], overflow: null, lang: null };
  out.lang = { lang: document.documentElement.lang || null, dir: document.documentElement.dir || null };

  // עוגנים פנימיים: קיימים? ומצביעים ליעד ייחודי?
  const links = [...document.querySelectorAll('a[href^="#"]')];
  const seen = {};
  for (const a of links) {
    const href = a.getAttribute('href');
    const id = href.slice(1);
    const text = (a.textContent || '').trim().slice(0, 40);
    const empty = href === '#';
    const missing = !empty && !document.getElementById(id);
    (seen[href] ||= []).push(text);
    out.anchors.push({ href, text, empty, missing });
  }
  out.duplicateAnchors = Object.entries(seen)
    .filter(([h, t]) => h !== '#' && t.length > 1)
    .map(([href, texts]) => ({ href, texts }));

  // כלל CSS שחל לפי תכונה — מה הצבע שיוצא בפועל
  for (const el of document.querySelectorAll('[aria-disabled="true"], [aria-busy="true"], [disabled]')) {
    const cs = getComputedStyle(el);
    out.attrRules.push({
      tag: el.tagName.toLowerCase(),
      text: (el.textContent || '').trim().slice(0, 30),
      ariaBusy: el.getAttribute('aria-busy'),
      ariaDisabled: el.getAttribute('aria-disabled'),
      disabled: el.hasAttribute('disabled'),
      color: cs.color, background: cs.backgroundColor, opacity: cs.opacity, cursor: cs.cursor,
    });
  }

  // גודל מגע — רק פקדים. קישור בתוך שורת טקסט פטור (WCAG 2.5.8).
  for (const el of document.querySelectorAll('a[href], button, input, select, textarea, [role="button"], [tabindex]:not([tabindex="-1"])')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    const cs = getComputedStyle(el);
    if (el.tagName === 'A' && cs.display === 'inline') continue;
    if (r.width < ${TAP_MIN} || r.height < ${TAP_MIN}) {
      out.taps.push({ tag: el.tagName.toLowerCase(), text: (el.textContent || el.value || '').trim().slice(0, 30), w: Math.round(r.width), h: Math.round(r.height) });
    }
  }

  // ניגודיות של טקסט שקוף — axe לא מכריע עליו, וזה בדיוק מה שנשבר
  const parse = (c) => { const m = c.match(/[\\d.]+/g); return m ? m.slice(0, 3).map(Number).concat(m[3] !== undefined ? Number(m[3]) : 1) : null; };
  const over = (fg, bg, a) => fg.map((v, i) => v * a + bg[i] * (1 - a));
  const lum = (c) => { const s = c.map((v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * s[0] + 0.7152 * s[1] + 0.0722 * s[2]; };
  const bgOf = (el) => {
    for (let n = el; n && n !== document.documentElement; n = n.parentElement) {
      const c = parse(getComputedStyle(n).backgroundColor);
      if (c && c[3] > 0.95) return c.slice(0, 3);
    }
    return [255, 255, 255];
  };
  for (const el of document.querySelectorAll('*')) {
    const t = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!t) continue;
    const cs = getComputedStyle(el);
    let a = 1;
    for (let n = el; n && n !== document.documentElement; n = n.parentElement) a *= Number(getComputedStyle(n).opacity || 1);
    const fg = parse(cs.color);
    if (!fg) continue;
    const alpha = a * (fg[3] ?? 1);
    if (alpha > 0.98) continue; // axe כבר מכסה טקסט אטום
    const bg = bgOf(el);
    const eff = over(fg.slice(0, 3), bg, alpha);
    const L1 = lum(eff), L2 = lum(bg);
    const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const px = parseFloat(cs.fontSize);
    const bold = Number(cs.fontWeight) >= 700;
    const min = px >= 24 || (bold && px >= 18.66) ? 3 : 4.5;
    if (ratio < min) {
      out.alphaContrast ||= [];
      out.alphaContrast.push({ text: (el.textContent || '').trim().slice(0, 28), ratio: Math.round(ratio * 100) / 100, min, opacity: Math.round(alpha * 100) / 100, px });
    }
  }

  // סדר Tab לפי סדר ה-DOM
  const focusables = [...document.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])')]
    .filter((el) => el.offsetParent !== null || getComputedStyle(el).position === 'fixed');
  out.tabOrder = focusables.map((el) => ({ tag: el.tagName.toLowerCase(), text: (el.textContent || el.getAttribute('aria-label') || el.value || '').trim().slice(0, 32) }));

  out.overflow = { scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth };
  return out;
}`;

// ---------- הרצה ----------
fs.mkdirSync(outDir, { recursive: true });
const browser = await chromium.launch({ executablePath: browserPath });
const report = { ranAt: new Date().toISOString(), browser: browserPath, files: [] };

for (const file of files) {
  const slug = path.basename(file, '.html');
  const entry = { file, findings: [], screenshots: [], notChecked: [] };
  const url = pathToFileURL(file).href;

  for (const vp of VIEWPORTS) {
    const page = await browser.newPage({ viewport: { width: vp.width, height: vp.height } });
    const consoleErrors = [];
    page.on('pageerror', (e) => consoleErrors.push(String(e).slice(0, 120)));
    await page.goto(url, { waitUntil: 'load' });

    const data = await page.evaluate(`(${IN_PAGE})()`);

    if (vp.name === 'mobile') {
      if (!data.lang.lang || !data.lang.dir) {
        entry.findings.push({ level: 'בינוני', rule: 'lang/dir', where: '<html>', msg: `חסר lang או dir (lang=${data.lang.lang}, dir=${data.lang.dir}) — קורא מסך לא יודע באיזו שפה לקרוא` });
      }
      for (const d of data.duplicateAnchors) {
        entry.findings.push({ level: 'בינוני', rule: 'anchor-duplicate', where: d.href, msg: `${d.texts.length} קישורים שונים מצביעים לאותו יעד: ${d.texts.join(' | ')} — לפחות אחד מהם שגוי` });
      }
      for (const a of data.anchors.filter((x) => x.missing)) {
        entry.findings.push({ level: 'חוסם', rule: 'anchor-missing', where: a.href, msg: `"${a.text}" מצביע ליעד שלא קיים בדף` });
      }
      const emptyLinks = data.anchors.filter((x) => x.empty);
      if (emptyLinks.length) {
        entry.findings.push({ level: 'קל', rule: 'anchor-empty', where: 'href="#"', msg: `${emptyLinks.length} קישורים ריקים: ${emptyLinks.map((x) => x.text).filter(Boolean).slice(0, 6).join(' | ')}` });
      }
      for (const r of data.attrRules) {
        if (r.ariaBusy === 'true' && r.ariaDisabled === 'true') {
          entry.findings.push({ level: 'בינוני', rule: 'attr-css-collision', where: `<${r.tag}> "${r.text}"`, msg: `טעינה (aria-busy) יחד עם aria-disabled — כלל ה-CSS של "מושבת" חל עליו. צבע בפועל: ${r.color} על ${r.background}` });
        }
      }
      for (const t of data.taps) {
        entry.findings.push({ level: 'בינוני', rule: 'tap-target', where: `<${t.tag}> "${t.text}"`, msg: `${t.w}×${t.h}px — מתחת ל-${TAP_MIN}px` });
      }
      for (const c of data.alphaContrast || []) {
        entry.findings.push({ level: 'בינוני', rule: 'contrast-alpha', where: `"${c.text}"`, msg: `${c.ratio}:1 מול מינימום ${c.min}:1 — הצבע המרונדר אחרי opacity ${c.opacity} (${c.px}px). axe לא מכריע על זה.` });
      }
      entry.tabOrder = data.tabOrder;
      if (consoleErrors.length) entry.findings.push({ level: 'קל', rule: 'js-error', where: 'console', msg: consoleErrors[0] });
    }

    // גלילה אופקית ברוחב הזה
    if (data.overflow.scrollWidth > data.overflow.clientWidth + 1) {
      entry.findings.push({ level: 'בינוני', rule: 'h-scroll', where: `${vp.width}px`, msg: `גלילה אופקית: תוכן ${data.overflow.scrollWidth}px ברוחב ${data.overflow.clientWidth}px` });
    }

    // axe — ניגודיות מרונדרת (כולל opacity), ARIA אסור, תוויות חסרות
    if (vp.name === 'mobile') {
      await page.addScriptTag({ content: AXE });
      const res = await page.evaluate(async () => await window.axe.run(document, { runOnly: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] }));
      for (const v of res.violations) {
        const level = v.impact === 'critical' || v.impact === 'serious' ? 'בינוני' : 'קל';
        for (const n of v.nodes.slice(0, 4)) {
          entry.findings.push({ level, rule: `axe:${v.id}`, where: n.target.join(' '), msg: (n.failureSummary || v.help).replace(/\s+/g, ' ').slice(0, 220) });
        }
        if (v.nodes.length > 4) entry.findings.push({ level, rule: `axe:${v.id}`, where: '…', msg: `ועוד ${v.nodes.length - 4} מקומות` });
      }
      entry.axeIncomplete = res.incomplete.map((i) => i.id);
    }

    // זום — רק במובייל, שם זה נשבר
    if (vp.name === 'mobile') {
      for (const z of ZOOMS) {
        await page.evaluate((zz) => { document.documentElement.style.zoom = String(zz); }, z);
        await page.waitForTimeout(80);
        const o = await page.evaluate(() => ({ s: document.documentElement.scrollWidth, c: document.documentElement.clientWidth }));
        if (o.s > o.c + 1) {
          entry.findings.push({ level: 'בינוני', rule: 'zoom-h-scroll', where: `זום ${z * 100}%`, msg: `גלילה אופקית בזום ${z * 100}% — WCAG ${z === 2 ? '1.4.4' : '1.4.10'}` });
        }
        if (shots && z === 2) {
          const p = path.join(outDir, `${slug}-zoom200.png`);
          await page.screenshot({ path: p });
          entry.screenshots.push(p);
        }
      }
      await page.evaluate(() => { document.documentElement.style.zoom = ''; });
    }

    if (shots && (vp.name === 'mobile' || vp.name === 'desktop')) {
      const p = path.join(outDir, `${slug}-${vp.name}.png`);
      await page.screenshot({ path: p, fullPage: vp.name === 'mobile' });
      entry.screenshots.push(p);
    }
    await page.close();
  }

  entry.notChecked = ['קורא מסך אמיתי (NVDA/VoiceOver)', 'מכשיר אמיתי במגע', 'טעם ועקביות מול ספר המותג'];
  report.files.push(entry);
}
await browser.close();

// ---------- פלט ----------
fs.writeFileSync(path.join(outDir, 'report.json'), JSON.stringify(report, null, 2));

const order = { 'חוסם': 0, 'בינוני': 1, 'קל': 2 };
let total = 0;
for (const f of report.files) {
  console.log(`\n=== ${path.basename(f.file)} ===`);
  if (!f.findings.length) console.log('  אין ממצאים אוטומטיים.');
  for (const x of f.findings.sort((a, b) => order[a.level] - order[b.level])) {
    console.log(`  [${x.level}] ${x.rule} — ${x.where}\n      ${x.msg}`);
    total++;
  }
  if (f.tabOrder?.length) {
    console.log(`  סדר Tab: ${f.tabOrder.map((t) => t.text || t.tag).slice(0, 12).join(' → ')}${f.tabOrder.length > 12 ? ' → …' : ''}`);
  }
  if (f.axeIncomplete?.length) console.log(`  axe לא הכריע (צריך עין): ${f.axeIncomplete.join(', ')}`);
  if (f.screenshots.length) console.log(`  צילומים: ${f.screenshots.map((s) => path.basename(s)).join(', ')}`);
}

console.log(`\nסה"כ ${total} ממצאים · דוח: ${path.join(outDir, 'report.json')}`);
console.log(`\n⚠️ ריצה נקייה היא לא אישור עמידה בתקן. בדיקה אוטומטית תופסת חלק מכשלי
הנגישות בלבד. חובה גם: להסתכל על הצילומים, ולכתוב במסירה מה לא נבדק —
קורא מסך אמיתי, מכשיר אמיתי, והתאמה למותג.`);
