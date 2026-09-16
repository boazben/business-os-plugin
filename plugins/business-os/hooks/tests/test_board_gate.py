"""Tries the known ways around the business-os board gate, and the board work it must keep allowing.

Run: python3 plugins/business-os/hooks/tests/test_board_gate.py
Every case sends a real PreToolUse event to board-gate.sh (the hook entry) and checks the exit code
(2 = blocked). Two tool families: the Notion connector (flat values) and a local Notion MCP server
exposing the REST API (nested values). When a new bypass is found, add it here before fixing the hook.
"""
import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "board-gate.sh"
CONNECTOR = "mcp__claude_ai_Notion__notion-"
REST = "mcp__notion__API-"
failures = []


def run(tool, tool_input, raw=None):
    data = raw if raw is not None else json.dumps({"tool_name": tool, "tool_input": tool_input}, ensure_ascii=False)
    p = subprocess.run(["sh", str(HOOK)], input=data.encode(), capture_output=True)
    return p.returncode


def case(name, want, got):
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> exit {got}, want {want}"))
    if not ok:
        failures.append(name)


def status(value):
    return {"סטטוס": {"status": {"name": value}}}


def main():
    board = {"type": "data_source_id", "data_source_id": "x"}

    print("== connector: board work stays allowed")
    case("status to בעבודה", 0, run(CONNECTOR + "update-page", {"page_id": "p", "properties": {"סטטוס": "בעבודה"}}))
    case("status to ממתין לאישור", 0, run(CONNECTOR + "update-page", {"page_id": "p", "properties": {"סטטוס": "ממתין לאישור"}}))
    case("result text mentions approval", 0, run(CONNECTOR + "update-page", {"page_id": "p", "properties": {"תוצאה": "legal-lead: עובר. brand-guardian approved the copy."}}))
    case("create a row under the board", 0, run(CONNECTOR + "create-pages", {"parent": board, "pages": [{"properties": {"שם": "x", "סטטוס": "לביצוע"}}]}))
    case("query", 0, run(CONNECTOR + "query-data-sources", {"data": {"query": "select * where סטטוס = 'מאושר'"}}))
    case("fetch", 0, run(CONNECTOR + "fetch", {"id": "p"}))
    case("comment", 0, run(CONNECTOR + "create-comment", {"page_id": "p", "text": "approved by legal? not yet"}))

    print("== connector: blocked")
    case("status to מאושר", 2, run(CONNECTOR + "update-page", {"page_id": "p", "properties": {"סטטוס": "מאושר"}}))
    case("status to מאושר with niqqud", 2, run(CONNECTOR + "update-page", {"page_id": "p", "properties": {"סטטוס": "מְאֻשָּׁר"}}))
    case("status to Approved", 2, run(CONNECTOR + "update-page", {"page_id": "p", "properties": {"Status": "Approved ✅"}}))
    case("other property exactly מאושר", 2, run(CONNECTOR + "update-page", {"page_id": "p", "properties": {"שלב": "מאושר"}}))
    case("status nested inside the connector call", 2, run(CONNECTOR + "update-page", {"page_id": "p", "properties": status("מאושר")}))
    case("properties as a JSON string", 2, run(CONNECTOR + "update-page", {"page_id": "p", "properties": json.dumps({"סטטוס": "מאושר"}, ensure_ascii=False)}))
    case("create a page without parent", 2, run(CONNECTOR + "create-pages", {"pages": [{"properties": {"title": "x"}}]}))
    case("create a row already approved", 2, run(CONNECTOR + "create-pages", {"parent": board, "pages": [{"properties": {"סטטוס": "מאושר"}}]}))
    case("create database", 2, run(CONNECTOR + "create-database", {}))
    case("update data source", 2, run(CONNECTOR + "update-data-source", {}))
    case("move pages", 2, run(CONNECTOR + "move-pages", {}))
    case("duplicate page", 2, run(CONNECTOR + "duplicate-page", {}))
    case("Notion AI session", 2, run(CONNECTOR + "spawn-session", {}))

    print("== REST (local Notion MCP): board work stays allowed")
    case("patch status to בעבודה", 0, run(REST + "patch-page", {"page_id": "p", "properties": status("בעבודה")}))
    case("patch result rich text mentioning approval", 0, run(REST + "patch-page", {"page_id": "p", "properties": {"תוצאה": {"rich_text": [{"text": {"content": "מחכה לאישור של בעז"}}]}}}))
    case("post page under the board", 0, run(REST + "post-page", {"parent": {"data_source_id": "x"}, "properties": status("לביצוע")}))
    case("query a data source", 0, run(REST + "query-data-source", {"data_source_id": "x", "filter": {"property": "סטטוס", "status": {"equals": "מאושר"}}}))
    case("retrieve a page", 0, run(REST + "retrieve-a-page", {"page_id": "p"}))
    case("append blocks", 0, run(REST + "patch-block-children", {"block_id": "p", "children": [{"paragraph": {"rich_text": [{"text": {"content": "approved"}}]}}]}))

    print("== REST (local Notion MCP): blocked")
    case("patch status to מאושר", 2, run(REST + "patch-page", {"page_id": "p", "properties": status("מאושר")}))
    case("patch select to מאושר", 2, run(REST + "patch-page", {"page_id": "p", "properties": {"סטטוס": {"select": {"name": "מאושר"}}}}))
    case("patch status by option id only", 2, run(REST + "patch-page", {"page_id": "p", "properties": {"Status": {"status": {"id": "abc1"}}}}))
    case("patch other property exactly approved", 2, run(REST + "patch-page", {"page_id": "p", "properties": {"שלב": {"rich_text": [{"text": {"content": "approved"}}]}}}))
    case("post page already approved", 2, run(REST + "post-page", {"parent": {"data_source_id": "x"}, "properties": status("מאושר")}))
    case("post page without parent", 2, run(REST + "post-page", {"properties": {"title": []}}))
    case("trash a page", 2, run(REST + "patch-page", {"page_id": "p", "in_trash": True}))
    case("archive a page", 2, run(REST + "patch-page", {"page_id": "p", "archived": True}))
    case("create database", 2, run(REST + "create-a-database", {}))
    case("update database", 2, run(REST + "update-a-database", {}))
    case("update data source", 2, run(REST + "update-a-data-source", {}))
    case("move page", 2, run(REST + "move-page", {}))
    case("tool input as a JSON string", 2, run(REST + "patch-page", json.dumps({"page_id": "p", "properties": status("מאושר")}, ensure_ascii=False)))

    print("== the check itself fails: fallback blocks approval words")
    case("unparseable event mentioning מאושר", 2, run("", None, raw='{"tool_name": "mcp__notion__API-patch-page", "tool_input": {"properties": "מאושר"'))
    case("unparseable event without approval words", 0, run("", None, raw='{"tool_name": "mcp__notion__API-retrieve-a-page", "tool_input": {'))


if __name__ == "__main__":
    main()
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)
