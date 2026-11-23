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


def _optional_handle(name: str) -> str | None:
    value = _optional(name)
    return value.lstrip("@") if value else None


def _text_list(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _handle_list(name: str) -> tuple[str, ...]:
    return tuple(item.lstrip("@") for item in _text_list(name))


@dataclass(frozen=True)
class Feed:
    """One account whose tweets are loaded, and which of them are forwarded."""

    username: str
    # Only tweets that tag one of these accounts are forwarded; empty means all.
    mentioned_usernames: tuple[str, ...] = ()
    include_retweets: bool = False
    include_replies: bool = False
    # Merge the account's follow-up tweets of a thread into one message.
    include_threads: bool = True


@dataclass(frozen=True)
class Config:
    watched_username: str
    telegram_bot_token: str
    telegram_chat_id: str
    mentioned_usernames: tuple[str, ...] = ()
    my_username: str | None = None
    forward_my_tweets: bool = False
    forward_my_retweets: bool = False
    tweet_source: str = "api"
    poll_interval_seconds: int = 300
    include_retweets: bool = False
    include_replies: bool = False
    include_threads: bool = True
    # A line starting with one of these, and the rest of its paragraph, is removed.
    credit_prefixes: tuple[str, ...] = ()
    state_file: Path = Path("state.json")
    log_level: str = "INFO"
    twitter_bearer_token: str | None = None
    scraper_auth_token: str | None = None
    scraper_ct0: str | None = None
    scraper_headless: bool = True
    scraper_scroll_count: int = 2
    scraper_timeout_seconds: int = 45
    scraper_browser_path: str | None = None

    def __post_init__(self) -> None:
        if not self.mentioned_usernames:
            raise ConfigError(
                "Set TWITTER_MENTIONED_USERNAMES (or TWITTER_MY_USERNAME) "
                "to the accounts whose tags should be forwarded"
            )
        if self.forward_my_tweets:
            if not self.my_username:
                raise ConfigError("TWITTER_MY_USERNAME is required when FORWARD_MY_TWEETS=true")
            if self.my_username.lower() == self.watched_username.lower():
                raise ConfigError(
                    "FORWARD_MY_TWEETS=true needs TWITTER_MY_USERNAME to differ "
                    "from TWITTER_WATCHED_USERNAME"
                )

    def feeds(self) -> list[Feed]:
        """The accounts to check on every cycle."""
        feeds = [
            Feed(
                username=self.watched_username,
                mentioned_usernames=self.mentioned_usernames,
                include_retweets=self.include_retweets,
                include_replies=self.include_replies,
                include_threads=self.include_threads,
            )
        ]
        if self.forward_my_tweets and self.my_username:
            feeds.append(
                Feed(
                    username=self.my_username,
                    include_retweets=self.forward_my_retweets,
                    include_replies=self.include_replies,
                    include_threads=self.include_threads,
                )
            )
        return feeds

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
        my_username = _optional_handle("TWITTER_MY_USERNAME")
        mentioned = _handle_list("TWITTER_MENTIONED_USERNAMES")
        if not mentioned and my_username:
            mentioned = (my_username,)
        return cls(
            watched_username=_handle("TWITTER_WATCHED_USERNAME"),
            mentioned_usernames=mentioned,
            my_username=my_username,
            forward_my_tweets=_bool("FORWARD_MY_TWEETS", False),
            forward_my_retweets=_bool("FORWARD_MY_RETWEETS", False),
            telegram_bot_token=_required("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_required("TELEGRAM_CHAT_ID"),
            tweet_source=os.getenv("TWEET_SOURCE", "api").strip().lower() or "api",
            poll_interval_seconds=_positive_int("POLL_INTERVAL_SECONDS", 300),
            include_retweets=_bool("INCLUDE_RETWEETS", False),
            include_replies=_bool("INCLUDE_REPLIES", False),
            include_threads=_bool("INCLUDE_THREADS", True),
            credit_prefixes=_text_list("CREDIT_PREFIXES"),
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
