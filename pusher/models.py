"""Domain models shared between the X and Telegram layers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import Enum


class MediaType(str, Enum):
    PHOTO = "photo"
    VIDEO = "video"
    ANIMATION = "animation"


@dataclass(frozen=True)
class Media:
    type: MediaType
    url: str
    thumbnail_url: str | None = None


@dataclass(frozen=True)
class Tweet:
    id: str
    author_username: str
    text: str
    mentions: frozenset[str] = field(default_factory=frozenset)
    media: tuple[Media, ...] = ()
    created_at: str | None = None
    retweeted_username: str | None = None
    in_reply_to_id: str | None = None
    in_reply_to_username: str | None = None
    # Ids of the author's follow-up tweets merged into this one (see ``with_thread``).
    thread_ids: tuple[str, ...] = ()
    # On X every edit is a new tweet; this is the id of the first version when
    # this tweet is an edit of it.
    first_version_id: str | None = None

    @property
    def url(self) -> str:
        return f"https://x.com/{self.author_username}/status/{self.id}"

    @property
    def is_retweet(self) -> bool:
        return self.retweeted_username is not None

    @property
    def is_reply(self) -> bool:
        return self.in_reply_to_id is not None

    @property
    def is_self_reply(self) -> bool:
        """A reply to the author's own tweet, i.e. the next tweet of a thread."""
        return (
            self.is_reply
            and (self.in_reply_to_username or "").lower() == self.author_username.lower()
        )

    @property
    def edit_root_id(self) -> str:
        """The id shared by all versions of this tweet."""
        return self.first_version_id or self.id

    @property
    def is_edit(self) -> bool:
        return self.edit_root_id != self.id

    @property
    def last_id(self) -> str:
        """The newest tweet id this tweet covers, including merged thread tweets."""
        return max((self.id, *self.thread_ids), key=int)

    def mentions_user(self, username: str) -> bool:
        return username.lower() in self.mentions

    def without_credits(self, prefixes: tuple[str, ...]) -> "Tweet":
        """This tweet with the credit lines that start with ``prefixes`` removed."""
        text = strip_credits(self.text, prefixes)
        return self if text == self.text else replace(self, text=text)

    def with_thread(self, continuations: "list[Tweet]") -> "Tweet":
        """This tweet with the given follow-up tweets appended to its text and media."""
        if not continuations:
            return self
        parts = [self, *continuations]
        return replace(
            self,
            text="\n\n".join(part.text for part in parts if part.text),
            mentions=frozenset().union(*(part.mentions for part in parts)),
            media=tuple(item for part in parts for item in part.media),
            thread_ids=self.thread_ids
            + tuple(i for part in continuations for i in (part.id, *part.thread_ids)),
        )


def merge_threads(tweets: list[Tweet]) -> list[Tweet]:
    """Merge every self-reply into the tweet that starts its thread.

    ``tweets`` must be ordered oldest first. A self-reply whose parent is not in
    the list (the thread was continued later) is kept as a tweet of its own.
    """
    roots: dict[str, Tweet] = {}
    continuations: dict[str, list[Tweet]] = {}
    root_of: dict[str, str] = {}
    for tweet in tweets:
        parent = tweet.in_reply_to_id
        if tweet.is_self_reply and parent in root_of:
            root_id = root_of[parent]
            continuations[root_id].append(tweet)
        else:
            root_id = tweet.id
            roots[root_id] = tweet
            continuations[root_id] = []
        root_of[tweet.id] = root_id
    return [root.with_thread(continuations[root_id]) for root_id, root in roots.items()]


def latest_versions(tweets: list[Tweet]) -> list[Tweet]:
    """Drop every tweet that has a newer edit in the list, keeping the list order."""
    newest: dict[str, str] = {}
    for tweet in tweets:
        current = newest.get(tweet.edit_root_id)
        if current is None or int(tweet.id) > int(current):
            newest[tweet.edit_root_id] = tweet.id
    return [tweet for tweet in tweets if newest[tweet.edit_root_id] == tweet.id]


_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")


def strip_credits(text: str, prefixes: tuple[str, ...]) -> str:
    """Remove every line that starts with one of ``prefixes`` and the rest of its paragraph.

    A credit such as "#show with @host" usually closes a tweet, sometimes
    followed by a link. Tags elsewhere in the text are kept.
    """
    if not prefixes:
        return text
    lowered = tuple(prefix.lower() for prefix in prefixes)
    paragraphs = []
    for paragraph in _PARAGRAPH_BREAK.split(text):
        kept = []
        for line in paragraph.split("\n"):
            if line.strip().lower().startswith(lowered):
                break
            kept.append(line)
        if any(line.strip() for line in kept):
            paragraphs.append("\n".join(kept).rstrip())
    return "\n\n".join(paragraphs).strip()
