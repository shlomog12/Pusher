"""Entry point: poll the configured accounts and forward their tweets to Telegram."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

import requests

from pusher.config import Config, ConfigError
from pusher.models import Tweet, latest_versions
from pusher.sources import TweetSource, TweetSourceError, TweetSourceFactory
from pusher.state import StateStore
from pusher.telegram_client import SentTweet, TelegramApiError, TelegramClient

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
        # An edit made shortly after posting arrives with the versions before it;
        # only the newest one is sent.
        for tweet in latest_versions(source.new_tweets(since_id)):
            if source.wants(tweet) and self._forward(tweet):
                sent += 1
            # A merged thread can end after a later standalone tweet; never move
            # the saved id backwards, or its tweets would be sent again.
            newest_id = max(newest_id, tweet.last_id, key=int)
            self._state.save_last_tweet_id(source.username, newest_id)
        return sent

    def _forward(self, tweet: Tweet) -> bool:
        """Send the tweet, after the tweet it quotes unless that was already sent.

        Returns False when the tweet itself was already sent, e.g. as a retweet.
        """
        if tweet.quoted is None:
            return self._send(tweet)
        self._send(tweet.quoted)
        # A quote without words of its own adds nothing to the quoted tweet.
        return self._send(tweet) if tweet.text or tweet.media else False

    def _send(self, tweet: Tweet) -> bool:
        """Send the tweet, or update the messages already sent for an earlier version.

        Returns False when the tweet was already sent.
        """
        text_tweet = tweet.without_credits(self._config.credit_prefixes)
        if self._config.remove_tag_credits:
            text_tweet = text_tweet.without_tag_credits()
        if self._config.remove_links:
            text_tweet = text_tweet.without_links()
        previous = next(
            (sent for key in tweet.sent_keys if (sent := self._state.load_sent_messages(key))),
            None,
        )
        if previous is not None and not tweet.is_edit:
            logger.info("Skipping tweet %s, it was already sent", tweet.url)
            return False
        if previous is None:
            logger.info("Forwarding tweet %s", tweet.url)
            sent = self._telegram.send_tweet(text_tweet)
        else:
            logger.info("Updating the message of edited tweet %s", tweet.url)
            sent = self._telegram.update_tweet(SentTweet.from_dict(previous), text_tweet)
        for key in tweet.sent_keys:
            self._state.save_sent_messages(key, sent.to_dict())
        return True


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
