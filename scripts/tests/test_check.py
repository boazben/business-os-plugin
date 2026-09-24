"""scripts/check.py: gate anchors in the persona, and the English read-path denylist.

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
r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check.py")], capture_output=True, text=True)
check("the repo as it is passes (exit 0)", r.returncode == 0)

print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
