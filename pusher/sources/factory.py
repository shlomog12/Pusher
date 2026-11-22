"""Factory that builds the tweet source selected in the configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, ClassVar

from pusher.config import ConfigError
from pusher.sources.api import ApiTweetSource
from pusher.sources.base import TweetSource
from pusher.sources.scraper import ScraperTweetSource

if TYPE_CHECKING:
    from pusher.config import Config

SourceBuilder = Callable[["Config"], TweetSource]


class TweetSourceFactory:
    """Maps a source name (``TWEET_SOURCE``) to the class that implements it."""

    _builders: ClassVar[dict[str, SourceBuilder]] = {
        "api": ApiTweetSource.from_config,
        "scraper": ScraperTweetSource.from_config,
    }

    @classmethod
    def register(cls, name: str, builder: SourceBuilder) -> None:
        cls._builders[name.lower()] = builder

    @classmethod
    def available(cls) -> list[str]:
        return sorted(cls._builders)

    @classmethod
    def create(cls, config: "Config") -> TweetSource:
        name = config.tweet_source.lower()
        try:
            builder = cls._builders[name]
        except KeyError as exc:
            raise ConfigError(
                f"Unknown TWEET_SOURCE {config.tweet_source!r}; "
                f"expected one of: {', '.join(cls.available())}"
            ) from exc
        return builder(config)
