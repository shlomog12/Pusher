"""Minimal client for the X (Twitter) API v2."""

from __future__ import annotations

import html
import logging
import time
from typing import Any, Iterator

import requests

from pusher.models import Media, MediaType, Tweet

logger = logging.getLogger(__name__)

API_BASE_URL = "https://api.x.com/2"
TWEET_FIELDS = "created_at,entities,attachments,note_tweet,referenced_tweets"
MEDIA_FIELDS = "type,url,preview_image_url,variants"
MAX_RESULTS_PER_PAGE = 100
MAX_RATE_LIMIT_WAIT_SECONDS = 15 * 60


class TwitterApiError(RuntimeError):
    """Raised when the X API returns an unexpected response."""


class TwitterClient:
    def __init__(
        self,
        bearer_token: str,
        session: requests.Session | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._session = session or requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {bearer_token}"})
        self._timeout = timeout

    def get_user_id(self, username: str) -> str:
        payload = self._get(f"/users/by/username/{username}")
        try:
            return payload["data"]["id"]
        except KeyError as exc:
            raise TwitterApiError(f"User @{username} was not found: {payload}") from exc

    def get_latest_tweet_id(self, user_id: str) -> str | None:
        payload = self._get(
            f"/users/{user_id}/tweets",
            params={"max_results": 5, "tweet.fields": "id"},
        )
        return payload.get("meta", {}).get("newest_id")

    def iter_new_tweets(
        self,
        user_id: str,
        author_username: str,
        since_id: str,
        include_retweets: bool = False,
        include_replies: bool = True,
    ) -> Iterator[Tweet]:
        """Yield tweets newer than ``since_id``, oldest first."""
        excluded = []
        if not include_retweets:
            excluded.append("retweets")
        if not include_replies:
            excluded.append("replies")

        params: dict[str, Any] = {
            "since_id": since_id,
            "max_results": MAX_RESULTS_PER_PAGE,
            "tweet.fields": TWEET_FIELDS,
            "expansions": "attachments.media_keys",
            "media.fields": MEDIA_FIELDS,
        }
        if excluded:
            params["exclude"] = ",".join(excluded)

        tweets: list[Tweet] = []
        while True:
            payload = self._get(f"/users/{user_id}/tweets", params=params)
            media_by_key = {
                item["media_key"]: item
                for item in payload.get("includes", {}).get("media", [])
            }
            for raw in payload.get("data", []):
                tweets.append(parse_tweet(raw, author_username, media_by_key))

            next_token = payload.get("meta", {}).get("next_token")
            if not next_token:
                break
            params["pagination_token"] = next_token

        tweets.sort(key=lambda tweet: int(tweet.id))
        yield from tweets

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{API_BASE_URL}{path}"
        while True:
            response = self._session.get(url, params=params, timeout=self._timeout)
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
        text=_clean_text(text, entities.get("urls", [])),
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
        url = _best_video_variant(raw.get("variants", []))
        if not url:
            return None
        kind = MediaType.VIDEO if media_type == "video" else MediaType.ANIMATION
        return Media(kind, url, raw.get("preview_image_url"))

    logger.debug("Skipping unsupported media type %r", media_type)
    return None


def _best_video_variant(variants: list[dict[str, Any]]) -> str | None:
    mp4_variants = [v for v in variants if v.get("content_type") == "video/mp4"]
    if not mp4_variants:
        return None
    best = max(mp4_variants, key=lambda v: v.get("bit_rate", 0))
    return best.get("url")


def _clean_text(text: str, urls: list[dict[str, Any]]) -> str:
    """Expand t.co links and drop the ones that only point at attached media."""
    for entry in urls:
        short_url = entry.get("url")
        if not short_url:
            continue
        if entry.get("media_key"):
            replacement = ""
        else:
            replacement = entry.get("expanded_url") or short_url
        text = text.replace(short_url, replacement)
    return html.unescape(text).strip()
