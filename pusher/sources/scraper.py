"""Tweet source that scrapes the X website, without the official API.

A headless Chromium (Playwright) opens the watched profile with the session
cookie of a logged-in X account and captures the GraphQL timeline responses
that the web app itself loads. Reading those JSON responses is far more stable
than parsing the rendered HTML, and it does not depend on GraphQL query ids,
which X rotates frequently.
"""

from __future__ import annotations

import html
import logging
import re
from typing import TYPE_CHECKING, Any, Iterable, Iterator, Protocol

from pusher.models import Media, MediaType, Tweet, merge_threads
from pusher.sources.base import (
    TweetSource,
    TweetSourceError,
    best_mp4_url,
    expand_urls,
    is_allowed,
)

if TYPE_CHECKING:
    from pusher.config import Config, Feed

logger = logging.getLogger(__name__)

X_BASE_URL = "https://x.com"
TIMELINE_URL_PATTERN = re.compile(r"/i/api/graphql/[^/]+/(UserTweets|UserTweetsAndReplies)\b")
CONVERSATION_URL_PATTERN = re.compile(r"/i/api/graphql/[^/]+/TweetDetail\b")
LOGIN_URL_MARKERS = ("/login", "/i/flow/login")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
SCROLL_PIXELS = 5000
SCROLL_PAUSE_MS = 1500


class ScraperError(TweetSourceError):
    """Raised when the X website could not be scraped."""


class TimelineFetcher(Protocol):
    def fetch(self, username: str, include_replies: bool) -> list[dict[str, Any]]:
        """Return the raw GraphQL timeline payloads for ``username``."""

    def fetch_conversations(
        self, username: str, tweet_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Return the raw GraphQL conversation payloads of the given tweets."""


class PlaywrightTimelineFetcher:
    """Loads a profile page in headless Chromium and records its timeline payloads."""

    def __init__(
        self,
        auth_token: str,
        ct0: str | None = None,
        headless: bool = True,
        scroll_count: int = 2,
        timeout_seconds: int = 45,
        executable_path: str | None = None,
    ) -> None:
        self._executable_path = executable_path
        self._auth_token = auth_token
        self._ct0 = ct0
        self._headless = headless
        self._scroll_count = scroll_count
        self._timeout_ms = timeout_seconds * 1000

    def fetch(self, username: str, include_replies: bool) -> list[dict[str, Any]]:
        url = f"{X_BASE_URL}/{username}"
        if include_replies:
            url += "/with_replies"
        return self._capture([url], TIMELINE_URL_PATTERN)

    def fetch_conversations(
        self, username: str, tweet_ids: list[str]
    ) -> list[dict[str, Any]]:
        urls = [f"{X_BASE_URL}/{username}/status/{tweet_id}" for tweet_id in tweet_ids]
        return self._capture(urls, CONVERSATION_URL_PATTERN)

    def _capture(self, urls: list[str], pattern: re.Pattern[str]) -> list[dict[str, Any]]:
        """Open each url in one browser session and return the matching payloads."""
        if not urls:
            return []
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ScraperError(
                "The scraper source requires Playwright: "
                "pip install -r requirements-scraper.txt && playwright install chromium"
            ) from exc

        url = urls[0]
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=self._headless, executable_path=self._executable_path
                )
                try:
                    context = browser.new_context(user_agent=USER_AGENT, locale="en-US")
                    context.add_cookies(self._cookies())
                    payloads: list[dict[str, Any]] = []
                    for url in urls:
                        payloads.extend(self._capture_page(context, url, pattern))
                    return payloads
                finally:
                    browser.close()
        except PlaywrightError as exc:
            raise ScraperError(f"Failed to load {url}: {exc}") from exc

    def _capture_page(
        self, context: Any, url: str, pattern: re.Pattern[str]
    ) -> list[dict[str, Any]]:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        page = context.new_page()
        matches = lambda response: bool(pattern.search(response.url))  # noqa: E731

        responses: list[Any] = []
        page.on("response", lambda r: responses.append(r) if matches(r) else None)

        try:
            with page.expect_response(matches, timeout=self._timeout_ms):
                page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
        except PlaywrightTimeoutError as exc:
            if any(marker in page.url for marker in LOGIN_URL_MARKERS):
                raise ScraperError(
                    "X redirected to the login page; X_AUTH_TOKEN is missing or expired"
                ) from exc
            raise ScraperError(f"Timed out waiting for the tweets of {url}") from exc

        for _ in range(self._scroll_count):
            page.mouse.wheel(0, SCROLL_PIXELS)
            page.wait_for_timeout(SCROLL_PAUSE_MS)

        payloads = []
        for response in responses:
            try:
                payloads.append(response.json())
            except Exception:  # noqa: BLE001 - a single bad body must not fail the cycle
                logger.debug("Skipping unreadable response %s", response.url)
        page.close()
        return payloads

    def _cookies(self) -> list[dict[str, Any]]:
        cookies = {"auth_token": self._auth_token}
        if self._ct0:
            cookies["ct0"] = self._ct0
        return [
            {
                "name": name,
                "value": value,
                "domain": ".x.com",
                "path": "/",
                "secure": True,
                "httpOnly": name == "auth_token",
                "sameSite": "None",
            }
            for name, value in cookies.items()
        ]


class ScraperTweetSource(TweetSource):
    def __init__(
        self,
        username: str,
        fetcher: TimelineFetcher,
        mentioned_usernames: Iterable[str] = (),
        include_retweets: bool = False,
        include_replies: bool = True,
        include_threads: bool = True,
    ) -> None:
        super().__init__(
            username, mentioned_usernames, include_retweets, include_replies, include_threads
        )
        self._fetcher = fetcher

    @classmethod
    def from_config(cls, config: "Config", feed: "Feed") -> "ScraperTweetSource":
        fetcher = PlaywrightTimelineFetcher(
            auth_token=config.require("scraper_auth_token", "X_AUTH_TOKEN"),
            ct0=config.scraper_ct0,
            headless=config.scraper_headless,
            scroll_count=config.scraper_scroll_count,
            timeout_seconds=config.scraper_timeout_seconds,
            executable_path=config.scraper_browser_path,
        )
        return cls(
            username=feed.username,
            fetcher=fetcher,
            mentioned_usernames=feed.mentioned_usernames,
            include_retweets=feed.include_retweets,
            include_replies=feed.include_replies,
            include_threads=feed.include_threads,
        )

    def initial_since_id(self) -> str | None:
        tweets = self._parse(self._fetcher.fetch(self.username, self.include_replies))
        return tweets[-1].id if tweets else None

    def new_tweets(self, since_id: str) -> list[Tweet]:
        payloads = self._fetcher.fetch(self.username, self.include_replies)
        tweets = [t for t in self._parse(payloads) if int(t.id) > int(since_id)]
        if not self.include_threads:
            return tweets

        known_ids = {tweet.id for tweet in tweets}
        continuations = [
            tweet
            for tweet in self._thread_continuations(tweets, _reply_counts(payloads))
            if tweet.id not in known_ids and int(tweet.id) > int(since_id)
        ]
        return merge_threads(sorted(tweets + continuations, key=lambda t: int(t.id)))

    def _thread_continuations(
        self, tweets: list[Tweet], reply_counts: dict[str, int]
    ) -> list[Tweet]:
        """The account's follow-ups of threads started by the wanted ``tweets``.

        The profile timeline shows only part of a thread, so the page of every
        new thread-starting tweet that has replies is opened to read the rest.
        """
        root_ids = [
            tweet.id
            for tweet in tweets
            if self.wants(tweet)
            and not tweet.is_retweet
            and not tweet.is_reply
            and reply_counts.get(tweet.id, 1) > 0
        ]
        if not root_ids:
            return []
        payloads = self._fetcher.fetch_conversations(self.username, root_ids)
        return [
            tweet
            for tweet in parse_timeline(payloads, self.username, include_replies=True)
            if tweet.is_self_reply
        ]

    def _parse(self, payloads: list[dict[str, Any]]) -> list[Tweet]:
        return parse_timeline(
            payloads,
            self.username,
            include_retweets=self.include_retweets,
            include_replies=self.include_replies,
            include_threads=self.include_threads,
        )


def parse_timeline(
    payloads: list[dict[str, Any]],
    username: str,
    include_retweets: bool = False,
    include_replies: bool = True,
    include_threads: bool = False,
) -> list[Tweet]:
    """Extract the account's own tweets from GraphQL timeline payloads."""
    tweets: dict[str, Tweet] = {}
    for payload in payloads:
        for result in _iter_tweet_results(payload):
            if _screen_name(result).lower() != username.lower():
                continue
            tweet = parse_tweet_result(result, username)
            if tweet is not None and is_allowed(
                tweet, include_retweets, include_replies, include_threads
            ):
                tweets[tweet.id] = tweet
    return sorted(tweets.values(), key=lambda tweet: int(tweet.id))


def parse_tweet_result(result: dict[str, Any], author_username: str) -> Tweet | None:
    tweet_id = result.get("rest_id") or (result.get("legacy") or {}).get("id_str")
    if not tweet_id:
        return None

    outer_legacy = result.get("legacy") or {}
    content = result
    retweeted_username = "" if "retweeted_status_result" in outer_legacy else None
    retweeted = _unwrap(outer_legacy.get("retweeted_status_result", {}).get("result"))
    if retweeted:
        content = retweeted
        retweeted_username = _screen_name(retweeted)

    legacy = content.get("legacy") or {}
    note = (
        (content.get("note_tweet") or {}).get("note_tweet_results", {}).get("result") or {}
    )
    text = note.get("text") or legacy.get("full_text", "")
    entities = note.get("entity_set") or legacy.get("entities") or {}

    mentions = {
        mention["screen_name"].lower()
        for source in (
            outer_legacy.get("entities") or {},
            legacy.get("entities") or {},
            note.get("entity_set") or {},
        )
        for mention in source.get("user_mentions", [])
        if mention.get("screen_name")
    }

    raw_media = (legacy.get("extended_entities") or legacy.get("entities") or {}).get(
        "media", []
    )
    media = tuple(m for m in (parse_media(item) for item in raw_media) if m is not None)
    media_links = {item["url"] for item in raw_media if item.get("url")}

    text = expand_urls(text, entities.get("urls", []), media_links)
    return Tweet(
        id=tweet_id,
        author_username=author_username,
        text=html.unescape(text).strip(),
        mentions=frozenset(mentions),
        media=media,
        created_at=outer_legacy.get("created_at"),
        retweeted_username=retweeted_username,
        in_reply_to_id=outer_legacy.get("in_reply_to_status_id_str") or None,
        in_reply_to_username=outer_legacy.get("in_reply_to_screen_name") or None,
        first_version_id=_first_version_id(result, tweet_id),
    )


def _first_version_id(result: dict[str, Any], tweet_id: str) -> str | None:
    """The id of the tweet's first version, when the tweet is an edit."""
    control = result.get("edit_control") or {}
    history = (control.get("edit_control_initial") or control).get("edit_tweet_ids") or []
    first = control.get("initial_tweet_id") or (history[0] if history else None)
    return first if first and first != tweet_id else None


def parse_media(raw: dict[str, Any]) -> Media | None:
    media_type = raw.get("type")
    preview = raw.get("media_url_https")
    if media_type == "photo":
        return Media(MediaType.PHOTO, preview) if preview else None
    if media_type in ("video", "animated_gif"):
        url = best_mp4_url((raw.get("video_info") or {}).get("variants", []))
        if not url:
            return None
        kind = MediaType.VIDEO if media_type == "video" else MediaType.ANIMATION
        return Media(kind, url, preview)
    return None


def _iter_tweet_results(node: Any) -> Iterator[dict[str, Any]]:
    """Walk a GraphQL payload and yield every top-level tweet result."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "tweet_results" and isinstance(value, dict):
                result = _unwrap(value.get("result"))
                if result:
                    yield result
            else:
                yield from _iter_tweet_results(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_tweet_results(item)


def _unwrap(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result and result.get("__typename") == "TweetWithVisibilityResults":
        return result.get("tweet")
    return result


def _screen_name(result: dict[str, Any]) -> str:
    user = (result.get("core") or {}).get("user_results", {}).get("result") or {}
    return (
        (user.get("core") or {}).get("screen_name")
        or (user.get("legacy") or {}).get("screen_name")
        or ""
    )


def _reply_counts(payloads: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for payload in payloads:
        for result in _iter_tweet_results(payload):
            legacy = result.get("legacy") or {}
            tweet_id = result.get("rest_id") or legacy.get("id_str")
            if tweet_id and isinstance(legacy.get("reply_count"), int):
                counts[tweet_id] = legacy["reply_count"]
    return counts
