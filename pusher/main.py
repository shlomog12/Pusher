"""Entry point: poll the configured accounts and forward their tweets to Telegram."""

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


class TweetForwarder:
    def __init__(
        self,
        config: Config,
        sources: list[TweetSource],
        telegram: TelegramClient,
        state: StateStore,
    ) -> None:
        self._config = config
        self._sources = sources
        self._telegram = telegram
        self._state = state

    def run_once(self) -> int:
        """Check every source once. Returns the number of notifications sent.

        A failing source is logged and skipped so it doesn't block the others.
        """
        sent = 0
        for source in self._sources:
            try:
                sent += self._check(source)
            except (TweetSourceError, TelegramApiError, requests.RequestException):
                logger.exception("Checking @%s failed, will retry", source.username)
        return sent

    def run_forever(self, stop_event: threading.Event) -> None:
        for source in self._sources:
            logger.info("Watching @%s: %s", source.username, _describe(source))
        logger.info(
            "Checking every %s seconds using the %r source",
            self._config.poll_interval_seconds,
            self._config.tweet_source,
        )
        while not stop_event.is_set():
            self.run_once()
            stop_event.wait(self._config.poll_interval_seconds)

    def _check(self, source: TweetSource) -> int:
        since_id = self._state.load_last_tweet_id(source.username)
        if since_id is None:
            start_id = source.initial_since_id()
            if start_id:
                self._state.save_last_tweet_id(source.username, start_id)
            logger.info(
                "First run for @%s: starting from tweet id %s, older tweets are skipped",
                source.username,
                start_id,
            )
            return 0

        sent = 0
        newest_id = since_id
        for tweet in source.new_tweets(since_id):
            if source.wants(tweet):
                logger.info("Forwarding tweet %s", tweet.url)
                self._telegram.send_tweet(tweet)
                sent += 1
            # A merged thread can end after a later standalone tweet; never move
            # the saved id backwards, or its tweets would be sent again.
            newest_id = max(newest_id, tweet.last_id, key=int)
            self._state.save_last_tweet_id(source.username, newest_id)
        return sent


def _describe(source: TweetSource) -> str:
    if source.mentioned_usernames:
        names = ", ".join(f"@{name}" for name in source.mentioned_usernames)
        threads = ", threads as one message" if source.include_threads else ""
        return f"tweets that tag {names}{threads}"
    description = "all tweets" + (" and retweets" if source.include_retweets else "")
    return description + (", threads as one message" if source.include_threads else "")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", help="Path to a .env file (default: ./.env)")
    parser.add_argument(
        "--once", action="store_true", help="Run a single polling cycle and exit"
    )
    args = parser.parse_args(argv)

    try:
        config = Config.from_env(args.env_file)
        sources = TweetSourceFactory.create_all(config)
    except ConfigError as exc:
        sys.exit(f"Configuration error: {exc}")

    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    forwarder = TweetForwarder(
        config=config,
        sources=sources,
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
