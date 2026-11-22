"""Abstract interface for anything that can load tweets of a single account."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable

from pusher.models import Tweet


class TweetSourceError(RuntimeError):
    """Raised when a tweet source fails to load tweets."""


class TweetSource(ABC):
    """Loads tweets authored by a single account.

    When ``mentioned_usernames`` is set, only tweets that tag one of those
    accounts are wanted; otherwise every tweet of the account is.
    """

    def __init__(
        self,
        username: str,
        mentioned_usernames: Iterable[str] = (),
        include_retweets: bool = False,
        include_replies: bool = True,
    ) -> None:
        self.username = username
        self.mentioned_usernames = tuple(
            sorted({name.lstrip("@").lower() for name in mentioned_usernames})
        )
        self.include_retweets = include_retweets
        self.include_replies = include_replies

    def wants(self, tweet: Tweet) -> bool:
        return not self.mentioned_usernames or any(
            tweet.mentions_user(name) for name in self.mentioned_usernames
        )

    @abstractmethod
    def initial_since_id(self) -> str | None:
        """Return the id to start watching from on the first run.

        Only tweets with a larger id are reported by :meth:`new_tweets`.
        ``None`` means no starting point could be determined yet.
        """

    @abstractmethod
    def new_tweets(self, since_id: str) -> list[Tweet]:
        """Return tweets newer than ``since_id``, ordered oldest first."""


def best_mp4_url(variants: list[dict[str, Any]]) -> str | None:
    """Pick the highest bitrate MP4 out of X's video variants."""
    mp4_variants = [v for v in variants if v.get("content_type") == "video/mp4"]
    if not mp4_variants:
        return None
    best = max(mp4_variants, key=lambda v: v.get("bit_rate", v.get("bitrate", 0)) or 0)
    return best.get("url")


def expand_urls(text: str, urls: list[dict[str, Any]], media_urls: set[str]) -> str:
    """Replace t.co links with their targets and drop links to attached media."""
    for entry in urls:
        short_url = entry.get("url")
        if not short_url:
            continue
        expanded = "" if entry.get("media_key") else entry.get("expanded_url") or short_url
        text = text.replace(short_url, expanded)
    for media_url in media_urls:
        text = text.replace(media_url, "")
    return text
