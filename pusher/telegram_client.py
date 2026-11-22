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
MAX_GROUP_SIZE = 10
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
# A host may resolve to an IPv6 address that never answers; a short connect
# timeout makes requests fall back to the next (IPv4) address quickly instead
# of waiting minutes for the operating system to give up.
CONNECT_TIMEOUT_SECONDS = 10

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
        self._timeout = (CONNECT_TIMEOUT_SECONDS, timeout)

    def send_tweet(self, tweet: Tweet) -> None:
        """Send the tweet in as few messages as Telegram allows.

        That is one message, unless the text (e.g. of a long thread) is over
        Telegram's limits, or there are more than 10 media items for one album.
        """
        texts = split_text(tweet.text)
        if not tweet.media:
            # Telegram rejects empty messages; a tweet without text or media is rare.
            for text in texts or [html.escape(tweet.url)]:
                self.send_message(text)
            return

        caption: str | None = None
        if len(texts) == 1 and len(texts[0]) <= MAX_CAPTION_LENGTH:
            caption = texts[0]
        else:
            for text in texts:
                self.send_message(text)

        groups = [
            tweet.media[start:start + MAX_GROUP_SIZE]
            for start in range(0, len(tweet.media), MAX_GROUP_SIZE)
        ]
        for index, group in enumerate(groups):
            self._send_media_with_fallback(group, caption if index == 0 else None)

    def _send_media_with_fallback(
        self, media: tuple[Media, ...], caption: str | None
    ) -> None:
        try:
            self._send_media(media, caption, upload=False)
            return
        except (TelegramApiError, requests.RequestException) as exc:
            logger.info("Sending media by URL failed (%s), uploading instead", exc)

        try:
            self._send_media(media, caption, upload=True)
            return
        except (TelegramApiError, requests.RequestException) as exc:
            logger.warning("Uploading media failed (%s), sending links only", exc)

        self.send_message(format_media_links(media, caption))

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


def format_media_links(media: tuple[Media, ...], text: str | None = None) -> str:
    """Links to the media, after ``text`` (already HTML-escaped) when given."""
    links = "\n".join(
        f'<a href="{html.escape(item.url)}">{item.type.value} {index}</a>'
        for index, item in enumerate(media, start=1)
    )
    return f"{text}\n\n{links}" if text else links


def split_text(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """HTML-escape ``text`` and split it into parts of at most ``limit`` characters.

    Parts end at a paragraph break (e.g. between the tweets of a thread) where
    possible, otherwise at a line break or a space.
    """
    parts = []
    rest = text.strip()
    while rest:
        escaped = html.escape(rest)
        if len(escaped) <= limit:
            parts.append(escaped)
            break
        cut = _longest_fitting_prefix(rest, limit)
        for separator in ("\n\n", "\n", " "):
            position = rest.rfind(separator, 0, cut + len(separator))
            # Don't settle for a tiny first part just to end at a separator.
            if position > cut // 2:
                cut = position
                break
        parts.append(html.escape(rest[:cut].rstrip()))
        rest = rest[cut:].strip()
    return parts


def _longest_fitting_prefix(text: str, limit: int) -> int:
    """Length of the longest prefix of ``text`` whose escaped form fits ``limit``."""
    low, high = 1, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if len(html.escape(text[:mid].rstrip())) <= limit:
            low = mid
        else:
            high = mid - 1
    return low
