# Feed Watcher

Get an email the moment a new item shows up on a JSON endpoint.

## How it works

`watcher.py` polls the endpoint in `WATCH_URL`, compares the result against
the items it has already seen (`seen.json`), and emails you about each new
one. If nothing is new, nothing is sent. A GitHub Actions workflow runs it
every 5 minutes, 24/7, with no server to maintain.

```
poll WATCH_URL  ─▶  diff vs seen.json  ─▶  new item?  ─▶  email alert
                                                     └▶  update seen.json
```

- Error responses, empty entries and temporary outages never trigger an email.
- An item that briefly disappears and comes back is not re-sent.
- The endpoint URL lives in a secret, not in the code.

---

## Setup

### 1. Gmail App Password

1. Turn on **2-Step Verification** for the Gmail account:
   myaccount.google.com → Security → 2-Step Verification.
2. Create an **App Password**: myaccount.google.com → Security →
   App passwords. You get a 16-character password: that's `SMTP_PASS`
   (not your normal Gmail password).

### 2. Add repository secrets

In the repo: **Settings ▸ Secrets and variables ▸ Actions ▸ New repository secret.**

| Secret           | Value                                         |
|------------------|-----------------------------------------------|
| `WATCH_URL`      | the JSON endpoint to watch                    |
| `WATCH_PAGE_URL` | *(optional)* link included in the email       |
| `ALERT_SUBJECT`  | *(optional)* email subject, default "New item posted" |
| `NOTIFY_BACKEND` | `email`                                       |
| `SMTP_USER`      | your full Gmail address                       |
| `SMTP_PASS`      | the 16-character App Password (no spaces)     |
| `EMAIL_TO`       | *(optional)* recipient, defaults to `SMTP_USER` |

### 3. Enable the workflow

Open the **Actions** tab and enable workflows if GitHub asks. To check the
setup, click **Watch ▸ Run workflow**. The log should say
`Fetched N item(s).` and `No new items.` (no email, since nothing is new).

---

## Running locally

```bash
cp .env.example .env    # then fill in your values
python watcher.py
```

`.env` is gitignored. Set `WATCH_DRY_RUN=1` to print the alert instead of
sending it.

## Other delivery options

`NOTIFY_BACKEND` also accepts `callmebot` (WhatsApp, needs `CALLMEBOT_PHONE`
and `CALLMEBOT_APIKEY`) or `twilio` (WhatsApp, needs `TWILIO_FROM`,
`TWILIO_TO`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`).

## Notes

- GitHub may start scheduled runs a few minutes late under load.
- The workflow re-enables itself each run, so GitHub's 60-day inactivity
  pause doesn't stop it.
- No third-party Python packages are needed.
