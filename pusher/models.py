"""Domain models shared between the X and Telegram layers."""

from __future__ import annotations

from dataclasses import dataclass, field
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

    @property
    def url(self) -> str:
        return f"https://x.com/{self.author_username}/status/{self.id}"

    def mentions_user(self, username: str) -> bool:
        return username.lower() in self.mentions
