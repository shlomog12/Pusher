"""Tweet source backed by the official X (Twitter) API v2."""

from __future__ import annotations

import html
import logging
import time
from typing import TYPE_CHECKING, Any

import requests

from pusher.models import Media, MediaType, Tweet
from pusher.sources.base import TweetSource, TweetSourceError, best_mp4_url, expand_urls

if TYPE_CHECKING:
    from pusher.config import Config

logger = logging.getLogger(__name__)

API_BASE_URL = "https://api.x.com/2"
TWEET_FIELDS = "created_at,entities,attachments,note_tweet,referenced_tweets"
MEDIA_FIELDS = "type,url,preview_image_url,variants"
MAX_RESULTS_PER_PAGE = 100
MAX_RATE_LIMIT_WAIT_SECONDS = 15 * 60


class TwitterApiError(TweetSourceError):
    """Raised when the X API returns an unexpected response."""


class ApiTweetSource(TweetSource):
    def __init__(
        self,
        username: str,
        bearer_token: str,
        include_retweets: bool = False,
        include_replies: bool = True,
        session: requests.Session | None = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(username, include_retweets, include_replies)
        self._session = session or requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {bearer_token}"})
        self._timeout = timeout
        self._user_id: str | None = None

    @classmethod
    def from_config(cls, config: "Config") -> "ApiTweetSource":
        return cls(
            username=config.watched_username,
            bearer_token=config.require("twitter_bearer_token", "TWITTER_BEARER_TOKEN"),
            include_retweets=config.include_retweets,
            include_replies=config.include_replies,
        )

    def latest_tweet_id(self) -> str | None:
        payload = self._get(
            f"/users/{self._resolve_user_id()}/tweets",
            params={"max_results": 5, **self._exclude_param()},
        )
        return payload.get("meta", {}).get("newest_id")

    def new_tweets(self, since_id: str) -> list[Tweet]:
        params: dict[str, Any] = {
            "since_id": since_id,
            "max_results": MAX_RESULTS_PER_PAGE,
            "tweet.fields": TWEET_FIELDS,
            "expansions": "attachments.media_keys",
            "media.fields": MEDIA_FIELDS,
            **self._exclude_param(),
        }

        tweets: list[Tweet] = []
        path = f"/users/{self._resolve_user_id()}/tweets"
        while True:
            payload = self._get(path, params=params)
            media_by_key = {
                item["media_key"]: item
                for item in payload.get("includes", {}).get("media", [])
            }
            tweets.extend(
                parse_tweet(raw, self.username, media_by_key)
                for raw in payload.get("data", [])
            )
            next_token = payload.get("meta", {}).get("next_token")
            if not next_token:
                break
            params["pagination_token"] = next_token

        tweets.sort(key=lambda tweet: int(tweet.id))
        return tweets

    def _resolve_user_id(self) -> str:
        if self._user_id is None:
            payload = self._get(f"/users/by/username/{self.username}")
            try:
                self._user_id = payload["data"]["id"]
            except KeyError as exc:
                raise TwitterApiError(
                    f"User @{self.username} was not found: {payload}"
                ) from exc
        return self._user_id

    def _exclude_param(self) -> dict[str, str]:
        excluded = []
        if not self.include_retweets:
            excluded.append("retweets")
        if not self.include_replies:
            excluded.append("replies")
        return {"exclude": ",".join(excluded)} if excluded else {}

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
                    f"GET {path} failed with {response.status_code}: {response.text}"
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


def parse_tweet(
    raw: dict[str, Any],
    author_username: str,
    media_by_key: dict[str, dict[str, Any]],
) -> Tweet:
    note = raw.get("note_tweet") or {}
    text = note.get("text") or raw.get("text", "")
    entities = note.get("entities") or raw.get("entities") or {}

    mentions = {
        mention["username"].lower()
        for source in (raw.get("entities") or {}, note.get("entities") or {})
        for mention in source.get("mentions", [])
    }

    media_keys = (raw.get("attachments") or {}).get("media_keys", [])
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
    )


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
