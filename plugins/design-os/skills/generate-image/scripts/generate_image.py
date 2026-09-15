#!/usr/bin/env python3
"""Generate or edit an image with Nano Banana (Gemini).

Cost is managed by the founder in Google Cloud (budget alerts on the project that
owns the key), not by this script. Nothing about the prompt is logged.

Where the API key comes from, first match wins:
1. the GEMINI_API_KEY environment variable;
2. a file named gemini-api-key (one line, the key alone) in a .business-os folder:
   BUSINESS_OS_STATE_DIR (tests), ~/.business-os, <working folder>/.business-os,
   a project folder staged into a Cowork cloud session
   (/mnt/user-data/uploads/*/.business-os), or a connected folder seen from the
   Cowork device VM ($HOME/mnt/*/.business-os).
The script keeps the file out of git with .business-os/.gitignore and warns if git
already tracks it. The key is sent to Google in a header and never printed; the
design-os hook blocks agents from reading the file.

Usage:
    generate_image.py --prompt "..." --out design/assets/x/a.jpg [--model flash|pro|lite]
                      [--size 1K|2K|4K] [--aspect 1:1] [--ref img.png ...]

Output is JPEG only: on 2026-09-16 the API answered 400 to image/png for flash
("Supported values: 'image/jpeg'").
    generate_image.py --status

Exit codes: 0 ok, 2 error.
"""
import argparse
import base64
import http.client
import json
import mimetypes
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import NoReturn

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
KEY_ENV = "GEMINI_API_KEY"
KEY_FILE = "gemini-api-key"
STATE_FOLDER = ".business-os"
# Classic keys are AIza + 35 characters; newer Google keys also contain a dot.
KEY_RE = re.compile(r"[A-Za-z0-9_.\-]{20,}")
COWORK_UPLOADS = Path("/mnt/user-data/uploads")

MODELS = {
    "pro": {"id": "gemini-3-pro-image", "sizes": ("1K", "2K", "4K")},
    "flash": {"id": "gemini-3.1-flash-image", "sizes": ("1K", "2K", "4K")},
    "lite": {"id": "gemini-3.1-flash-lite-image", "sizes": ("1K",)},
}
MAX_REFS = 14
ASPECTS = ("1:1", "3:2", "2:3", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
OUT_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def fail(message) -> NoReturn:
    print(message, file=sys.stderr)
    sys.exit(2)


def real_home():
    try:
        import pwd
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, AttributeError):
        return Path.home()


def key_folders():
    """.business-os folders that may hold the key file, in lookup order, without duplicates."""
    override = os.environ.get("BUSINESS_OS_STATE_DIR")
    if override:
        return [Path(override)]
    homes = {real_home(), Path.home()}
    candidates = [home / STATE_FOLDER for home in homes] + [Path.cwd() / STATE_FOLDER]
    for root in [COWORK_UPLOADS] + [home / "mnt" for home in homes]:
        try:
            candidates += sorted(p / STATE_FOLDER for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
    seen, folders = set(), []
    for folder in candidates:
        resolved = str(folder.resolve()) if folder.exists() else str(folder)
        if resolved not in seen:
            seen.add(resolved)
            folders.append(folder)
    return folders


def protect_key_file(folder):
    """Best effort: make <folder>/.gitignore name the key file, so `git add -A` skips it."""
    if not folder.is_dir():
        return
    ignore = folder / ".gitignore"
    try:
        text = ignore.read_text() if ignore.exists() else ""
        if KEY_FILE not in (line.strip() for line in text.splitlines()):
            with open(ignore, "a") as handle:
                handle.write(("" if not text or text.endswith("\n") else "\n") + KEY_FILE + "\n")
    except OSError:
        pass


def key_tracked_by_git(folder):
    try:
        result = subprocess.run(["git", "ls-files", "--error-unmatch", KEY_FILE], cwd=folder,
                                capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def read_key_file(path):
    """(key, problem) from one key file."""
    try:
        raw = path.read_bytes()
    except OSError:
        return "", f"קובץ המפתח {path} קיים אבל לא נפתח."
    try:
        text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return "", f"קובץ המפתח {path} לא קריא — שומרים אותו כטקסט רגיל (UTF-8)."
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    value = lines[0] if lines else ""
    if "=" in value:  # tolerate GEMINI_API_KEY=... pasted as is
        value = value.split("=", 1)[1]
    # Quotes, spaces and invisible marks a copy from a browser or a Hebrew UI can add.
    value = "".join(ch for ch in value if ch.isascii() and ch.isprintable() and not ch.isspace()).strip("\"'")
    if not KEY_RE.fullmatch(value):
        return "", f"קובץ המפתח {path} קיים, אבל התוכן לא נראה כמו מפתח. צריך שורה אחת, רק המפתח."
    return value, None


def load_key():
    """(key, source, folder, problem). source is a Hebrew description; folder is set for a key file."""
    key = os.environ.get(KEY_ENV, "").strip()
    if key:
        return key, f"משתנה הסביבה {KEY_ENV}", None, None
    problem = None
    for folder in key_folders():
        path = folder / KEY_FILE
        if not path.is_file():
            continue
        protect_key_file(folder)
        value, problem = read_key_file(path)
        if value:
            return value, f"הקובץ {path}", folder, None
    return "", None, None, problem


def missing_key_message(problem=None):
    message = f"חסר מפתח Gemini — לא נוצרה תמונה. {problem + ' ' if problem else ''}"
    if COWORK_UPLOADS.parent.is_dir() and not problem:
        return message + (
            f"ב-Cowork בענן: מעלים עם device_stage_files את {STATE_FOLDER}/{KEY_FILE} מהתיקייה המחוברת, ואז "
            "מריצים שוב את אותה פקודה בדיוק — בלי cd, בלי להעתיק את הקובץ ובלי להוסיף נתיב. הסקריפט מוצא "
            "את הקובץ שהועלה לבד. אין קובץ כזה בתיקייה המחוברת — חסם בראש הדיווח. לא מבקשים את המפתח בצ'אט.")
    return message + (f"המייסד שם את המפתח, מחוץ לשיחה, בקובץ {STATE_FOLDER}/{KEY_FILE} בתיקיית הפרויקט "
                      f"(שורה אחת, רק המפתח) או במשתנה הסביבה {KEY_ENV}. לא מבקשים את המפתח בצ'אט.")


def tracked_key_warning(folder):
    if folder is not None and key_tracked_by_git(folder):
        return ("⚠️ קובץ המפתח נמצא ב-git. המייסד צריך להוציא אותו מה-repo ולהחליף את המפתח ב-Google Cloud "
                "— חסם בראש הדיווח.")
    return None


def model_output_items(response, kind):
    """Items of one type from the model's final output — not thoughts, not echoed input."""
    for step in response.get("steps") or []:
        if isinstance(step, dict) and step.get("type") == "model_output":
            for item in step.get("content") or []:
                if isinstance(item, dict) and item.get("type") == kind:
                    yield item


def api_error_message(body):
    try:
        data = json.loads(body)
        error = data.get("error", data)
        return str(error.get("message") or error.get("status") or error.get("code") or body[:300])
    except (ValueError, AttributeError):
        return body[:300]


class ApiError(Exception):
    pass


def call_api(key, payload):
    """Return the Interaction; raise ApiError with a Hebrew message."""
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
        raise ApiError(f"ה-API של Google החזיר שגיאה {exc.code}: "
                       f"{api_error_message(exc.read().decode(errors='replace'))}") from exc
    except UnicodeEncodeError as exc:  # a character that cannot go in an HTTP header; nothing was sent
        raise ApiError("המפתח מכיל תווים לא תקינים — לא נשלחה בקשה ל-Google.") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException, OSError) as exc:
        raise ApiError(f"אין תשובה מלאה מ-Google ({exc}). אם זו חסימת רשת בסביבה — זה חסם בראש הדיווח.") from exc
    except ValueError as exc:
        raise ApiError("ה-API של Google החזיר תשובה שאינה JSON.") from exc
    if not isinstance(data, dict):
        raise ApiError("ה-API של Google החזיר תשובה לא צפויה.")
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


def status():
    _, source, folder, problem = load_key()
    print(f"מפתח Gemini: נמצא — {source}." if source else missing_key_message(problem))
    warning = tracked_key_warning(folder)
    if warning:
        print(warning)


def generate(args):
    model = MODELS[args.model]
    if args.size not in model["sizes"]:
        fail(f"המודל {args.model} לא תומך בגודל {args.size}. גדלים אפשריים: {', '.join(model['sizes'])}.")
    if Path(args.out).suffix.lower() not in OUT_MIME:
        fail(f"סיומת קובץ לא נתמכת: {args.out}. Google מחזיר רק JPEG — --out צריך להסתיים ב-.jpg.")
    if len(args.ref) > MAX_REFS:
        fail(f"יותר מדי קבצי ייחוס ({len(args.ref)}); המקסימום {MAX_REFS}.")
    key, _, folder, problem = load_key()
    if not key:
        fail(missing_key_message(problem))
    warning = tracked_key_warning(folder)
    if warning:
        print(warning)

    try:
        response = call_api(key, build_payload(args, model["id"]))
    except ApiError as exc:
        fail(str(exc))

    images = [i for i in model_output_items(response, "image") if isinstance(i.get("data"), str)]
    if not images:
        reply = " ".join(i["text"] for i in model_output_items(response, "text") if isinstance(i.get("text"), str)).strip()
        fail(f"לא נוצרה תמונה (status: {response.get('status')}).{' תשובת המודל: ' + reply[:500] if reply else ''}")

    for path in save_images(images, Path(args.out)):
        print(f"נשמר: {path}")
    print(f"מודל {model['id']}, {args.size}, {args.aspect}.")


def main():
    parser = argparse.ArgumentParser(description="Nano Banana image generation.")
    parser.add_argument("--status", action="store_true", help="say whether a Gemini key was found, and where")
    parser.add_argument("--prompt")
    parser.add_argument("--out", help="output file (.jpg)")
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
