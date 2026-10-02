"""Delivers change reports to the console and (optionally) Telegram."""

import html
import logging

import requests

from .storage import Diff, Member

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4000  # Telegram caps messages at 4096 chars; leave headroom


def _label(kind: str) -> tuple[str, str]:
    if kind == "followers":
        return "New followers", "Lost followers"
    return "Started following", "Stopped following"


def format_report(target: str, kind: str, diff: Diff, *, rich: bool) -> str:
    plus_title, minus_title = _label(kind)

    def line(m: Member) -> str:
        name = f" ({m.full_name})" if m.full_name else ""
        if rich:
            return (f'• <a href="https://instagram.com/{html.escape(m.username)}">@{html.escape(m.username)}</a>'
                    f"{html.escape(name)}")
        return f"  - @{m.username}{name}"

    header = f"@{target} — {kind}"
    parts = [f"<b>{html.escape(header)}</b>" if rich else f"=== {header} ==="]
    if diff.added:
        parts.append(f"{'➕ ' if rich else '+ '}{plus_title} ({len(diff.added)}):")
        parts.extend(line(m) for m in diff.added)
    if diff.removed:
        parts.append(f"{'➖ ' if rich else '- '}{minus_title} ({len(diff.removed)}):")
        parts.extend(line(m) for m in diff.removed)
    return "\n".join(parts)


class Notifier:
    def __init__(self, telegram_token: str = "", telegram_chat_id: str = ""):
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)

    def report_changes(self, target: str, kind: str, diff: Diff) -> None:
        if not (diff.added or diff.removed):
            return
        print(format_report(target, kind, diff, rich=False), flush=True)
        if self.telegram_enabled:
            self._send_telegram(format_report(target, kind, diff, rich=True))

    def alert(self, message: str) -> None:
        """Operational problems (challenge required, login failed, ...) that need the owner's attention."""
        log.warning(message)
        if self.telegram_enabled:
            self._send_telegram(f"⚠️ <b>instabot</b>: {html.escape(message)}")

    def _send_telegram(self, text: str) -> None:
        for chunk in _chunks(text, TELEGRAM_LIMIT):
            try:
                resp = requests.post(
                    f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                    json={
                        "chat_id": self.telegram_chat_id,
                        "text": chunk,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                    timeout=15,
                )
                if not resp.ok:
                    log.error("Telegram send failed: %s %s", resp.status_code, resp.text[:200])
            except requests.RequestException as e:
                log.error("Telegram send failed: %s", e)


def _chunks(text: str, limit: int) -> list[str]:
    """Split on line boundaries so HTML tags (one per line) are never cut in half."""
    chunks, current = [], ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
