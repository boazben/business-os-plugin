"""marketing-os studio gate: keeps Claude's hands off real money and off the founder's choices.

Reads a PreToolUse payload on stdin and prints a decision. Three rules:

1. Meta Ads tools (any MCP server prefix, so Cowork too). An allow list: reading, previews, uploading media and
   creating campaigns, ad sets, creatives and ads (the connected tools create them paused). Everything else under
   `__ads_` is denied — turning a campaign on (`ads_activate_entity`), budget and targeting changes, audiences, pixels,
   catalogs, boosts, and any tool Meta adds later. One exception asks instead of denying: `ads_update_entity` whose
   only field is a status of PAUSED, ARCHIVED or DELETED. That spends nothing, and the founder confirms in one click.
   Turning a campaign on is done by the founder in Ads Manager, never here.
2. campaign.json under `שיווק/קמפיינים/<slug>/` is written only by the studio server. Write/Edit/MultiEdit and
   Cowork's `device_commit_files` to it are denied; Claude sends a patch to the campaign's inbox/ instead
   (`studio.py patch`, or a new file in inbox/ from Cowork). Images and inbox files are allowed.
3. Bash and Cowork's `device_bash` may not call the board server over HTTP (curl, wget, python urllib…) — that is
   how the board packs and records the founder's picks.

Rules 2 and 3 guard against mistakes, not against a model set on getting around them. The money does not depend on
them: nothing goes live until the founder turns it on in Ads Manager, and rule 1 keeps that switch out of reach.

Tests: hooks/tests/test_studio_gate.py.
"""
import json
import posixpath
import re
import sys

ALLOW_ADS = re.compile(
    r"^(get_[a-z_]+|insights_[a-z_]+|library_search|account_get_activity_logs|catalog_(list|get)_[a-z_]+"
    r"|catalog_event_source_get[a-z_]*|experiment_(list_tests|check_eligibility|abtest_get_test|lift_get_test)"
    r"|pixel_(event|parameter)_read|creative_upload_media|create_campaign|create_ad_set|create_creative|create_ad)$")
SAFE_STATUS = {"PAUSED", "ARCHIVED", "DELETED"}
CAMPAIGN_FILE = re.compile(r"(^|/)שיווק/קמפיינים/[^/]+/campaign\.json$")
# A create call may not carry a live status anywhere in its arguments (some take nested specs as JSON strings).
LIVE_STATUS = re.compile(r'\\?"(status|configured_status|effective_status)\\?"\s*:\s*\\?"(ACTIVE|ON|LIVE)\\?"', re.I)
# The only arguments ads_update_entity may carry for an "ask": which object, the fields, and the tool's bookkeeping.
UPDATE_ARGS = {"ad_account_id", "entity_id", "entity_type", "fields", "client_conversation_id", "client_model",
               "advertiser_job", "advertiser_request", "opId", "verbose"}


def _campaign_file(path):
    """Normalised the way the file system will see it: / separators, no // or ./ or ../, any letter case
    (/mnt/c is case-insensitive)."""
    norm = posixpath.normpath(str(path or "").replace("\\", "/")).casefold()
    return bool(CAMPAIGN_FILE.search(norm))
HTTP_CLIENT = re.compile(r"\b(curl|wget|Invoke-WebRequest|Invoke-RestMethod|urllib|http\.client|requests|fetch\()")
BOARD_PORT = re.compile(r"(127\.0\.0\.1|localhost|0\.0\.0\.0|\[::1\]):87(6[5-9]|7[0-9]|8[0-4])\b")

MONEY = ("פעולה ב-Meta שלא מותרת ל-Claude. הפעלת קמפיין, שינוי תקציב או קהל של קמפיין קיים, וכל מה שמוציא כסף, "
         "נעשים רק על ידי המייסד במנהל המודעות. Claude יוצר קמפיינים מושהים בלבד.")
FILE = ("את campaign.json כותב רק לוח הקמפיינים. שלח בקשת שינוי: `studio.py patch <תיקיית הקמפיין> <ops.json>`, "
        "או מ-Cowork קובץ חדש ב-inbox/ של הקמפיין ({\"format\": 1, \"ops\": [...]}).")
BOARD = ("Claude לא פונה ללוח הקמפיינים ישירות. האריזה והבחירות שייכות למייסד בלוח; שינויים נשלחים דרך "
         "`studio.py patch`.")


def decide(payload):
    """(decision, reason) — decision is None (allow), "deny" or "ask"."""
    tool = str(payload.get("tool_name") or "")
    args = payload.get("tool_input") or {}

    if "__ads_" in tool:
        suffix = tool.rsplit("__ads_", 1)[1]
        if ALLOW_ADS.match(suffix):
            if suffix.startswith("create_") and LIVE_STATUS.search(json.dumps(args, ensure_ascii=False)):
                return "deny", MONEY
            return None, ""
        if suffix == "update_entity" and set(args) <= UPDATE_ARGS:
            fields = args.get("fields")
            try:
                fields = json.loads(fields) if isinstance(fields, str) else fields
            except ValueError:
                fields = None
            if isinstance(fields, dict) and set(fields) == {"status"} and str(fields["status"]).upper() in SAFE_STATUS:
                return "ask", f"Claude מבקש לשנות סטטוס ל-{fields['status']} (לא מוציא כסף). לאשר?"
        return "deny", MONEY

    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        if _campaign_file(args.get("file_path") or args.get("notebook_path")):
            return "deny", FILE
        return None, ""

    if tool.endswith("device_commit_files"):
        for f in args.get("files") or []:
            if _campaign_file((f or {}).get("devicePath")):
                return "deny", FILE
        return None, ""

    if tool == "Bash" or tool.endswith("device_bash"):
        cmd = str(args.get("command") or "")
        if HTTP_CLIENT.search(cmd) and BOARD_PORT.search(cmd):
            return "deny", BOARD
        return None, ""

    return None, ""


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}
    decision, reason = decide(payload)
    if decision:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                                 "permissionDecisionReason": f"marketing-os — {reason}"}},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
