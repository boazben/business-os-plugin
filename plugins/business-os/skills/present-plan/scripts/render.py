#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render a plan dashboard from a JSON content file.

Why this exists: the HTML scaffolding (table rows, checklist items, placeholder
substitution, copying the Mermaid bundle) is identical on every run. Doing it in
a script instead of hand-writing HTML each time keeps the output consistent and
keeps the model's attention on extracting the plan rather than on markup.

Usage:
    python3 render.py content.json -o plan-preview.html [--inline]

content.json:
{
  "title":   "short Hebrew title",
  "meta":    "repo · branch · date · source",
  "summary": "<p>...</p><p>...</p>",          raw HTML
  "mermaid": "flowchart TD\\n  A[\\"...\\"] --> B",
  "impact":  [{"component":"app.py","now":"...","change":"...",
               "badge":"mod","why":"..."}],
  "modules": [{"summary":"module name","badge":"new","badge_text":"3 קבצים",
               "open": false, "body":"<h4>...</h4><ul>...</ul>"}],
  "verification": [{"label":"...","desc":"...","cmd":"pytest"}],
  "footer":  "optional"
}
Badge values: new | mod | del | info | risk
"""
import argparse
import html
import json
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets"
BADGE_DEFAULT = {"new": "חדש", "mod": "שינוי", "del": "מחיקה",
                 "info": "מידע", "risk": "סיכון"}


def badge(kind, text=None):
    if not kind:
        return ""
    kind = kind if kind in BADGE_DEFAULT else "info"
    return f'<span class="badge {kind}">{text or BADGE_DEFAULT[kind]}</span>'


def impact_rows(items):
    out = []
    for it in items:
        out.append(
            '          <tr>\n'
            f'            <td class="component"><code>{it["component"]}</code></td>\n'
            f'            <td>{it.get("now", "")}</td>\n'
            f'            <td>{badge(it.get("badge"), it.get("badge_text"))} {it.get("change", "")}</td>\n'
            f'            <td>{it.get("why", "")}</td>\n'
            '          </tr>'
        )
    return "\n".join(out)


def module_blocks(items):
    out = []
    for it in items:
        tag = "<details open>" if it.get("open") else "<details>"
        out.append(
            f'  {tag}\n'
            f'    <summary>{it["summary"]} {badge(it.get("badge"), it.get("badge_text"))}</summary>\n'
            f'    <div class="details-body">\n{it.get("body", "")}\n    </div>\n'
            '  </details>'
        )
    return "\n\n".join(out)


def checklist(items):
    out = []
    for it in items:
        cmd = it.get("cmd")
        cmd_html = f'\n          <span class="cmd">{html.escape(cmd)}</span>' if cmd else ""
        out.append(
            '      <li>\n'
            '        <input type="checkbox">\n'
            '        <div>\n'
            f'          <div class="c-label">{it["label"]}</div>\n'
            f'          <div class="c-desc">{it.get("desc", "")}</div>{cmd_html}\n'
            '        </div>\n'
            '      </li>'
        )
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("content", help="path to the JSON content file")
    ap.add_argument("-o", "--out", default="plan-preview.html")
    ap.add_argument("--inline", action="store_true",
                    help="embed mermaid.min.js in the HTML (one ~5.5MB file "
                         "instead of two, for sending the preview to someone)")
    args = ap.parse_args()

    data = json.loads(pathlib.Path(args.content).read_text(encoding="utf-8"))
    tpl = (ASSETS / "template.html").read_text(encoding="utf-8")

    fields = {
        "{{TITLE}}": data["title"],
        "{{META}}": data.get("meta", ""),
        "{{SUMMARY}}": data["summary"],
        "{{MERMAID}}": data["mermaid"],
        "{{IMPACT_ROWS}}": impact_rows(data.get("impact", [])),
        "{{MODULES}}": module_blocks(data.get("modules", [])),
        "{{VERIFICATION}}": checklist(data.get("verification", [])),
        "{{FOOTER}}": data.get("footer", "נוצר על ידי /visualize-plan"),
    }
    for k, v in fields.items():
        tpl = tpl.replace(k, v)

    out = pathlib.Path(args.out).resolve()
    bundle = ASSETS / "mermaid.min.js"

    if args.inline:
        tpl = tpl.replace('<script src="./mermaid.min.js"></script>',
                          "<script>" + bundle.read_text(encoding="utf-8") + "</script>")
    else:
        shutil.copy(bundle, out.parent / "mermaid.min.js")

    out.write_text(tpl, encoding="utf-8")

    leftover = [k for k in fields if k in tpl]
    if leftover:
        print(f"אזהרה: placeholders לא הוחלפו: {leftover}", file=sys.stderr)

    print(out)


if __name__ == "__main__":
    main()
