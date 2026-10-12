"""plugins/marketing-os/hooks/studio-gate.py and its sh wrapper.

Run: python3 plugins/marketing-os/hooks/tests/test_studio_gate.py
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

HOOKS = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gate", HOOKS / "studio-gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

FAILS = []


def case(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


def d(tool, **args):
    return gate.decide({"tool_name": tool, "tool_input": args})[0]


P = "mcp__5889c975-5b1c-463c-83ce-b2001dfcdc78__"
COWORK = "mcp__meta-ads__"

# Every Meta Ads tool in the connected schema (2026-10-12), with what the gate must do.
ALLOWED = """ads_get_ad_accounts ads_get_ad_account_pages ads_get_ad_entities ads_get_ad_images ads_get_ad_preview
ads_get_ad_videos ads_get_creative_ads ads_get_creatives ads_get_custom_audience ads_get_custom_audience_adsets
ads_get_customconversions ads_get_dataset_details ads_get_dataset_quality ads_get_dataset_stats ads_get_datasets
ads_get_errors ads_get_field_context ads_get_help_article ads_get_ig_accounts ads_get_ig_media ads_get_opportunity_score
ads_get_pages_for_business ads_get_user_pages ads_get_ad_account_custom_audiences ads_insights_advertiser_context
ads_insights_anomaly_signal ads_insights_auction_ranking_benchmarks ads_insights_industry_benchmark
ads_insights_performance_trend ads_library_search ads_account_get_activity_logs ads_catalog_list_catalogs
ads_catalog_list_dpa_eligible_catalogs ads_catalog_list_partner_integrations ads_catalog_list_product_feeds
ads_catalog_list_product_sets ads_catalog_list_products ads_catalog_get_businesses ads_catalog_get_data_sources
ads_catalog_get_diagnostics ads_catalog_get_dynamic_ads_health ads_catalog_get_feed_rules
ads_catalog_get_product_feed_upload_sessions ads_catalog_get_suggested_filter ads_catalog_event_source_get
ads_catalog_event_source_get_catalogs ads_catalog_event_source_get_health ads_catalog_event_source_get_recommendations
ads_experiment_list_tests ads_experiment_check_eligibility ads_experiment_abtest_get_test ads_experiment_lift_get_test
ads_pixel_event_read ads_pixel_parameter_read ads_creative_upload_media ads_create_campaign ads_create_ad_set
ads_create_creative ads_create_ad""".split()
DENIED = """ads_activate_entity ads_boost_ig_post ads_catalog_create ads_catalog_create_feed_rule
ads_catalog_create_product_feed ads_catalog_create_product_feed_upload_session ads_catalog_create_product_set
ads_catalog_delete_product ads_catalog_event_source_connect ads_catalog_event_source_disconnect
ads_catalog_product_create ads_catalog_product_feed_delete ads_catalog_product_feed_delete_rule
ads_catalog_product_set_delete ads_catalog_update_catalog ads_catalog_update_product ads_catalog_update_product_feed
ads_catalog_update_product_set ads_create_custom_audience ads_creative_delete ads_creative_update
ads_delete_custom_audience ads_experiment_abtest_create_test ads_experiment_abtest_update_test
ads_experiment_lift_create_test ads_pixel_event_create ads_pixel_event_delete ads_pixel_event_update
ads_pixel_parameter_create ads_pixel_parameter_delete ads_pixel_parameter_update ads_update_custom_audience
ads_update_custom_audience_users ads_update_entity""".split()

case("every read and create tool is allowed", all(d(P + t) is None for t in ALLOWED))
case("every other write tool is denied", all(d(P + t) == "deny" for t in DENIED))
case("same with a Cowork prefix", d(COWORK + "ads_activate_entity") == "deny" and d(COWORK + "ads_create_ad") is None)
case("an unknown future tool is denied", d(P + "ads_publish_everything") == "deny" and d(P + "ads_create_anything") == "deny")
case("pause/delete asks", d(P + "ads_update_entity", fields='{"status":"DELETED"}') == "ask"
     and d(P + "ads_update_entity", fields={"status": "PAUSED"}) == "ask")
case("ACTIVE through update is denied", d(P + "ads_update_entity", fields='{"status":"ACTIVE"}') == "deny")
case("status plus budget is denied", d(P + "ads_update_entity", fields='{"status":"PAUSED","daily_budget":9999}') == "deny")
case("budget change is denied", d(P + "ads_update_entity", fields='{"daily_budget":100}') == "deny")

case("a create carrying a live status is denied", d(P + "ads_create_ad", status="ACTIVE") == "deny"
     and d(P + "ads_create_ad", adset_spec=json.dumps({"status": "ACTIVE"})) == "deny")
case("a create with a paused spec passes", d(P + "ads_create_ad_set", campaign_spec=json.dumps({"status": "PAUSED"})) is None)
case("ad copy that mentions a status passes", d(P + "ads_create_creative", message="the status: on sale now") is None)
case("a status update with extra arguments is denied",
     d(P + "ads_update_entity", fields={"status": "PAUSED"}, daily_budget=99999) == "deny")

C = "/mnt/c/Users/boazb/Documents/יזמות/wellness דיגטילי/שיווק/קמפיינים/נובמבר/campaign.json"
case("Write to campaign.json is denied", d("Write", file_path=C) == "deny" and d("Edit", file_path=C) == "deny")
case("Windows path too", d("Edit", file_path=C.replace("/", "\\")) == "deny")
case("// ./ and letter case do not slip past", d("Write", file_path=C.replace("/נובמבר/", "//נובמבר/./")) == "deny"
     and d("Write", file_path=C.replace("campaign.json", "Campaign.JSON")) == "deny"
     and d("Write", file_path=C.replace("/נובמבר/", "/x/../נובמבר/")) == "deny")
case("inbox and images are allowed", d("Write", file_path=C.replace("campaign.json", "inbox/1.json")) is None
     and d("Write", file_path=C.replace("campaign.json", "images/a.jpg")) is None)
case("other campaign.json files are allowed", d("Write", file_path="/tmp/x/campaign.json") is None)
case("device_commit_files to campaign.json is denied",
     d("mcp__remote-devices__device_commit_files", files=[{"devicePath": "C:\\Users\\b\\wellness\\שיווק\\קמפיינים\\x\\inbox\\1.json"},
                                                          {"devicePath": "C:\\Users\\b\\wellness\\שיווק\\קמפיינים\\x\\campaign.json"}]) == "deny")
case("device_commit_files of inbox only is allowed",
     d("mcp__remote-devices__device_commit_files", files=[{"devicePath": "C:\\w\\שיווק\\קמפיינים\\x\\inbox\\1.json"}]) is None)

case("curl to the board is denied", d("Bash", command="curl -X POST -H 'X-Studio: 1' http://127.0.0.1:8765/api/pack?c=a/b") == "deny")
case("python urllib to localhost:8770 is denied", d("Bash", command="python3 -c 'import urllib.request; urllib.request.urlopen(\"http://localhost:8770/\")'") == "deny")
case("device_bash too", d("mcp__remote-devices__device_bash", command="wget localhost:8765/api/ops") == "deny")
case("grep and tests are allowed", d("Bash", command="grep -n X-Studio studio.py && python3 tests/test_studio.py") is None)
case("curl elsewhere is allowed", d("Bash", command="curl https://example.com") is None)
case("other tools pass", d("Read", file_path=C) is None)

# the sh wrapper: python missing → Meta writes denied, reads and others pass
import shutil
import tempfile
NOPY = tempfile.mkdtemp(prefix="gate-nopy-")
for tool in ("cat", "dirname", "grep"):
    os.symlink(shutil.which(tool), os.path.join(NOPY, tool))
env = {"PATH": NOPY}
sh = "/bin/sh"


def wrap(tool):
    r = subprocess.run([sh, str(HOOKS / "studio-gate.sh")], input=json.dumps({"tool_name": tool, "tool_input": {}}),
                       capture_output=True, text=True, env=env)
    return r.stdout


case("no python: Meta create denied", '"deny"' in wrap(P + "ads_create_campaign"))
case("no python: Meta read passes", wrap(P + "ads_get_ad_accounts") == "")
case("no python: Write passes", wrap("Write") == "")
r = subprocess.run([sh, str(HOOKS / "studio-gate.sh")], input=json.dumps({"tool_name": P + "ads_activate_entity", "tool_input": {}}),
                   capture_output=True, text=True, env={**os.environ})
case("with python: wrapper relays the deny", '"deny"' in r.stdout)

print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
