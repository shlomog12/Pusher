"""Abstract interface for anything that can load tweets of a single account."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any, Iterable

from pusher.models import Tweet


class TweetSourceError(RuntimeError):
    """Raised when a tweet source fails to load tweets."""


class TweetSource(ABC):
    """Loads tweets authored by a single account.

    When ``mentioned_usernames`` is set, only tweets that tag one of those
    accounts are wanted; otherwise every tweet of the account is.

    With ``include_threads``, the account's replies to its own tweets are not
    treated as replies: they are merged into the tweet that starts the thread
    (see :func:`pusher.models.merge_threads`), so a thread is reported as one
    tweet. A thread is wanted when any of its tweets is.
    """

    def __init__(
        self,
        username: str,
        mentioned_usernames: Iterable[str] = (),
        include_retweets: bool = False,
        include_replies: bool = True,
        include_threads: bool = True,
    ) -> None:
        self.username = username
        self.mentioned_usernames = tuple(
            sorted({name.lstrip("@").lower() for name in mentioned_usernames})
        )
        self.include_retweets = include_retweets
        self.include_replies = include_replies
        self.include_threads = include_threads

    def keeps(self, tweet: Tweet) -> bool:
        """Whether the retweet, reply and thread settings allow ``tweet``."""
        return is_allowed(
            tweet, self.include_retweets, self.include_replies, self.include_threads
        )

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
        """Return tweets newer than ``since_id``, ordered oldest first.

        With ``include_threads``, each thread is returned as a single merged
        tweet whose :attr:`~pusher.models.Tweet.last_id` is its newest tweet.
        """


def is_allowed(
    tweet: Tweet, include_retweets: bool, include_replies: bool, include_threads: bool
) -> bool:
    """Whether ``tweet`` passes the retweet and reply settings.

    With ``include_threads``, a reply to the author's own tweet is part of a
    thread rather than a reply, so it is kept even without ``include_replies``.
    """
    if tweet.is_retweet and not include_retweets:
        return False
    if tweet.is_reply and not include_replies:
        return include_threads and tweet.is_self_reply
    return True


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


def drop_status_link(text: str, tweet_id: str) -> str:
    """``text`` without the links to the tweet ``tweet_id``, e.g. the one a quote adds."""
    pattern = rf"[ \t]*https?://(?:www\.)?(?:twitter|x)\.com/\w+/status/{tweet_id}\S*"
    return re.sub(pattern, "", text)
