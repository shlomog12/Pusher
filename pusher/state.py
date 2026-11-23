"""Persists the id of the last processed tweet so restarts do not resend tweets."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

# Key of the messages sent per tweet; "#" can't appear in an X username.
_MESSAGES_KEY = "#messages"
# Enough to cover the tweets that can still be edited (X allows edits for an hour).
MAX_REMEMBERED_TWEETS = 200


class StateStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load_last_tweet_id(self, watched_username: str) -> str | None:
        return self._read().get(watched_username.lower())

    def load_sent_messages(self, tweet_id: str) -> dict[str, Any] | None:
        """What was sent to Telegram for the tweet, by the id of its first version."""
        return self._read().get(_MESSAGES_KEY, {}).get(tweet_id)

    def save_sent_messages(self, tweet_id: str, messages: dict[str, Any]) -> None:
        state = self._read()
        sent = state.get(_MESSAGES_KEY, {})
        sent[tweet_id] = messages
        newest = sorted(sent, key=int)[-MAX_REMEMBERED_TWEETS:]
        state[_MESSAGES_KEY] = {key: sent[key] for key in newest}
        self._write(state)

    def save_last_tweet_id(self, watched_username: str, tweet_id: str) -> None:
        state = self._read()
        state[watched_username.lower()] = tweet_id
        self._write(state)

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        with self._path.open(encoding="utf-8") as handle:
            return json.load(handle)

    def _write(self, state: dict[str, Any]) -> None:
        directory = self._path.parent
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2)
            os.replace(tmp_name, self._path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
