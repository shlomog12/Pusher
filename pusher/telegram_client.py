"""Telegram Bot API client that knows how to deliver a tweet with its media."""

from __future__ import annotations

import html
import json
import logging
from typing import Any

import requests

from pusher.models import Media, MediaType, Tweet

logger = logging.getLogger(__name__)

API_BASE_URL = "https://api.telegram.org"
MAX_MESSAGE_LENGTH = 4096
MAX_CAPTION_LENGTH = 1024
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1024 * 1024

_SINGLE_MEDIA_METHODS = {
    MediaType.PHOTO: ("sendPhoto", "photo"),
    MediaType.VIDEO: ("sendVideo", "video"),
    MediaType.ANIMATION: ("sendAnimation", "animation"),
}


class TelegramApiError(RuntimeError):
    """Raised when the Telegram Bot API rejects a request."""


class TelegramClient:
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        session: requests.Session | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._base_url = f"{API_BASE_URL}/bot{bot_token}"
        self._chat_id = chat_id
        self._session = session or requests.Session()
        self._timeout = timeout

    def send_tweet(self, tweet: Tweet) -> None:
        text = format_tweet(tweet)
        if not tweet.media:
            self.send_message(text)
            return

        caption: str | None = text
        if len(text) > MAX_CAPTION_LENGTH:
            self.send_message(text)
            caption = None

        try:
            self._send_media(tweet.media, caption, upload=False)
            return
        except (TelegramApiError, requests.RequestException) as exc:
            logger.info("Sending media by URL failed (%s), uploading instead", exc)

        try:
            self._send_media(tweet.media, caption, upload=True)
            return
        except (TelegramApiError, requests.RequestException) as exc:
            logger.warning("Uploading media failed (%s), sending links only", exc)

        self.send_message(format_media_fallback(tweet, include_text=caption is not None))

    def send_message(self, text: str) -> None:
        self._call(
            "sendMessage",
            data={
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
        )

    def _send_media(
        self, media: tuple[Media, ...], caption: str | None, upload: bool
    ) -> None:
        files: dict[str, tuple[str, bytes]] = {}
        if len(media) == 1:
            self._send_single(media[0], caption, upload, files)
        else:
            self._send_group(media, caption, upload, files)

    def _send_single(
        self,
        item: Media,
        caption: str | None,
        upload: bool,
        files: dict[str, tuple[str, bytes]],
    ) -> None:
        method, field = _SINGLE_MEDIA_METHODS[item.type]
        data: dict[str, Any] = {"chat_id": self._chat_id}
        if caption:
            data["caption"] = caption
            data["parse_mode"] = "HTML"
        if item.type is not MediaType.PHOTO:
            data["supports_streaming"] = "true"

        if upload:
            files[field] = self._download(item.url)
        else:
            data[field] = item.url
        self._call(method, data=data, files=files or None)

    def _send_group(
        self,
        media: tuple[Media, ...],
        caption: str | None,
        upload: bool,
        files: dict[str, tuple[str, bytes]],
    ) -> None:
        entries = []
        for index, item in enumerate(media):
            # Media groups accept only photos and videos; GIFs from X are MP4s.
            entry: dict[str, Any] = {
                "type": "photo" if item.type is MediaType.PHOTO else "video",
            }
            if upload:
                name = f"file{index}"
                files[name] = self._download(item.url)
                entry["media"] = f"attach://{name}"
            else:
                entry["media"] = item.url
            if index == 0 and caption:
                entry["caption"] = caption
                entry["parse_mode"] = "HTML"
            entries.append(entry)

        self._call(
            "sendMediaGroup",
            data={"chat_id": self._chat_id, "media": json.dumps(entries)},
            files=files or None,
        )

    def _download(self, url: str) -> tuple[str, bytes]:
        with self._session.get(url, stream=True, timeout=self._timeout) as response:
            response.raise_for_status()
            chunks = []
            size = 0
            for chunk in response.iter_content(DOWNLOAD_CHUNK_BYTES):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise TelegramApiError(f"Media exceeds Telegram upload limit: {url}")
                chunks.append(chunk)
        filename = url.split("?", 1)[0].rsplit("/", 1)[-1] or "media"
        return filename, b"".join(chunks)

    def _call(
        self,
        method: str,
        data: dict[str, Any],
        files: dict[str, tuple[str, bytes]] | None = None,
    ) -> dict[str, Any]:
        response = self._session.post(
            f"{self._base_url}/{method}", data=data, files=files, timeout=self._timeout
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise TelegramApiError(
                f"{method} returned non-JSON response ({response.status_code})"
            ) from exc
        if not payload.get("ok"):
            raise TelegramApiError(
                f"{method} failed: {payload.get('description', response.text)}"
            )
        return payload


def format_tweet(tweet: Tweet, max_length: int = MAX_MESSAGE_LENGTH) -> str:
    header = f"<b>@{html.escape(tweet.author_username)}</b> tagged you on X"
    footer = f'<a href="{html.escape(tweet.url)}">View on X</a>'
    budget = max_length - len(header) - len(footer) - 4
    body = _escape_within(tweet.text, budget)
    return f"{header}\n\n{body}\n\n{footer}" if body else f"{header}\n\n{footer}"


def format_media_fallback(tweet: Tweet, include_text: bool) -> str:
    links = "\n".join(
        f'<a href="{html.escape(item.url)}">{item.type.value} {index}</a>'
        for index, item in enumerate(tweet.media, start=1)
    )
    prefix = format_tweet(tweet) + "\n\n" if include_text else ""
    return f"{prefix}Media:\n{links}"


def _escape_within(text: str, limit: int) -> str:
    """HTML-escape ``text``, truncating the plain text so the result fits ``limit``."""
    escaped = html.escape(text)
    if len(escaped) <= limit:
        return escaped
    cut = limit - 1
    while cut > 0:
        escaped = html.escape(text[:cut].rstrip()) + "…"
        if len(escaped) <= limit:
            return escaped
        cut -= len(escaped) - limit
    return ""
