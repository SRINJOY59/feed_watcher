#!/usr/bin/env python3
"""
Feed watcher.

Polls a JSON endpoint, compares it against previously seen items, and sends
an alert for every NEW one. No alert is sent when nothing new appears.

The endpoint is read from the WATCH_URL env var (kept in .env locally or a
repository secret in CI), so nothing about the target is in the code.

Delivery backends supported (pick one via env NOTIFY_BACKEND):
  - email      (Gmail/any SMTP, reliable)  -> NOTIFY_BACKEND=email
  - callmebot  (free WhatsApp, personal)   -> NOTIFY_BACKEND=callmebot
  - twilio     (WhatsApp, team / group)    -> NOTIFY_BACKEND=twilio

If the JSON shape changes the script still works: it fingerprints each item
and pulls a human-readable title from whatever field is present.
"""

import json
import os
import sys
import hashlib
import urllib.request
import urllib.parse
from pathlib import Path


def load_dotenv(path=Path(__file__).with_name(".env")):
    """Minimal .env loader (stdlib only). Real env vars / CI secrets win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if not os.environ.get(key):
            os.environ[key] = val


load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles choke on emoji

API_URL = os.environ.get("WATCH_URL", "")
PAGE_URL = os.environ.get("WATCH_PAGE_URL", "")   # optional "see all" link
STATE_FILE = Path(os.environ.get("WATCH_STATE_FILE") or "seen.json")
ALERT_SUBJECT = os.environ.get("ALERT_SUBJECT") or "New item posted"

# If True, don't send anything the first time we ever run (just record the
# current items as "already seen"). This stops a flood if items already exist
# when you first deploy. Set WATCH_NOTIFY_ON_FIRST_RUN=1 to alert instead.
NOTIFY_ON_FIRST_RUN = os.environ.get("WATCH_NOTIFY_ON_FIRST_RUN", "0") == "1"

DRY_RUN = os.environ.get("WATCH_DRY_RUN", "0") == "1"

ID_KEYS = ("id", "_id", "slug", "uuid")
TITLE_KEYS = ("title", "name", "heading")


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
def fetch_items():
    req = urllib.request.Request(
        API_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)

    # The endpoint may return a bare list, or wrap it like {"data": [...]}.
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        # no list field -> treat the dict as a single item; is_real_item()
        # drops it if it's really an error/status object
        data = lists[0] if lists else [data]
    if not isinstance(data, list):
        data = []
    return [it for it in data if is_real_item(it)]


def is_real_item(item):
    """True only for entries that look like an actual posted item."""
    if not isinstance(item, dict):
        return False
    has_title = any(str(item.get(k) or "").strip() for k in TITLE_KEYS)
    has_id = any(item.get(k) not in (None, "") for k in ID_KEYS)
    return has_title or (has_id and len(item) > 1)


# --------------------------------------------------------------------------- #
# Identity / display helpers (defensive about unknown JSON shape)
# --------------------------------------------------------------------------- #
def item_id(item):
    """Stable identity for an item, so we don't re-alert on the same one."""
    if isinstance(item, dict):
        for key in ID_KEYS:
            if item.get(key) not in (None, ""):
                return f"{key}:{item[key]}"
        # fall back to a hash of the full content
    return "hash:" + hashlib.sha256(
        json.dumps(item, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]


def item_title(item):
    if isinstance(item, dict):
        for key in TITLE_KEYS:
            if item.get(key):
                return str(item[key])
        return next((str(v) for v in item.values() if isinstance(v, str) and v.strip()),
                    "New item")
    return str(item)


def item_tag(item):
    if isinstance(item, dict):
        for key in ("domain", "category", "type", "track"):
            if item.get(key):
                return str(item[key])
    return ""


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
def load_seen():
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except Exception:
            return set()
    return None  # None == never run before


def save_seen(seen):
    STATE_FILE.write_text(json.dumps(sorted(seen), indent=2))


# --------------------------------------------------------------------------- #
# Notification
# --------------------------------------------------------------------------- #
def build_message(new_items):
    n = len(new_items)
    lines = [f"🔔 {n} new item{'s' if n != 1 else ''} posted!", ""]
    for it in new_items:
        tag = item_tag(it)
        lines.append(f"• {item_title(it)}" + (f" [{tag}]" if tag else ""))
    if PAGE_URL:
        lines += ["", f"See all: {PAGE_URL}"]
    return "\n".join(lines)


def send_callmebot(message):
    phone = os.environ["CALLMEBOT_PHONE"]        # e.g. +919876543210
    apikey = os.environ["CALLMEBOT_APIKEY"]
    url = "https://api.callmebot.com/whatsapp.php?" + urllib.parse.urlencode(
        {"phone": phone, "text": message, "apikey": apikey}
    )
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def send_twilio(message):
    # Uses Twilio's WhatsApp sandbox or a provisioned number.
    from_ = os.environ["TWILIO_FROM"]            # e.g. whatsapp:+14155238886
    to = os.environ["TWILIO_TO"]                 # e.g. whatsapp:+919876543210
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    endpoint = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    data = urllib.parse.urlencode(
        {"From": from_, "To": to, "Body": message}
    ).encode()
    req = urllib.request.Request(endpoint, data=data)
    auth = (sid + ":" + token).encode()
    import base64
    req.add_header("Authorization", "Basic " + base64.b64encode(auth).decode())
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def send_email(message):
    import smtplib
    from email.message import EmailMessage

    # `or` defaults: unset GitHub secrets arrive as empty strings
    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    port = int(os.environ.get("SMTP_PORT") or "587")
    user = os.environ["SMTP_USER"]              # your full gmail address
    password = os.environ["SMTP_PASS"]          # Gmail App Password (not your login pw)
    to_addr = os.environ.get("EMAIL_TO") or user  # defaults to sending to yourself
    from_addr = os.environ.get("EMAIL_FROM") or user

    msg = EmailMessage()
    msg["Subject"] = f"🔔 {ALERT_SUBJECT}"
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(message)

    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        s.login(user, password)
        s.send_message(msg)
    return f"email sent to {to_addr}"


def notify(message):
    backend = (os.environ.get("NOTIFY_BACKEND") or "email").lower()
    if DRY_RUN:
        print(f"--- DRY RUN (backend={backend}), would send message: ---")
        print(message)
        print("--- end ---")
        return
    if backend == "email":
        print("Email:", send_email(message))
    elif backend == "callmebot":
        print("CallMeBot response:", send_callmebot(message))
    elif backend == "twilio":
        print("Twilio response:", send_twilio(message))
    else:
        raise SystemExit(f"Unknown NOTIFY_BACKEND: {backend}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    if not API_URL:
        sys.exit("WATCH_URL is not set (add it to .env or the repo secrets).")

    try:
        items = fetch_items()
    except Exception as e:
        # Endpoint hiccups are common; exit 0 so GitHub doesn't email a
        # "workflow failed" notice every 5 min. Next run retries.
        print(f"::warning::Fetch failed, will retry next run: {e}")
        return

    print(f"Fetched {len(items)} item(s).")
    current_ids = {item_id(it) for it in items}
    seen = load_seen()

    first_run = seen is None
    if first_run:
        seen = set()

    new_items = [it for it in items if item_id(it) not in seen]

    if first_run and not NOTIFY_ON_FIRST_RUN:
        # Record everything as seen, don't alert.
        save_seen(current_ids)
        print(f"First run: recorded {len(current_ids)} existing item(s) as seen, no alert sent.")
        return

    if new_items:
        print(f"{len(new_items)} NEW item(s) detected.")
        notify(build_message(new_items))
    else:
        print("No new items.")

    # Persist the union so we never lose track if the list changes order.
    seen |= current_ids
    save_seen(seen)


if __name__ == "__main__":
    main()
