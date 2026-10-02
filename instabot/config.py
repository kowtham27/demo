import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(Exception):
    pass


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from e


@dataclass(frozen=True)
class Config:
    ig_username: str
    ig_password: str
    ig_totp_secret: str
    targets: list[str]
    check_interval_minutes: int
    interval_jitter_percent: int
    min_fetch_completeness: float
    max_list_size: int
    proxy: str
    telegram_bot_token: str
    telegram_chat_id: str
    data_dir: Path

    @property
    def session_file(self) -> Path:
        return self.data_dir / "session.json"

    @property
    def db_file(self) -> Path:
        return self.data_dir / "tracker.db"


def load_config() -> Config:
    load_dotenv()

    username = os.getenv("IG_USERNAME", "").strip()
    password = os.getenv("IG_PASSWORD", "").strip()
    if not username or not password:
        raise ConfigError("IG_USERNAME and IG_PASSWORD must be set (see .env.example)")

    targets = [
        t.strip().lstrip("@").lower()
        for t in os.getenv("TARGET_USERNAMES", "").split(",")
        if t.strip()
    ]
    if not targets:
        raise ConfigError("TARGET_USERNAMES must list at least one account")

    interval = _int("CHECK_INTERVAL_MINUTES", 360)
    if interval < 30:
        raise ConfigError("CHECK_INTERVAL_MINUTES below 30 is very likely to get the account blocked")

    data_dir = Path(os.getenv("DATA_DIR", "data").strip() or "data")
    data_dir.mkdir(parents=True, exist_ok=True)

    return Config(
        ig_username=username,
        ig_password=password,
        ig_totp_secret=os.getenv("IG_TOTP_SECRET", "").strip(),
        targets=targets,
        check_interval_minutes=interval,
        interval_jitter_percent=max(0, min(_int("INTERVAL_JITTER_PERCENT", 20), 50)),
        min_fetch_completeness=max(0, min(_int("MIN_FETCH_COMPLETENESS_PERCENT", 90), 100)) / 100,
        max_list_size=_int("MAX_LIST_SIZE", 5000),
        proxy=os.getenv("IG_PROXY", "").strip(),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        data_dir=data_dir,
    )
