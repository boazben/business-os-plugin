"""The verdict the business-os board gate gives each Notion call.

Run: python3 plugins/business-os/hooks/tests/test_board_gate.py
Every case sends a real PreToolUse event to board-gate.sh (the hook entry) and reads the verdict off
its stdout: "ok" (silent, ordinary board work), "warn" (goes through, the founder is told and the
call is recorded) or "ask" (he confirms in one click). The gate does not block; a "blocked" verdict
would be a bug. Two tool families: the Notion connector (flat values) and a local Notion MCP server
exposing the REST API (nested values). When the gate misreads a call, add the case here before fixing it.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "board-gate.sh"
CONNECTOR = "mcp__claude_ai_Notion__notion-"
REST = "mcp__notion__API-"
failures = []


def run(tool, tool_input, raw=None):
    """The gate's verdict: "ok", "warn", "ask", or "blocked" if it ever blocks."""
    data = raw if raw is not None else json.dumps({"tool_name": tool, "tool_input": tool_input}, ensure_ascii=False)
    env = dict(os.environ, HOME=HOME)
    p = subprocess.run(["sh", str(HOOK)], input=data.encode(), capture_output=True, env=env)
    if p.returncode == 2:
        return "blocked"
    out = p.stdout.decode().strip()
    if not out:
        return "ok"
    try:
        decision = json.loads(out)
    except ValueError:
        return f"unparseable output: {out[:80]}"
    hook = decision.get("hookSpecificOutput") or {}
    if hook.get("permissionDecision") == "ask":
        return "ask"
    return "warn" if decision.get("systemMessage") else "ok"


def case(name, want, got):
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {got}, want {want}"))
    if not ok:
        failures.append(name)


def status(value):
    return {"סטטוס": {"status": {"name": value}}}


def main():
    board = {"type": "data_source_id", "data_source_id": "x"}

    print("== connector: ordinary board work, silent")
    case("status to בעבודה", "ok", run(CONNECTOR + "update-page", {"page_id": "p", "properties": {"סטטוס": "בעבודה"}}))
    case("status to ממתין לאישור", "ok", run(CONNECTOR + "update-page", {"page_id": "p", "properties": {"סטטוס": "ממתין לאישור"}}))
    case("status to בעבודה on a returned task", "ok", run(CONNECTOR + "update-page", {"page_id": "p", "properties": {"סטטוס": "בעבודה", "תוצאה": "סבב 2: נלקח"}}))
    case("result quotes the founder's reply", "ok", run(CONNECTOR + "update-page", {"page_id": "p", "properties": {"תוצאה": "סבב 2: קוצר לפי מה שבעז כתב בתגובת מייסד"}}))
    case("result text mentions approval", "ok", run(CONNECTOR + "update-page", {"page_id": "p", "properties": {"תוצאה": "legal-lead: עובר. brand-guardian approved the copy."}}))
    case("create a row under the board", "ok", run(CONNECTOR + "create-pages", {"parent": board, "pages": [{"properties": {"שם": "x", "סטטוס": "לביצוע"}}]}))
    case("query", "ok", run(CONNECTOR + "query-data-sources", {"data": {"query": "select * where סטטוס = 'מאושר'"}}))
    case("fetch", "ok", run(CONNECTOR + "fetch", {"id": "p"}))
    case("comment", "ok", run(CONNECTOR + "create-comment", {"page_id": "p", "text": "approved by legal? not yet"}))

    print("== connector: through, but the founder is told and it is recorded")
    case("status to מאושר", "warn", run(CONNECTOR + "update-page", {"page_id": "p", "properties": {"סטטוס": "מאושר"}}))
    case("status to לסבב נוסף", "warn", run(CONNECTOR + "update-page", {"page_id": "p", "properties": {"סטטוס": "לסבב נוסף"}}))
    case("write תגובת מייסד", "warn", run(CONNECTOR + "update-page", {"page_id": "p", "properties": {"תגובת מייסד": "תקצר את זה"}}))
    case("write תגובת מייסד alongside an allowed field", "warn", run(CONNECTOR + "update-page", {"page_id": "p", "properties": {"סטטוס": "בעבודה", "תגובת מייסד": ""}}))
    case("clear תגובת מייסד", "warn", run(CONNECTOR + "update-page", {"page_id": "p", "properties": {"תגובת מייסד": None}}))
    case("create a row with a founder reply", "warn", run(CONNECTOR + "create-pages", {"parent": board, "pages": [{"properties": {"תגובת מייסד": "בסדר"}}]}))
    case("status to מאושר with niqqud", "warn", run(CONNECTOR + "update-page", {"page_id": "p", "properties": {"סטטוס": "מְאֻשָּׁר"}}))
    case("status to Approved", "warn", run(CONNECTOR + "update-page", {"page_id": "p", "properties": {"Status": "Approved ✅"}}))
    case("other property exactly מאושר", "warn", run(CONNECTOR + "update-page", {"page_id": "p", "properties": {"שלב": "מאושר"}}))
    case("status nested inside the connector call", "warn", run(CONNECTOR + "update-page", {"page_id": "p", "properties": status("מאושר")}))
    case("properties as a JSON string", "warn", run(CONNECTOR + "update-page", {"page_id": "p", "properties": json.dumps({"סטטוס": "מאושר"}, ensure_ascii=False)}))
    case("create a page without parent", "warn", run(CONNECTOR + "create-pages", {"pages": [{"properties": {"title": "x"}}]}))
    case("create a row already approved", "warn", run(CONNECTOR + "create-pages", {"parent": board, "pages": [{"properties": {"סטטוס": "מאושר"}}]}))
    case("create database", "warn", run(CONNECTOR + "create-database", {}))
    case("update data source (add a column or an option)", "warn", run(CONNECTOR + "update-data-source", {}))
    case("move pages", "warn", run(CONNECTOR + "move-pages", {}))
    case("duplicate page", "warn", run(CONNECTOR + "duplicate-page", {}))

    print("== connector: one click first")
    case("Notion AI session", "ask", run(CONNECTOR + "spawn-session", {}))
    case("Notion AI message", "ask", run(CONNECTOR + "send-message-to-session", {}))

    print("== REST (local Notion MCP): ordinary board work, silent")
    case("patch status to בעבודה", "ok", run(REST + "patch-page", {"page_id": "p", "properties": status("בעבודה")}))
    case("patch result rich text mentioning approval", "ok", run(REST + "patch-page", {"page_id": "p", "properties": {"תוצאה": {"rich_text": [{"text": {"content": "מחכה לאישור של בעז"}}]}}}))
    case("post page under the board", "ok", run(REST + "post-page", {"parent": {"data_source_id": "x"}, "properties": status("לביצוע")}))
    case("query a data source", "ok", run(REST + "query-data-source", {"data_source_id": "x", "filter": {"property": "סטטוס", "status": {"equals": "מאושר"}}}))
    case("retrieve a page", "ok", run(REST + "retrieve-a-page", {"page_id": "p"}))
    case("append blocks", "ok", run(REST + "patch-block-children", {"block_id": "p", "children": [{"paragraph": {"rich_text": [{"text": {"content": "approved"}}]}}]}))
    case("copy the founder's reply into the page body", "ok", run(REST + "patch-block-children", {"block_id": "p", "children": [{"heading_2": {"rich_text": [{"text": {"content": "סבב 2"}}]}}, {"paragraph": {"rich_text": [{"text": {"content": "תגובת מייסד (17.9): קצר מדי, תרחיב"}}]}}]}))

    print("== REST (local Notion MCP): through, told and recorded")
    case("patch status to מאושר", "warn", run(REST + "patch-page", {"page_id": "p", "properties": status("מאושר")}))
    case("patch select to מאושר", "warn", run(REST + "patch-page", {"page_id": "p", "properties": {"סטטוס": {"select": {"name": "מאושר"}}}}))
    case("patch status by option id only", "warn", run(REST + "patch-page", {"page_id": "p", "properties": {"Status": {"status": {"id": "abc1"}}}}))
    case("patch תגובת מייסד as rich text", "warn", run(REST + "patch-page", {"page_id": "p", "properties": {"תגובת מייסד": {"rich_text": [{"text": {"content": "לא זה"}}]}}}))
    case("patch תגובת מייסד with niqqud in the name", "warn", run(REST + "patch-page", {"page_id": "p", "properties": {"תְּגוּבַת מְיַיסֵּד": {"rich_text": []}}}))
    case("patch Founder Note", "warn", run(REST + "patch-page", {"page_id": "p", "properties": {"Founder Note": {"rich_text": [{"text": {"content": "x"}}]}}}))
    case("patch תגובת מייסד by property id", "warn", run(REST + "patch-page", {"page_id": "p", "properties": {"תגובת מייסד (BOS)": {"rich_text": []}}}))
    case("patch other property exactly approved", "warn", run(REST + "patch-page", {"page_id": "p", "properties": {"שלב": {"rich_text": [{"text": {"content": "approved"}}]}}}))
    case("post page already approved", "warn", run(REST + "post-page", {"parent": {"data_source_id": "x"}, "properties": status("מאושר")}))
    case("post page without parent", "warn", run(REST + "post-page", {"properties": {"title": []}}))
    case("create database", "warn", run(REST + "create-a-database", {}))
    case("update database", "warn", run(REST + "update-a-database", {}))
    case("update data source (add a column or an option)", "warn", run(REST + "update-a-data-source", {}))
    case("move page", "warn", run(REST + "move-page", {}))
    case("tool input as a JSON string", "warn", run(REST + "patch-page", json.dumps({"page_id": "p", "properties": status("מאושר")}, ensure_ascii=False)))

    print("== REST (local Notion MCP): one click first")
    case("trash a page", "ask", run(REST + "patch-page", {"page_id": "p", "in_trash": True}))
    case("archive a page", "ask", run(REST + "patch-page", {"page_id": "p", "archived": True}))
    case("trash a page the connector way", "ask", run(CONNECTOR + "update-page", {"page_id": "p", "in_trash": True}))

    print("== the check itself fails: through with a warning, one click for the rest")
    case("unparseable write event", "warn", run("", None, raw='{"tool_name": "mcp__notion__API-patch-page", "tool_input": {"properties": "מאושר"'))
    case("unparseable read event", "ok", run("", None, raw='{"tool_name": "mcp__notion__API-retrieve-a-page", "tool_input": {'))
    case("unparseable trash event", "ask", run("", None, raw='{"tool_name": "mcp__notion__API-patch-page", "tool_input": {"in_trash": true'))

    print("== the record")
    log = Path(HOME) / ".business-os" / "board-log.md"
    rows = log.read_text(encoding="utf-8").splitlines()[2:] if log.exists() else []
    case("every warn and ask left a row", True, len(rows) >= 30)
    case("no row for silent board work", True, all("| ok |" not in r for r in rows))


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as home:
        HOME = home
        main()
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)
