"""plugins/design-os/hooks/gemini-key-guard.py: which tool calls reach the Gemini or Notion key.

Run: python3 plugins/design-os/hooks/tests/test_key_guard.py
Fake keys are built at run time, so this file itself holds nothing key-shaped.
"""
import json
import pathlib
import subprocess
import sys

HOOK = pathlib.Path(__file__).resolve().parents[1] / "gemini-key-guard.py"
NOTION = "ntn" + "_" + "a1B2" * 11
OLD_NOTION = "secret" + "_" + "c3D4" * 11
GOOGLE = "AI" + "za" + "x" * 35
TOKEN_FILE = "notion" + "-token"
failures = []


def exit_code(tool, tool_input):
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"tool_name": tool, "tool_input": tool_input}),
                       capture_output=True, text=True)
    return r.returncode


def check(name, tool, tool_input, want):
    got = exit_code(tool, tool_input)
    ok = got == want
    if not ok:
        failures.append(name)
    print(("ok   " if ok else "FAIL ") + f"{name} (exit {got}, want {want})")


check("cat of the Notion key file", "Bash", {"command": f"cat ~/.business-os/{TOKEN_FILE}"}, 2)
check("Read of the Notion key file", "Read", {"file_path": f"/x/.business-os/{TOKEN_FILE}"}, 2)
check("Notion key value in a command", "Bash", {"command": f"curl -H 'Authorization: Bearer {NOTION}'"}, 2)
check("Notion key value written to a file", "Write", {"file_path": "/x/config.js", "content": f"k='{NOTION}'"}, 2)
check("old-style Notion key in an edit", "Edit", {"file_path": "/x/a.py", "old_string": "a", "new_string": OLD_NOTION}, 2)
check("Notion key in a Cowork device shell", "mcp__remote-devices__device_bash", {"command": f"echo {NOTION}"}, 2)
check("grep for the Notion prefix", "Grep", {"pattern": "ntn" + "_", "path": "/home"}, 2)
check("Google browser key may be written to front-end code", "Write", {"file_path": "/x/site.js", "content": GOOGLE}, 0)
check("Google key value in a command", "Bash", {"command": f"echo {GOOGLE}"}, 2)
check("running orient is allowed", "Bash", {"command": 'python3 orient.py --folder "w" --venture "/v"'}, 0)
check("reading the ledger is allowed", "Bash", {"command": "cat ~/.business-os/ledger.md"}, 0)
check("staging the project folder is allowed", "mcp__remote-devices__device_stage_files", {"paths": ["משפטי"]}, 0)

print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
