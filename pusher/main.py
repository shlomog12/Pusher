"""Entry point: poll the watched account and forward tweets that mention you."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

import requests

from pusher.config import Config, ConfigError
from pusher.sources import TweetSource, TweetSourceError, TweetSourceFactory
from pusher.state import StateStore
from pusher.telegram_client import TelegramApiError, TelegramClient

logger = logging.getLogger("pusher")


class MentionForwarder:
    def __init__(
        self,
        config: Config,
        source: TweetSource,
        telegram: TelegramClient,
        state: StateStore,
    ) -> None:
        self._config = config
        self._source = source
        self._telegram = telegram
        self._state = state

    def run_once(self) -> int:
        """Check for new tweets once. Returns the number of notifications sent."""
        watched = self._config.watched_username
        since_id = self._state.load_last_tweet_id(watched)
        if since_id is None:
            latest = self._source.latest_tweet_id()
            if latest:
                self._state.save_last_tweet_id(watched, latest)
            logger.info(
                "First run for @%s: starting from tweet %s, older tweets are skipped",
                watched,
                latest,
            )
            return 0

        sent = 0
        for tweet in self._source.new_tweets(since_id):
            if tweet.mentions_user(self._config.my_username):
                logger.info("Forwarding tweet %s", tweet.url)
                self._telegram.send_tweet(tweet)
                sent += 1
            self._state.save_last_tweet_id(watched, tweet.id)
        return sent

    def run_forever(self, stop_event: threading.Event) -> None:
        logger.info(
            "Watching @%s for mentions of @%s every %s seconds using the %r source",
            self._config.watched_username,
            self._config.my_username,
            self._config.poll_interval_seconds,
            self._config.tweet_source,
        )
        while not stop_event.is_set():
            try:
                self.run_once()
            except (TweetSourceError, TelegramApiError, requests.RequestException):
                logger.exception("Polling cycle failed, will retry")
            stop_event.wait(self._config.poll_interval_seconds)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", help="Path to a .env file (default: ./.env)")
    parser.add_argument(
        "--once", action="store_true", help="Run a single polling cycle and exit"
    )
    args = parser.parse_args(argv)

    try:
        config = Config.from_env(args.env_file)
        source = TweetSourceFactory.create(config)
    except ConfigError as exc:
        sys.exit(f"Configuration error: {exc}")

    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    forwarder = MentionForwarder(
        config=config,
        source=source,
        telegram=TelegramClient(config.telegram_bot_token, config.telegram_chat_id),
        state=StateStore(config.state_file),
    )

    if args.once:
        forwarder.run_once()
        return

    stop_event = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop_event.set())
    forwarder.run_forever(stop_event)
