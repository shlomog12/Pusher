"""Tweet source backed by the official X (Twitter) API v2."""

from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Iterable

import requests

from pusher.models import Media, MediaType, Tweet
from pusher.sources.base import TweetSource, TweetSourceError, best_mp4_url, expand_urls

if TYPE_CHECKING:
    from pusher.config import Config, Feed

logger = logging.getLogger(__name__)

API_BASE_URL = "https://api.x.com/2"
SEARCH_PATH = "/tweets/search/recent"
TWEET_FIELDS = "created_at,entities,attachments,note_tweet,referenced_tweets"
MEDIA_FIELDS = "type,url,preview_image_url,variants"
# The retweeted tweet, its media and its author, so retweets are sent in full
# rather than as X's truncated "RT @user: ..." text.
EXPANSIONS = ",".join([
    "attachments.media_keys",
    "referenced_tweets.id",
    "referenced_tweets.id.attachments.media_keys",
    "referenced_tweets.id.author_id",
])
USER_FIELDS = "username"
RETWEET_PREFIX = re.compile(r"^RT @(\w+):")
MAX_RESULTS_PER_PAGE = 100
MAX_RATE_LIMIT_WAIT_SECONDS = 15 * 60
# A host may resolve to an IPv6 address that never answers; a short connect
# timeout makes requests fall back to the next (IPv4) address quickly instead
# of waiting minutes for the operating system to give up.
CONNECT_TIMEOUT_SECONDS = 10

# Recent search only covers the last 7 days; stay a little inside that window.
SEARCH_WINDOW = timedelta(days=7) - timedelta(minutes=5)
TWITTER_EPOCH_MS = 1288834974657
SNOWFLAKE_TIMESTAMP_SHIFT = 22


class TwitterApiError(TweetSourceError):
    """Raised when the X API returns an unexpected response."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ApiTweetSource(TweetSource):
    """Loads tweets through the X API v2 recent search endpoint.

    When ``mentioned_usernames`` is set, the search query only matches tweets
    that tag one of them, so with X's pay-per-use pricing only those tweets are
    billed rather than every tweet the account publishes.
    """

    def __init__(
        self,
        username: str,
        bearer_token: str,
        mentioned_usernames: Iterable[str] = (),
        include_retweets: bool = False,
        include_replies: bool = True,
        session: requests.Session | None = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(username, mentioned_usernames, include_retweets, include_replies)
        self._session = session or requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {bearer_token}"})
        self._timeout = (CONNECT_TIMEOUT_SECONDS, timeout)

    @classmethod
    def from_config(cls, config: "Config", feed: "Feed") -> "ApiTweetSource":
        return cls(
            username=feed.username,
            bearer_token=config.require("twitter_bearer_token", "TWITTER_BEARER_TOKEN"),
            mentioned_usernames=feed.mentioned_usernames,
            include_retweets=feed.include_retweets,
            include_replies=feed.include_replies,
        )

    def initial_since_id(self) -> str:
        # A tweet id derived from the current time costs no API reads: every
        # tweet posted from now on has a larger id.
        return snowflake_from_datetime(datetime.now(timezone.utc))

    def new_tweets(self, since_id: str) -> list[Tweet]:
        params: dict[str, Any] = {
            "query": self.search_query(),
            "since_id": since_id,
            "max_results": MAX_RESULTS_PER_PAGE,
            "tweet.fields": TWEET_FIELDS,
            "expansions": EXPANSIONS,
            "media.fields": MEDIA_FIELDS,
            "user.fields": USER_FIELDS,
        }
        try:
            tweets = self._search(params)
        except TwitterApiError as exc:
            if exc.status_code != 400:
                raise
            # since_id is older than the search window (e.g. after a long
            # downtime); fall back to the oldest time the endpoint accepts.
            logger.warning("since_id %s was rejected, searching by start time", since_id)
            params.pop("since_id")
            params["start_time"] = _search_start_time(since_id)
            tweets = [t for t in self._search(params) if int(t.id) > int(since_id)]

        tweets.sort(key=lambda tweet: int(tweet.id))
        return tweets

    def search_query(self) -> str:
        terms = [f"from:{self.username}"]
        mentions = " OR ".join(f"@{name}" for name in self.mentioned_usernames)
        if len(self.mentioned_usernames) > 1:
            mentions = f"({mentions})"
        if mentions:
            terms.append(mentions)
        if not self.include_retweets:
            terms.append("-is:retweet")
        if not self.include_replies:
            terms.append("-is:reply")
        return " ".join(terms)

    def _search(self, params: dict[str, Any]) -> list[Tweet]:
        params = dict(params)
        tweets: list[Tweet] = []
        while True:
            payload = self._get(SEARCH_PATH, params=params)
            includes = payload.get("includes", {})
            media_by_key = {item["media_key"]: item for item in includes.get("media", [])}
            tweets_by_id = {item["id"]: item for item in includes.get("tweets", [])}
            users_by_id = {item["id"]: item for item in includes.get("users", [])}
            tweets.extend(
                parse_tweet(raw, self.username, media_by_key, tweets_by_id, users_by_id)
                for raw in payload.get("data", [])
            )
            next_token = payload.get("meta", {}).get("next_token")
            if not next_token:
                return tweets
            params["next_token"] = next_token

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{API_BASE_URL}{path}"
        while True:
            try:
                response = self._session.get(url, params=params, timeout=self._timeout)
            except requests.RequestException as exc:
                raise TwitterApiError(f"GET {path} failed: {exc}") from exc
            if response.status_code == 429:
                self._wait_for_rate_limit(response)
                continue
            if response.status_code >= 400:
                raise TwitterApiError(
                    f"GET {path} failed with {response.status_code}: {response.text}",
                    status_code=response.status_code,
                )
            return response.json()

    @staticmethod
    def _wait_for_rate_limit(response: requests.Response) -> None:
        reset = response.headers.get("x-rate-limit-reset")
        wait = 60.0
        if reset and reset.isdigit():
            wait = max(int(reset) - time.time(), 1.0)
        wait = min(wait, MAX_RATE_LIMIT_WAIT_SECONDS)
        logger.warning("X API rate limit reached, waiting %.0f seconds", wait)
        time.sleep(wait)


def snowflake_from_datetime(moment: datetime) -> str:
    milliseconds = int(moment.timestamp() * 1000) - TWITTER_EPOCH_MS
    return str(milliseconds << SNOWFLAKE_TIMESTAMP_SHIFT)


def datetime_from_snowflake(tweet_id: str) -> datetime:
    milliseconds = (int(tweet_id) >> SNOWFLAKE_TIMESTAMP_SHIFT) + TWITTER_EPOCH_MS
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)


def _search_start_time(since_id: str) -> str:
    earliest = datetime.now(timezone.utc) - SEARCH_WINDOW
    start = max(datetime_from_snowflake(since_id), earliest)
    return start.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_tweet(
    raw: dict[str, Any],
    author_username: str,
    media_by_key: dict[str, dict[str, Any]],
    tweets_by_id: dict[str, dict[str, Any]] | None = None,
    users_by_id: dict[str, dict[str, Any]] | None = None,
) -> Tweet:
    content = raw
    retweeted_username = None
    retweeted_id = _retweeted_id(raw)
    if retweeted_id:
        prefix = RETWEET_PREFIX.match(raw.get("text", ""))
        retweeted_username = prefix.group(1) if prefix else ""
        original = (tweets_by_id or {}).get(retweeted_id)
        if original:
            content = original
            author = (users_by_id or {}).get(original.get("author_id", ""), {})
            retweeted_username = author.get("username") or retweeted_username

    note = content.get("note_tweet") or {}
    text = note.get("text") or content.get("text", "")
    entities = note.get("entities") or content.get("entities") or {}

    mentions = {
        mention["username"].lower()
        for source in (
            raw.get("entities") or {},
            content.get("entities") or {},
            note.get("entities") or {},
        )
        for mention in source.get("mentions", [])
    }

    media_keys = (content.get("attachments") or {}).get("media_keys", [])
    media = tuple(
        parsed
        for key in media_keys
        if key in media_by_key
        for parsed in [parse_media(media_by_key[key])]
        if parsed is not None
    )

    return Tweet(
        id=raw["id"],
        author_username=author_username,
        text=html.unescape(expand_urls(text, entities.get("urls", []), set())).strip(),
        mentions=frozenset(mentions),
        media=media,
        created_at=raw.get("created_at"),
        retweeted_username=retweeted_username,
    )


def _retweeted_id(raw: dict[str, Any]) -> str | None:
    for reference in raw.get("referenced_tweets") or []:
        if reference.get("type") == "retweeted":
            return reference.get("id")
    return None


def parse_media(raw: dict[str, Any]) -> Media | None:
    media_type = raw.get("type")
    if media_type == "photo":
        url = raw.get("url")
        return Media(MediaType.PHOTO, url) if url else None

    if media_type in ("video", "animated_gif"):
        url = best_mp4_url(raw.get("variants", []))
        if not url:
            return None
        kind = MediaType.VIDEO if media_type == "video" else MediaType.ANIMATION
        return Media(kind, url, raw.get("preview_image_url"))

    logger.debug("Skipping unsupported media type %r", media_type)
    return None
