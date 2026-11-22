"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ConfigError(f"{name} must be a boolean, got {raw!r}")


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be positive, got {value}")
    return value


def _handle(name: str) -> str:
    return _required(name).lstrip("@")


@dataclass(frozen=True)
class Config:
    twitter_bearer_token: str
    watched_username: str
    my_username: str
    telegram_bot_token: str
    telegram_chat_id: str
    poll_interval_seconds: int = 300
    include_retweets: bool = False
    include_replies: bool = True
    state_file: Path = Path("state.json")
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env_file: str | None = None) -> "Config":
        load_dotenv(env_file)
        return cls(
            twitter_bearer_token=_required("TWITTER_BEARER_TOKEN"),
            watched_username=_handle("TWITTER_WATCHED_USERNAME"),
            my_username=_handle("TWITTER_MY_USERNAME"),
            telegram_bot_token=_required("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_required("TELEGRAM_CHAT_ID"),
            poll_interval_seconds=_positive_int("POLL_INTERVAL_SECONDS", 300),
            include_retweets=_bool("INCLUDE_RETWEETS", False),
            include_replies=_bool("INCLUDE_REPLIES", True),
            state_file=Path(os.getenv("STATE_FILE", "state.json")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
