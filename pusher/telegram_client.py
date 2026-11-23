"""Telegram Bot API client that knows how to deliver a tweet with its media."""

from __future__ import annotations

import html
import json
import logging
from dataclasses import dataclass
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

_TEXT_OPTIONS = {
    "parse_mode": "HTML",
    # Show the preview card of the first link (e.g. an article's image and
    # title), large like on X.
    "link_preview_options": json.dumps({"prefer_large_media": True}),
}

_SINGLE_MEDIA_METHODS = {
    MediaType.PHOTO: ("sendPhoto", "photo"),
    MediaType.VIDEO: ("sendVideo", "video"),
    MediaType.ANIMATION: ("sendAnimation", "animation"),
}


class TelegramApiError(RuntimeError):
    """Raised when the Telegram Bot API rejects a request."""


@dataclass(frozen=True)
class SentTweet:
    """The Telegram messages a tweet was sent as, so an edit of it can update them."""

    message_ids: tuple[int, ...]
    # The message whose text or caption is the whole tweet text, if there is one.
    text_message_id: int | None = None
    media_urls: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_ids": list(self.message_ids),
            "text_message_id": self.text_message_id,
            "media_urls": list(self.media_urls),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SentTweet":
        return cls(
            message_ids=tuple(data.get("message_ids", ())),
            text_message_id=data.get("text_message_id"),
            media_urls=tuple(data.get("media_urls", ())),
        )


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

    def send_tweet(self, tweet: Tweet) -> SentTweet:
        """Send the tweet in as few messages as Telegram allows.

        That is one message, unless the text (e.g. of a long thread) is over
        Telegram's limits, or there are more than 10 media items for one album.
        """
        texts = split_text(tweet.text)
        media_urls = tuple(item.url for item in tweet.media)
        if not tweet.media:
            # Telegram rejects empty messages; a tweet without text or media is rare.
            ids = tuple(self.send_message(text) for text in texts or [html.escape(tweet.url)])
            return SentTweet(ids, ids[0] if len(ids) == 1 else None)

        ids: list[int] = []
        caption: str | None = None
        if len(texts) <= 1 and len("".join(texts)) <= MAX_CAPTION_LENGTH:
            caption = texts[0] if texts else None
        else:
            ids.extend(self.send_message(text) for text in texts)
        text_message_id: int | None = None

        groups = [
            tweet.media[start:start + MAX_GROUP_SIZE]
            for start in range(0, len(tweet.media), MAX_GROUP_SIZE)
        ]
        for index, group in enumerate(groups):
            first = index == 0
            group_ids, with_media = self._send_media_with_fallback(
                group, caption if first else None
            )
            if first and with_media and not ids:
                text_message_id = group_ids[0]
            ids.extend(group_ids)
        return SentTweet(tuple(ids), text_message_id, media_urls)

    def update_tweet(self, sent: SentTweet, tweet: Tweet) -> SentTweet:
        """Change the messages of ``sent`` to show ``tweet``, an edit of that tweet.

        The text is edited in place when the media is unchanged and the text
        still fits one message; otherwise the old messages are replaced.
        """
        texts = split_text(tweet.text)
        media_urls = tuple(item.url for item in tweet.media)
        limit = MAX_CAPTION_LENGTH if media_urls else MAX_MESSAGE_LENGTH
        if (
            sent.text_message_id is not None
            and media_urls == sent.media_urls
            and len(texts) <= 1
            and len("".join(texts)) <= limit
            and (texts or media_urls)
        ):
            try:
                if media_urls:
                    self.edit_caption(sent.text_message_id, texts[0] if texts else "")
                else:
                    self.edit_message(sent.text_message_id, texts[0])
                return sent
            except TelegramApiError as exc:
                if "message is not modified" in str(exc):
                    return sent
                logger.info("Editing the message failed (%s), sending it again", exc)

        new = self.send_tweet(tweet)
        for message_id in sent.message_ids:
            try:
                self.delete_message(message_id)
            except (TelegramApiError, requests.RequestException) as exc:
                logger.info("Deleting message %s failed (%s)", message_id, exc)
        return new

    def _send_media_with_fallback(
        self, media: tuple[Media, ...], caption: str | None
    ) -> tuple[list[int], bool]:
        """The ids of the sent messages, and whether the media itself was sent."""
        try:
            return self._send_media(media, caption, upload=False), True
        except (TelegramApiError, requests.RequestException) as exc:
            logger.info("Sending media by URL failed (%s), uploading instead", exc)

        try:
            return self._send_media(media, caption, upload=True), True
        except (TelegramApiError, requests.RequestException) as exc:
            logger.warning("Uploading media failed (%s), sending links only", exc)

        return [self.send_message(format_media_links(media, caption))], False

    def send_message(self, text: str) -> int:
        result = self._call(
            "sendMessage",
            data={"chat_id": self._chat_id, "text": text, **_TEXT_OPTIONS},
        )
        return _message_ids(result)[0]

    def edit_message(self, message_id: int, text: str) -> None:
        self._call(
            "editMessageText",
            data={
                "chat_id": self._chat_id,
                "message_id": message_id,
                "text": text,
                **_TEXT_OPTIONS,
            },
        )

    def edit_caption(self, message_id: int, caption: str) -> None:
        self._call(
            "editMessageCaption",
            data={
                "chat_id": self._chat_id,
                "message_id": message_id,
                "caption": caption,
                "parse_mode": "HTML",
            },
        )

    def delete_message(self, message_id: int) -> None:
        self._call("deleteMessage", data={"chat_id": self._chat_id, "message_id": message_id})

    def _send_media(
        self, media: tuple[Media, ...], caption: str | None, upload: bool
    ) -> list[int]:
        files: dict[str, tuple[str, bytes]] = {}
        if len(media) == 1:
            return self._send_single(media[0], caption, upload, files)
        return self._send_group(media, caption, upload, files)

    def _send_single(
        self,
        item: Media,
        caption: str | None,
        upload: bool,
        files: dict[str, tuple[str, bytes]],
    ) -> list[int]:
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
        return _message_ids(self._call(method, data=data, files=files or None))

    def _send_group(
        self,
        media: tuple[Media, ...],
        caption: str | None,
        upload: bool,
        files: dict[str, tuple[str, bytes]],
    ) -> list[int]:
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

        return _message_ids(self._call(
            "sendMediaGroup",
            data={"chat_id": self._chat_id, "media": json.dumps(entries)},
            files=files or None,
        ))

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


def _message_ids(payload: dict[str, Any]) -> list[int]:
    """The ids of the messages in a send method's response."""
    result = payload.get("result")
    messages = result if isinstance(result, list) else [result]
    return [
        message["message_id"]
        for message in messages
        if isinstance(message, dict) and "message_id" in message
    ]


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
