"""scripts/check.py: gate anchors in the persona, the English read-path denylist, and the shared prompt sections.

Run: python3 scripts/tests/test_check.py
"""
import importlib.util
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check", ROOT / "scripts" / "check.py")
ck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ck)
failures = []


def check(name, cond):
    if not cond:
        failures.append(name)
    print(("ok   " if cond else "FAIL ") + name)


persona = (ROOT / "CEO-PERSONA.md").read_text(encoding="utf-8")
check("the current persona has every anchor", ck.check_persona(persona) == [])
check("the current tree has no English read path back", ck.check_read_paths() == [])
first = ck.anchors()[0]
check("dropping a gate sentence is caught", any(first in f for f in ck.check_persona(persona.replace(first, "…"))))
check("a line break inside an anchor is still a match", ck.check_persona(persona.replace(first, first.replace(" ", "\n", 1))) == [])
check("comment lines are not anchors", all(not a.startswith("#") for a in ck.anchors()))

base = (ROOT / "scripts" / "persona-anchors.txt").read_text(encoding="utf-8")
dropped_list = base.replace(first + "\n", "")
fails = ck.check_persona(persona.replace(first, "…"), dropped_list, base, "")
check("rule + its anchor dropped together is still caught", any("removed without" in f for f in fails))
check("…unless the founder's OK is recorded", ck.check_persona(persona.replace(first, "…"), dropped_list, base, first + "\n") == [])
check("an emptied anchor list fails", any("shorter than" in f for f in ck.check_persona(persona, "# none\n")))
check("an anchor only in the leading '>' note does not count",
      any(first in f for f in ck.check_persona("> " + first + "\n\n" + persona.replace(first, "…"))))
check("a BOM does not turn the comment into an anchor", ck.parse("\ufeff# c\nx\n") == ["x"])
fails = ck.check_persona(persona.replace(first, "…"), None, base + base, "")
check("a base anchor is reported once", sum(first in f for f in fails) == 1)

with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
    fh.write("# persona without gates\n")
r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check.py"), "--persona", fh.name], capture_output=True, text=True)
check("--persona on a gutted file exits 1 with reasons", r.returncode == 1 and "gate anchor missing" in r.stderr)

# 3. shared prompt sections (BOS-49 C9/C10)
check("the current tree's shared sections match their sources", ck.check_shared() == [])


def tree(**changes):
    """A reader over the working tree with some files replaced (None = absent)."""
    def read(rel):
        if rel in changes:
            return changes[rel]
        path = ROOT / rel
        return path.read_text(encoding="utf-8") if path.exists() else None
    return read


legal_lead = (ROOT / "plugins/legal-os/agents/legal-lead.md").read_text(encoding="utf-8")
fails = ck.check_shared(tree(**{"plugins/legal-os/agents/legal-lead.md": legal_lead.replace("תקרה: 3 סבבי ביקורת", "תקרה: 5 סבבי ביקורת")}))
check("a head's copy edited alone is caught", any("legal-lead.md: shared section differs" in f for f in fails))
heading = (ROOT / ck.HEAD_RULES).read_text(encoding="utf-8").splitlines()[0]
fails = ck.check_shared(tree(**{"plugins/legal-os/agents/legal-lead.md": legal_lead.replace(heading, "## משהו אחר")}))
check("a head without the section is caught", any("legal-lead.md: shared section missing" in f for f in fails))
verifier = (ROOT / "plugins/legal-os/agents/legal-verifier.md").read_text(encoding="utf-8")
contract_heading = (ROOT / ck.REVIEWER_CONTRACT).read_text(encoding="utf-8").splitlines()[0]
fails = ck.check_shared(tree(**{"plugins/legal-os/agents/legal-verifier.md": verifier.replace(contract_heading, "## פלט")}))
check("a reviewer without the verdict contract is caught", any("legal-verifier.md: shared section missing" in f for f in fails))
report = (ROOT / ck.LEDGER_REPORT).read_text(encoding="utf-8")
fails = ck.check_shared(tree(**{ck.LEDGER_REPORT: report.replace('"brand-guardian", ', "")}))
check("an agent with the contract that report.py does not check is caught", any("REVIEWERS lacks brand-guardian" in f for f in fails))
fails = ck.check_shared(tree(**{ck.HEAD_RULES: None}))
check("a missing source is caught", any("head-review-rules.md: missing" in f for f in fails))

r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check.py")], capture_output=True, text=True)
check("the repo as it is passes (exit 0)", r.returncode == 0)

check("the current tree's frontmatter parses", ck.check_frontmatter() == [])
agent = "plugins/supply-os/agents/supply-lead.md"
text = (ROOT / agent).read_text(encoding="utf-8")
bad = text.replace("description: ", "description: ראש המחלקה: ", 1)
check("a ': ' in an unquoted description is caught",
      any("supply-lead.md: frontmatter 'description'" in f for f in ck.check_frontmatter(lambda rel: bad if rel == agent else (ROOT / rel).read_text(encoding="utf-8"))))
def with_description(value):
    lines = text.split("\n")
    i = next(k for k, l in enumerate(lines) if l.startswith("description: "))
    lines[i] = "description: " + value
    changed = "\n".join(lines)
    return ck.check_frontmatter(lambda rel: changed if rel == agent else (ROOT / rel).read_text(encoding="utf-8"))
check("a well-formed quoted value with ': ' is fine", with_description('"ראש המחלקה: בודק"') == [])
check("a quoted value broken by a quote inside it is caught", any("broken quoted value" in f for f in with_description('"חו"ל: x"')))
check("a single-quoted value with a doubled quote is fine", with_description("'it''s: fine'") == [])
print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
