"""One tracking pass: for every target, fetch followers/following, diff against storage, notify."""

import logging
from typing import Protocol

from .client import TargetInfo
from .notifier import Notifier
from .storage import Member, Storage

log = logging.getLogger(__name__)

KINDS = ("followers", "following")


class InstagramSource(Protocol):
    def target_info(self, username: str) -> TargetInfo: ...
    def can_view(self, target: TargetInfo) -> bool: ...
    def fetch(self, kind: str, target_pk: str) -> list[Member]: ...


class Tracker:
    def __init__(
        self,
        source: InstagramSource,
        storage: Storage,
        notifier: Notifier,
        *,
        min_completeness: float = 0.9,
        max_list_size: int = 5000,
        pause_between_targets: tuple[float, float] = (20, 60),
    ):
        self.source = source
        self.storage = storage
        self.notifier = notifier
        self.min_completeness = min_completeness
        self.max_list_size = max_list_size
        self.pause_between_targets = pause_between_targets

    def check_target(self, username: str) -> None:
        info = self.source.target_info(username)
        if not self.source.can_view(info):
            self.notifier.alert(
                f"@{username} is private and your account doesn't follow it — its lists can't be read."
            )
            return

        for kind in KINDS:
            self._check_list(info, kind)

    def _check_list(self, info: TargetInfo, kind: str) -> None:
        reported = info.count(kind)
        if reported > self.max_list_size:
            log.warning("@%s has %d %s (> MAX_LIST_SIZE=%d), skipping",
                        info.username, reported, kind, self.max_list_size)
            return

        fetched = self.source.fetch(kind, info.pk)

        # Instagram sometimes silently returns a truncated list. Diffing that would report a flood of
        # fake "unfollows", so only trust fetches that are close to the count Instagram itself reports.
        if reported and len(fetched) < reported * self.min_completeness:
            log.warning("@%s %s: fetched %d of %d reported — looks incomplete, not diffing this round",
                        info.username, kind, len(fetched), reported)
            return

        diff = self.storage.apply_snapshot(info.pk, info.username, kind, fetched, reported)
        if diff.baseline:
            log.info("@%s %s: baseline recorded (%d accounts)", info.username, kind, len(fetched))
        elif diff.added or diff.removed:
            log.info("@%s %s: +%d / -%d", info.username, kind, len(diff.added), len(diff.removed))
            self.notifier.report_changes(info.username, kind, diff)
        else:
            log.info("@%s %s: no changes (%d)", info.username, kind, len(fetched))
