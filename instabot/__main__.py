"""CLI: python -m instabot {login,check,run,history}"""

import argparse
import logging
import random
import sys
import time

from instagrapi.exceptions import (
    ChallengeRequired,
    FeedbackRequired,
    LoginRequired,
    PleaseWaitFewMinutes,
    RateLimitError,
    UserNotFound,
)

from .client import InstagramClient, InteractiveLoginNeeded
from .config import Config, ConfigError, load_config
from .notifier import Notifier
from .storage import Storage
from .tracker import Tracker

log = logging.getLogger("instabot")

RATE_LIMIT_ERRORS = (PleaseWaitFewMinutes, RateLimitError, FeedbackRequired)
MAX_BACKOFF_MINUTES = 24 * 60


def build(config: Config, interactive: bool) -> tuple[InstagramClient, Storage, Notifier, Tracker]:
    client = InstagramClient(config, interactive=interactive)
    storage = Storage(config.db_file)
    notifier = Notifier(config.telegram_bot_token, config.telegram_chat_id)
    tracker = Tracker(
        client, storage, notifier,
        min_completeness=config.min_fetch_completeness,
        max_list_size=config.max_list_size,
    )
    return client, storage, notifier, tracker


def run_pass(config: Config, client: InstagramClient, tracker: Tracker, notifier: Notifier) -> None:
    for i, username in enumerate(config.targets):
        if i:
            time.sleep(random.uniform(*tracker.pause_between_targets))
        try:
            tracker.check_target(username)
        except UserNotFound:
            notifier.alert(f"@{username} not found (renamed, deleted, or has blocked you).")
        except LoginRequired:
            log.info("Session dropped mid-run, logging in again")
            client.login()
            tracker.check_target(username)


def cmd_login(config: Config) -> int:
    client = InstagramClient(config, interactive=True)
    client.login()
    print(f"Logged in as @{config.ig_username}. Session saved to {config.session_file}")
    return 0


def cmd_check(config: Config) -> int:
    client, storage, notifier, tracker = build(config, interactive=sys.stdin.isatty())
    try:
        client.login()
        run_pass(config, client, tracker, notifier)
    finally:
        storage.close()
    return 0


def cmd_run(config: Config) -> int:
    client, storage, notifier, tracker = build(config, interactive=False)
    base = config.check_interval_minutes
    backoff = base
    try:
        client.login()
        log.info("Watching %s every ~%d min", ", ".join(f"@{t}" for t in config.targets), base)
        while True:
            try:
                run_pass(config, client, tracker, notifier)
                backoff = base
            except RATE_LIMIT_ERRORS as e:
                backoff = min(backoff * 2, MAX_BACKOFF_MINUTES)
                notifier.alert(f"Instagram is rate-limiting ({type(e).__name__}). Backing off to {backoff} min.")
            except (ChallengeRequired, InteractiveLoginNeeded) as e:
                notifier.alert(f"Stopped: {e or 'Instagram security challenge'}. "
                               "Run `python -m instabot login`, then restart.")
                return 2

            jitter = backoff * config.interval_jitter_percent / 100
            wait_min = backoff + random.uniform(-jitter, jitter)
            log.info("Next check in %.0f min", wait_min)
            time.sleep(wait_min * 60)
    except KeyboardInterrupt:
        log.info("Stopped by user")
        return 0
    finally:
        storage.close()


def cmd_history(config: Config, target: str | None, limit: int) -> int:
    storage = Storage(config.db_file)
    try:
        rows = storage.history(target, limit)
    finally:
        storage.close()
    if not rows:
        print("No changes recorded yet.")
        return 0
    verbs = {
        ("followers", "added"): "gained follower",
        ("followers", "removed"): "lost follower",
        ("following", "added"): "started following",
        ("following", "removed"): "stopped following",
    }
    for r in reversed(rows):
        name = f" ({r['full_name']})" if r["full_name"] else ""
        print(f"{r['at']}  @{r['target_username']} {verbs[(r['kind'], r['change'])]} @{r['username']}{name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="instabot", description="Track Instagram follower/following changes.")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("login", help="log in interactively (handles 2FA / security codes) and save the session")
    sub.add_parser("check", help="run a single check now")
    sub.add_parser("run", help="check forever on the configured interval")
    h = sub.add_parser("history", help="show recorded changes")
    h.add_argument("target", nargs="?", help="only this account")
    h.add_argument("-n", "--limit", type=int, default=50)
    args = parser.parse_args(argv)

    # Windows consoles default to a legacy code page; names with emoji/non-Latin chars would crash print().
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # instagrapi logs every HTTP request at INFO; keep our output readable.
    if not args.verbose:
        logging.getLogger("instagrapi").setLevel(logging.WARNING)
        logging.getLogger("public_request").setLevel(logging.WARNING)
        logging.getLogger("private_request").setLevel(logging.WARNING)

    try:
        config = load_config()
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    if args.command == "login":
        return cmd_login(config)
    if args.command == "check":
        return cmd_check(config)
    if args.command == "run":
        return cmd_run(config)
    return cmd_history(config, args.target, args.limit)


if __name__ == "__main__":
    sys.exit(main())
