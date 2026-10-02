"""Thin wrapper around instagrapi: session persistence, login, and the few calls the tracker needs."""

import logging
from dataclasses import dataclass

from instagrapi import Client
from instagrapi.exceptions import LoginRequired, TwoFactorRequired

from .config import Config
from .storage import Member

log = logging.getLogger(__name__)


class InteractiveLoginNeeded(Exception):
    """Raised in unattended mode when Instagram wants a code that only a human can supply."""


@dataclass(frozen=True)
class TargetInfo:
    pk: str
    username: str
    is_private: bool
    follower_count: int
    following_count: int

    def count(self, kind: str) -> int:
        return self.follower_count if kind == "followers" else self.following_count


class InstagramClient:
    def __init__(self, config: Config, interactive: bool = False):
        self.config = config
        self.interactive = interactive
        self.cl = Client()
        # Human-ish pause between every private-API request.
        self.cl.delay_range = [2, 6]
        if config.proxy:
            self.cl.set_proxy(config.proxy)
        self.cl.challenge_code_handler = self._challenge_code_handler

    # ---------- auth ----------

    def login(self) -> None:
        """Reuse the saved session when possible; fall back to a fresh login that keeps the same device ids."""
        cfg = self.config
        if cfg.session_file.exists():
            session = self.cl.load_settings(cfg.session_file)
            self.cl.set_settings(session)
            try:
                self._password_login()
                self.cl.get_timeline_feed()  # cheap call that proves the session is valid
                log.info("Logged in with saved session")
                self._save_session()
                return
            except LoginRequired:
                log.info("Saved session expired, logging in again with the same device")
                old = self.cl.get_settings()
                self.cl.set_settings({})
                self.cl.set_uuids(old["uuids"])

        self._password_login()
        self._save_session()
        log.info("Logged in as @%s", cfg.ig_username)

    def _password_login(self) -> None:
        cfg = self.config
        try:
            self.cl.login(cfg.ig_username, cfg.ig_password)
        except TwoFactorRequired:
            code = self._two_factor_code()
            self.cl.login(cfg.ig_username, cfg.ig_password, verification_code=code)

    def _two_factor_code(self) -> str:
        if self.config.ig_totp_secret:
            return self.cl.totp_generate_code(self.config.ig_totp_secret)
        if self.interactive:
            return input("Enter the 2FA code from your authenticator/SMS: ").strip()
        raise InteractiveLoginNeeded(
            "Instagram requires a 2FA code. Run `python -m instabot login` once, or set IG_TOTP_SECRET."
        )

    def _challenge_code_handler(self, username: str, choice) -> str:
        if self.interactive:
            return input(f"Instagram sent a security code to @{username} via {choice}. Enter it: ").strip()
        raise InteractiveLoginNeeded(
            "Instagram raised a security challenge. Run `python -m instabot login` to complete it."
        )

    def _save_session(self) -> None:
        self.cl.dump_settings(self.config.session_file)

    # ---------- data ----------

    def target_info(self, username: str) -> TargetInfo:
        u = self.cl.user_info_by_username_v1(username)
        return TargetInfo(
            pk=str(u.pk),
            username=u.username.lower(),
            is_private=bool(u.is_private),
            follower_count=int(u.follower_count or 0),
            following_count=int(u.following_count or 0),
        )

    def can_view(self, target: TargetInfo) -> bool:
        if not target.is_private:
            return True
        if target.username == self.config.ig_username.lower():
            return True
        return bool(self.cl.user_friendship_v1(target.pk).following)

    def fetch(self, kind: str, target_pk: str) -> list[Member]:
        # The v1 (private mobile API) endpoints are far more reliable than the GraphQL ones when logged in.
        if kind == "followers":
            users = self.cl.user_followers_v1(target_pk, amount=0)
        else:
            users = self.cl.user_following_v1(target_pk, amount=0)
        return [Member(str(u.pk), u.username.lower(), u.full_name or "") for u in users]
