"""Domain models shared between the X and Telegram layers."""

from __future__ import annotations

import re
from collections.abc import Callable
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

    def without_tag_credits(self) -> "Tweet":
        """This tweet with the reporters' tag credits removed from its text."""
        text = strip_tag_credits(self.text)
        return self if text == self.text else replace(self, text=text)

    def without_links(self) -> "Tweet":
        """This tweet with the links removed from its text."""
        text = strip_links(self.text)
        return self if text == self.text else replace(self, text=text)

    def with_thread(self, continuations: "list[Tweet]") -> "Tweet":
        """This tweet with the given follow-up tweets appended to its text and media."""
        if not continuations:
            return self
        parts = [self, *continuations]
        texts = [self.text]
        known = {self.author_username.lower(), *self.mentions}
        for part in continuations:
            texts.append(_without_reply_tags(part.text, known))
            known |= part.mentions
        return replace(
            self,
            text="\n\n".join(text for text in texts if text),
            mentions=frozenset().union(*(part.mentions for part in parts)),
            media=tuple(item for part in parts for item in part.media),
            thread_ids=self.thread_ids
            + tuple(i for part in continuations for i in (part.id, *part.thread_ids)),
        )


def _without_reply_tags(text: str, known: set[str]) -> str:
    """``text`` without the tags X puts before a reply to the earlier tweets of a thread."""
    while match := _LEADING_TAG.match(text):
        if match.group(1).lower() not in known:
            break
        text = text[match.end():]
    return text


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
_LINK = re.compile(r"[ \t]*https?://\S+")
_LEADING_TAG = re.compile(r"\s*@(\w+)\s*")
# Direction marks that X may put around tags and hashtags in right-to-left text.
_DIRECTION_MARKS = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_TAGS = r"(?:@\w+\s*)+"
# A part of a line that only credits reporters by their tags.
_TAG_CREDIT = re.compile(
    r"\s*(?:"
    rf"{_TAGS}(?:\([^)]*\))?"  # @reporter, maybe with a note like (צילום: ...)
    rf"|ה?פרסום\s+של\s+{_TAGS}"  # פרסום של @reporter
    rf"|{_TAGS}עם\s+הפרטים"  # @reporter עם הפרטים
    rf"|עם\s+הפרטים\s+{_TAGS}"  # עם הפרטים @reporter
    rf"|(?:#\w+\s*)+עם\s+{_TAGS}"  # #show עם @host
    r")\s*"
)


def strip_credits(text: str, prefixes: tuple[str, ...]) -> str:
    """Remove every line that starts with one of ``prefixes`` from ``text``."""
    if not prefixes:
        return text
    lowered = tuple(prefix.lower() for prefix in prefixes)
    return _map_lines(text, lambda line: None if line.strip().lower().startswith(lowered) else line)


def strip_tag_credits(text: str) -> str:
    """Remove the parts of lines that only credit reporters by their tags.

    A line, or a part of it after a "|", is removed when it holds only tags, or
    tags in a credit such as "פרסום של @reporter", "@reporter עם הפרטים" or
    "#show עם @host". A link in such a part is kept, and so is a tag anywhere
    in the text of the tweet itself.
    """
    return _map_lines(text, _strip_line_tag_credits)


def _strip_line_tag_credits(line: str) -> str | None:
    parts = line.split("|")
    kept = []
    for part in parts:
        links = [link.strip() for link in _LINK.findall(part)]
        if _TAG_CREDIT.fullmatch(_DIRECTION_MARKS.sub("", _LINK.sub("", part))):
            kept.extend(links)
        else:
            kept.append(part)
    if kept == parts:
        return line
    kept = [part.strip() for part in kept if part.strip()]
    return " | ".join(kept) or None


def strip_links(text: str) -> str:
    """Remove the links from ``text``, and the lines and paragraphs left empty."""

    def strip(line: str) -> str | None:
        stripped = _LINK.sub("", line)
        return line if stripped == line else stripped.strip() or None

    return _map_lines(text, strip)


def _map_lines(text: str, change: Callable[[str], str | None]) -> str:
    """Replace each line by ``change(line)``, dropping it on None, and the empty paragraphs."""
    paragraphs = []
    for paragraph in _PARAGRAPH_BREAK.split(text):
        kept = [changed for line in paragraph.split("\n") if (changed := change(line)) is not None]
        if any(line.strip() for line in kept):
            paragraphs.append("\n".join(kept).rstrip())
    return "\n\n".join(paragraphs).strip()
