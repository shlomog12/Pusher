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


def _optional(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


@dataclass(frozen=True)
class Config:
    watched_username: str
    my_username: str
    telegram_bot_token: str
    telegram_chat_id: str
    tweet_source: str = "api"
    poll_interval_seconds: int = 300
    include_retweets: bool = False
    include_replies: bool = True
    state_file: Path = Path("state.json")
    log_level: str = "INFO"
    twitter_bearer_token: str | None = None
    scraper_auth_token: str | None = None
    scraper_ct0: str | None = None
    scraper_headless: bool = True
    scraper_scroll_count: int = 2
    scraper_timeout_seconds: int = 45
    scraper_browser_path: str | None = None

    def require(self, field_name: str, env_name: str) -> str:
        """Return a source-specific setting, failing if it was not configured."""
        value = getattr(self, field_name)
        if not value:
            raise ConfigError(
                f"{env_name} is required when TWEET_SOURCE={self.tweet_source}"
            )
        return value

    @classmethod
    def from_env(cls, env_file: str | None = None) -> "Config":
        load_dotenv(env_file)
        return cls(
            watched_username=_handle("TWITTER_WATCHED_USERNAME"),
            my_username=_handle("TWITTER_MY_USERNAME"),
            telegram_bot_token=_required("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_required("TELEGRAM_CHAT_ID"),
            tweet_source=os.getenv("TWEET_SOURCE", "api").strip().lower() or "api",
            poll_interval_seconds=_positive_int("POLL_INTERVAL_SECONDS", 300),
            include_retweets=_bool("INCLUDE_RETWEETS", False),
            include_replies=_bool("INCLUDE_REPLIES", True),
            state_file=Path(os.getenv("STATE_FILE", "state.json")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            twitter_bearer_token=_optional("TWITTER_BEARER_TOKEN"),
            scraper_auth_token=_optional("X_AUTH_TOKEN"),
            scraper_ct0=_optional("X_CT0"),
            scraper_headless=_bool("SCRAPER_HEADLESS", True),
            scraper_scroll_count=_positive_int("SCRAPER_SCROLLS", 2),
            scraper_timeout_seconds=_positive_int("SCRAPER_TIMEOUT_SECONDS", 45),
            scraper_browser_path=_optional("SCRAPER_BROWSER_PATH"),
        )
