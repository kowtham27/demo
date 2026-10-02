"""SQLite persistence: the current member list per (target, kind) plus an append-only change log."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    target_pk   TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('followers', 'following')),
    user_pk     TEXT NOT NULL,
    username    TEXT NOT NULL,
    full_name   TEXT NOT NULL DEFAULT '',
    first_seen  TEXT NOT NULL,
    PRIMARY KEY (target_pk, kind, user_pk)
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_pk   TEXT NOT NULL,
    target_username TEXT NOT NULL,
    kind        TEXT NOT NULL,
    change      TEXT NOT NULL CHECK (change IN ('added', 'removed')),
    user_pk     TEXT NOT NULL,
    username    TEXT NOT NULL,
    full_name   TEXT NOT NULL DEFAULT '',
    at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_target ON events (target_username, at);

CREATE TABLE IF NOT EXISTS checks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_pk   TEXT NOT NULL,
    kind        TEXT NOT NULL,
    at          TEXT NOT NULL,
    fetched     INTEGER NOT NULL,
    reported    INTEGER NOT NULL,
    added       INTEGER NOT NULL,
    removed     INTEGER NOT NULL,
    baseline    INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class Member:
    pk: str
    username: str
    full_name: str = ""


@dataclass(frozen=True)
class Diff:
    added: list[Member]
    removed: list[Member]
    baseline: bool  # True on the first check: everything is "new", nothing is reported


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def current_members(self, target_pk: str, kind: str) -> dict[str, Member]:
        rows = self.conn.execute(
            "SELECT user_pk, username, full_name FROM members WHERE target_pk = ? AND kind = ?",
            (target_pk, kind),
        )
        return {r["user_pk"]: Member(r["user_pk"], r["username"], r["full_name"]) for r in rows}

    def has_baseline(self, target_pk: str, kind: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM checks WHERE target_pk = ? AND kind = ? LIMIT 1", (target_pk, kind)
        ).fetchone()
        return row is not None

    def apply_snapshot(
        self,
        target_pk: str,
        target_username: str,
        kind: str,
        fetched: list[Member],
        reported_count: int,
    ) -> Diff:
        """Diff `fetched` against the stored list, persist the new state and log the changes."""
        baseline = not self.has_baseline(target_pk, kind)
        previous = self.current_members(target_pk, kind)
        latest = {m.pk: m for m in fetched}

        added = [m for pk, m in latest.items() if pk not in previous]
        removed = [m for pk, m in previous.items() if pk not in latest]
        now = _now()

        with self.conn:
            self.conn.executemany(
                "INSERT INTO members (target_pk, kind, user_pk, username, full_name, first_seen) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(target_pk, kind, m.pk, m.username, m.full_name, now) for m in added],
            )
            self.conn.executemany(
                "DELETE FROM members WHERE target_pk = ? AND kind = ? AND user_pk = ?",
                [(target_pk, kind, m.pk) for m in removed],
            )
            # Keep usernames fresh: people rename, and a rename is not a follow/unfollow.
            self.conn.executemany(
                "UPDATE members SET username = ?, full_name = ? "
                "WHERE target_pk = ? AND kind = ? AND user_pk = ?",
                [
                    (m.username, m.full_name, target_pk, kind, m.pk)
                    for pk, m in latest.items()
                    if pk in previous and previous[pk] != m
                ],
            )
            if not baseline:
                self.conn.executemany(
                    "INSERT INTO events (target_pk, target_username, kind, change, user_pk, username, full_name, at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [(target_pk, target_username, kind, "added", m.pk, m.username, m.full_name, now) for m in added]
                    + [(target_pk, target_username, kind, "removed", m.pk, m.username, m.full_name, now) for m in removed],
                )
            self.conn.execute(
                "INSERT INTO checks (target_pk, kind, at, fetched, reported, added, removed, baseline) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (target_pk, kind, now, len(fetched), reported_count,
                 0 if baseline else len(added), 0 if baseline else len(removed), int(baseline)),
            )

        if baseline:
            return Diff(added=[], removed=[], baseline=True)
        return Diff(
            added=sorted(added, key=lambda m: m.username),
            removed=sorted(removed, key=lambda m: m.username),
            baseline=False,
        )

    def history(self, target_username: str | None = None, limit: int = 50) -> list[sqlite3.Row]:
        query = "SELECT * FROM events"
        params: tuple = ()
        if target_username:
            query += " WHERE target_username = ?"
            params = (target_username.lstrip("@").lower(),)
        query += " ORDER BY id DESC LIMIT ?"
        return list(self.conn.execute(query, params + (limit,)))
