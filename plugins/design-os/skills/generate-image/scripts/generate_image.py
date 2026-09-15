#!/usr/bin/env python3
"""Generate or edit an image with Nano Banana (Gemini) and count it against the
business-os monthly image budget.

Budget rules (founder decision, 2026-09-15): alert at 90% and 100% of the
monthly budget, refuse to generate past 130%. The budget defaults to $10 — the
Google Cloud credit bundled with the founder's Google AI Pro subscription.

Every run reserves its estimated cost in the spend log before calling Google and
settles the difference afterwards, so a killed or timed-out run is still
counted. Costs are estimated from Google's published prices, not read from the
invoice. Nothing about the prompt is logged.

Where the budget and spend log live:
- a real machine (HOME under /home, /Users, /mnt, C:\\): ~/.business-os/
- a sandbox such as Cowork, whose HOME is wiped per session: <working folder>/.business-os/
  when the working folder is outside HOME and /tmp (a mounted project folder);
  otherwise paid generation is refused, because spend could not be remembered.
BUSINESS_OS_STATE_DIR overrides both (tests; the design-os hook blocks agents from setting it).

Usage:
    generate_image.py --prompt "..." --out design/assets/x/a.png [--model flash|pro|lite]
                      [--size 1K|2K|4K] [--aspect 1:1] [--ref img.png ...]
    generate_image.py --status

Exit codes: 0 ok, 2 error, 3 blocked by budget (or no place to keep the meter).
"""
import argparse
import base64
import http.client
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

try:
    import fcntl
except ImportError:  # Windows: no lock, the budget check still runs
    fcntl = None

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
DEFAULT_BUDGET_USD = 10.0
ALERT_LEVEL = 0.9
BLOCK_LEVEL = 1.3
REAL_MACHINE_HOME_RE = re.compile(r"^(/home/|/mnt/|/Users/|/c/|/[A-Za-z]/Users/|[A-Za-z]:\\)")

# Official Gemini API paid-tier prices, checked 2026-09-15: per output image, and
# per 1M tokens for input and for text/thinking output.
MODELS = {
    "pro": {"id": "gemini-3-pro-image", "image": {"1K": 0.134, "2K": 0.134, "4K": 0.24},
            "input_per_m": 2.00, "text_per_m": 12.00, "thinking_allowance": 0.015},
    "flash": {"id": "gemini-3.1-flash-image", "image": {"1K": 0.067, "2K": 0.101, "4K": 0.151},
              "input_per_m": 0.50, "text_per_m": 3.00, "thinking_allowance": 0.004},
    "lite": {"id": "gemini-3.1-flash-lite-image", "image": {"1K": 0.0336},
             "input_per_m": 0.25, "text_per_m": 1.50, "thinking_allowance": 0.002},
}
# Used only when the response carries no usage block.
REF_ALLOWANCE_USD = 0.003
MAX_REFS = 14
ASPECTS = ("1:1", "3:2", "2:3", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
OUT_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def fail(message, code=2) -> NoReturn:
    print(message, file=sys.stderr)
    sys.exit(code)


def real_home():
    try:
        import pwd
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, AttributeError):
        return Path.home()


def state_dir():
    """Folder for the budget and spend log, or None when nothing would persist."""
    override = os.environ.get("BUSINESS_OS_STATE_DIR")
    if override:
        return Path(override)
    home = real_home()
    if REAL_MACHINE_HOME_RE.match(str(home)):
        return home / ".business-os"
    cwd = Path.cwd().resolve()
    if cwd != home and home not in cwd.parents and cwd.parts[:2] != ("/", "tmp"):
        return cwd / ".business-os"
    return None


def load_budget(state):
    budget_file = state / "image-budget.json"
    if not budget_file.exists():
        return DEFAULT_BUDGET_USD
    try:
        value = float(json.loads(budget_file.read_text())["monthly_budget_usd"])
        if value > 0:
            return value
    except (OSError, ValueError, KeyError, TypeError):
        pass
    print(f"קובץ התקציב {budget_file} לא תקין — משתמשים בברירת המחדל {DEFAULT_BUDGET_USD:.2f}$.",
          file=sys.stderr)
    return DEFAULT_BUDGET_USD


def month_key():
    return datetime.now(timezone.utc).strftime("%Y-%m")


def month_spend(state, month):
    total, images = 0.0, 0
    try:
        lines = (state / "image-spend.jsonl").read_text().splitlines()
    except FileNotFoundError:
        return total, images
    for line in lines:
        try:
            record = json.loads(line)
            if record.get("month") == month:
                total += float(record.get("cost_usd") or 0)
                images += int(record.get("images") or 0)
        except (ValueError, TypeError, AttributeError):
            continue
    return total, images


def budget_line(spent, budget):
    summary = f"{spent:.2f}$ מתוך {budget:.2f}$ החודש ({spent / budget * 100:.0f}%)"
    if spent >= budget * BLOCK_LEVEL:
        return (f"⛔ תקציב תמונות: {summary} — הגענו ל-130%. יצירת תמונות בתשלום חסומה "
                "עד סוף החודש, או עד שהמייסד מגדיל את התקציב.")
    if spent >= budget:
        return (f"⚠️ תקציב תמונות: {summary} — עברנו את התקציב החודשי. "
                f"חסימה ב-130% ({budget * BLOCK_LEVEL:.2f}$).")
    if spent >= budget * ALERT_LEVEL:
        return f"⚠️ תקציב תמונות: {summary} — עברנו 90% מהתקציב החודשי."
    return f"תקציב תמונות: {summary}."


@contextmanager
def spend_lock(state):
    state.mkdir(parents=True, exist_ok=True)
    if fcntl is None:
        yield
        return
    with open(state / "image-spend.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def append_record(state, record):
    record = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **record}
    with open(state / "image-spend.jsonl", "a") as log:
        log.write(json.dumps(record) + "\n")


def model_output_items(response, kind):
    """Items of one type from the model's final output — not thoughts, not echoed input."""
    for step in response.get("steps") or []:
        if isinstance(step, dict) and step.get("type") == "model_output":
            for item in step.get("content") or []:
                if isinstance(item, dict) and item.get("type") == kind:
                    yield item


def token_cost(usage, model):
    """Input, thinking and text-output cost from the usage block, or None if absent."""
    if not isinstance(usage, dict):
        return None
    try:
        text_tokens = sum(float(m.get("tokens") or 0) for m in usage.get("output_tokens_by_modality") or []
                          if str(m.get("modality", "")).lower() == "text")
        return (float(usage.get("total_input_tokens") or 0) * model["input_per_m"]
                + (float(usage.get("total_thought_tokens") or 0) + text_tokens) * model["text_per_m"]) / 1e6
    except (TypeError, ValueError, AttributeError):
        return None


def api_error_message(body):
    try:
        data = json.loads(body)
        error = data.get("error", data)
        return str(error.get("message") or error.get("status") or error.get("code") or body[:300])
    except (ValueError, AttributeError):
        return body[:300]


class ApiError(Exception):
    def __init__(self, message, billed_maybe):
        super().__init__(message)
        self.billed_maybe = billed_maybe


def call_api(key, payload):
    """Return the Interaction; raise ApiError, noting whether Google may have billed."""
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        message = f"ה-API של Google החזיר שגיאה {exc.code}: {api_error_message(exc.read().decode(errors='replace'))}"
        raise ApiError(message, billed_maybe=exc.code >= 500) from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException, OSError) as exc:
        raise ApiError(f"אין תשובה מלאה מ-Google ({exc}).", billed_maybe=True) from exc
    except ValueError as exc:
        raise ApiError("ה-API של Google החזיר תשובה שאינה JSON.", billed_maybe=True) from exc
    if not isinstance(data, dict):
        raise ApiError("ה-API של Google החזיר תשובה לא צפויה.", billed_maybe=True)
    return data


def build_payload(args, model_id):
    parts = [{"type": "text", "text": args.prompt}]
    for ref in args.ref:
        path = Path(ref)
        mime = mimetypes.guess_type(path.name)[0]
        if not path.is_file() or not (mime or "").startswith("image/"):
            fail(f"קובץ ייחוס לא קיים או שאינו תמונה: {ref}")
        parts.append({"type": "image", "mime_type": mime, "data": base64.b64encode(path.read_bytes()).decode()})
    response_format = {"type": "image", "mime_type": OUT_MIME[Path(args.out).suffix.lower()], "aspect_ratio": args.aspect}
    if args.size != "1K":
        response_format["image_size"] = args.size
    return {"model": model_id, "input": parts, "response_format": response_format}


def save_images(images, out):
    out.parent.mkdir(parents=True, exist_ok=True)
    expected = OUT_MIME[out.suffix.lower()]
    saved = []
    for index, image in enumerate(images):
        path = out if index == 0 else out.with_name(f"{out.stem}-{index + 1}{out.suffix}")
        path.write_bytes(base64.b64decode(image["data"]))
        if image.get("mime_type") and image["mime_type"] != expected:
            print(f"שים לב: Google החזיר {image['mime_type']}, והקובץ נשמר כ-{path.suffix}.", file=sys.stderr)
        saved.append(path)
    return saved


def no_meter_message():
    return ("⛔ תקציב תמונות: אין כאן מקום שבו אפשר לזכור את ההוצאות בין שיחות (למשל Cowork בלי תיקיית "
            "פרויקט מחוברת). לא נוצרה תמונה. פותחים את השיחה מתוך תיקיית ה-venture, או ממשיכים בכלים חינמיים.")


def status():
    state = state_dir()
    if state is None:
        print(no_meter_message())
        return
    budget = load_budget(state)
    spent, images = month_spend(state, month_key())
    print(f"החודש ({month_key()}, UTC): {images} תמונות. מונה: {state}")
    print(budget_line(spent, budget))


def generate(args):
    model = MODELS[args.model]
    prices = model["image"]
    if args.size not in prices:
        fail(f"המודל {args.model} לא תומך בגודל {args.size}. גדלים אפשריים: {', '.join(prices)}.")
    if Path(args.out).suffix.lower() not in OUT_MIME:
        fail(f"סיומת קובץ לא נתמכת: {args.out} (png, jpg, webp).")
    if len(args.ref) > MAX_REFS:
        fail(f"יותר מדי קבצי ייחוס ({len(args.ref)}); המקסימום {MAX_REFS}.")
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        fail("חסר משתנה הסביבה GEMINI_API_KEY — המייסד צריך להגדיר אותו. לא נוצרה תמונה.")
    state = state_dir()
    if state is None:
        print(no_meter_message())
        sys.exit(3)

    payload = build_payload(args, model["id"])
    fallback_tokens = REF_ALLOWANCE_USD * len(args.ref) + model["thinking_allowance"]
    estimate = prices[args.size] + fallback_tokens
    budget = load_budget(state)
    month, run_id = month_key(), uuid.uuid4().hex

    with spend_lock(state):
        spent, _ = month_spend(state, month)
        if spent + estimate > budget * BLOCK_LEVEL:
            print(f"⛔ תקציב תמונות: {spent:.2f}$ מתוך {budget:.2f}$ החודש — התמונה הזו (~{estimate:.3f}$) "
                  f"הייתה עוברת את 130% ({budget * BLOCK_LEVEL:.2f}$). לא נוצרה תמונה. "
                  "המשך רק בכלים חינמיים, או שהמייסד מגדיל את התקציב.")
            sys.exit(3)
        append_record(state, {"month": month, "kind": "reserve", "id": run_id, "model": model["id"],
                              "size": args.size, "refs": len(args.ref), "cost_usd": round(estimate, 4),
                              "images": 0, "cwd": os.getcwd()})

    try:
        response = call_api(key, payload)
    except ApiError as exc:
        with spend_lock(state):
            if not exc.billed_maybe:
                append_record(state, {"month": month, "kind": "refund", "id": run_id, "cost_usd": -round(estimate, 4), "images": 0})
            spent, _ = month_spend(state, month)
        note = " ההוצאה נספרה לפי הערכה, כי לא ברור אם Google חייב." if exc.billed_maybe else ""
        print(f"{exc}{note}", file=sys.stderr)
        print(budget_line(spent, budget))
        sys.exit(2)

    images = [i for i in model_output_items(response, "image") if isinstance(i.get("data"), str)]
    tokens = token_cost(response.get("usage"), model)
    cost = prices[args.size] * len(images) + (fallback_tokens if tokens is None else tokens)
    with spend_lock(state):
        append_record(state, {"month": month, "kind": "settle", "id": run_id, "status": response.get("status"),
                              "images": len(images), "cost_usd": round(cost - estimate, 4)})
        spent, _ = month_spend(state, month)

    if not images:
        reply = " ".join(i["text"] for i in model_output_items(response, "text") if isinstance(i.get("text"), str)).strip()
        print(f"לא נוצרה תמונה (status: {response.get('status')}).{' תשובת המודל: ' + reply[:500] if reply else ''}",
              file=sys.stderr)
        print(budget_line(spent, budget))
        sys.exit(2)

    for path in save_images(images, Path(args.out)):
        print(f"נשמר: {path}")
    print(f"מודל {model['id']}, {args.size}, {args.aspect} — עלות משוערת {cost:.3f}$.")
    print(budget_line(spent, budget))


def main():
    parser = argparse.ArgumentParser(description="Nano Banana image generation with a monthly budget.")
    parser.add_argument("--status", action="store_true", help="print this month's spend and exit")
    parser.add_argument("--prompt")
    parser.add_argument("--out", help="output file (.png, .jpg, .webp)")
    parser.add_argument("--model", choices=sorted(MODELS), default="flash")
    parser.add_argument("--size", choices=("1K", "2K", "4K"), default="1K")
    parser.add_argument("--aspect", choices=ASPECTS, default="1:1")
    parser.add_argument("--ref", action="append", default=[], help="reference or source image (repeatable)")
    args = parser.parse_args()

    if args.status:
        status()
        return
    if not args.prompt or not args.out:
        parser.error("--prompt and --out are required (or use --status)")
    generate(args)


if __name__ == "__main__":
    main()
