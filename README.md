# instabot — Instagram follower/following change tracker

Watches one or more Instagram accounts and tells you when someone **follows / unfollows them** or
when they **start / stop following** someone. Changes are printed to the console, stored in SQLite,
and (optionally) pushed to Telegram.

## Read this first

- **Instagram's official API cannot do this.** The Graph API only exposes follower *counts* for other
  accounts, never the lists. This bot therefore uses Instagram's private mobile API via
  [instagrapi](https://github.com/subzeroid/instagrapi), logged in as you. That is against Instagram's
  Terms of Service. The risk falls on the **login account** (security challenges, temporary blocks,
  in the worst case a ban). **Use a secondary account** if you can.
- The bot can only see what the login account can see: a private target must be followed by it.
- Be a decent person: ideally the people you track know about it.

## How it works

```
every ~6h (± jitter)
  └─ for each target
       ├─ look up profile (private? counts)
       ├─ fetch followers + following (v1 API, 2–6s between requests)
       ├─ fetched ≥ 90% of reported count?  ── no ──> skip (Instagram truncated the list)
       ├─ diff against the last stored list (SQLite)
       └─ notify: + new / − lost   (the first run only records a baseline)
```

Safeguards built in:
- **Session reuse** (`data/session.json`): logs in once, not every run. Repeated logins are the #1 ban trigger.
- **Truncation guard**: a partial list from Instagram never turns into a flood of fake "unfollows".
- **Rename-safe**: users are tracked by numeric ID, so a username change isn't reported as unfollow + follow.
- **Exponential backoff** on rate limits (up to 24h), and a Telegram alert if a security challenge needs you.
- **Size cap** (`MAX_LIST_SIZE`, default 5000) so huge accounts don't burn hundreds of requests.

## Setup (Windows / PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # then edit .env
```

Fill in `IG_USERNAME`, `IG_PASSWORD` and `TARGET_USERNAMES` in `.env`.

### Telegram notifications (optional, recommended)
1. Message [@BotFather](https://t.me/BotFather), then `/newbot`, and copy the token into `TELEGRAM_BOT_TOKEN`.
2. Send your new bot any message, then get your chat id from [@userinfobot](https://t.me/userinfobot) and put it in `TELEGRAM_CHAT_ID`.

## Usage

```powershell
python -m instabot login      # first time: log in interactively (answers 2FA / security codes), saves session
python -m instabot check      # one check now (first run = baseline, no notifications)
python -m instabot run        # keep running, checking every CHECK_INTERVAL_MINUTES
python -m instabot history            # recent changes for all targets
python -m instabot history friend -n 100
```

Example output:
```
=== @friend — followers ===
+ New followers (2):
  - @alice (Alice Smith)
  - @bob
- Lost followers (1):
  - @carol
```

### Running it 24/7
- **Simplest:** leave `python -m instabot run` open in a terminal.
- **Windows Task Scheduler:** schedule `.venv\Scripts\python.exe -m instabot check` every 6 hours,
  with "Start in" set to this folder.
- **Linux server / Raspberry Pi:** a cron entry such as `0 */6 * * * cd /path/instabot && .venv/bin/python -m instabot check`.
  Run `login` once on that machine first. Residential IPs are much safer than cloud or VPS IPs.
  Set `IG_PROXY` if you have to run from a datacenter.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ChallengeRequired` / "security challenge" alert | Open the Instagram app on your phone, approve "It was me", then run `python -m instabot login`. |
| 2FA prompt every time | Put your authenticator secret in `IG_TOTP_SECRET`, or re-run `login`. |
| "looks incomplete, not diffing" | Normal now and then. If it happens every time, lower `MIN_FETCH_COMPLETENESS_PERCENT` a little (e.g. 80). |
| "Instagram is rate-limiting" | The bot backs off automatically. Raise `CHECK_INTERVAL_MINUTES`. |
| "private and your account doesn't follow it" | The login account must follow the private target. |

## Project layout

```
instabot/
  __main__.py   CLI + scheduler loop, error handling/backoff
  config.py     .env loading + validation
  client.py     instagrapi wrapper: session persistence, 2FA/challenge, fetch calls
  tracker.py    per-target check: completeness guard, diff, notify
  storage.py    SQLite: current members, event log, check log
  notifier.py   console + Telegram formatting/delivery
tests/          offline tests with a fake Instagram source (pytest)
data/           session.json + tracker.db (git-ignored — contains your session!)
```

Run tests: `python -m pytest -q`
