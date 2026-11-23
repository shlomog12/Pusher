from dataclasses import replace

from pusher.config import Config
from pusher.main import TweetForwarder
from pusher.models import Tweet
from pusher.sources.base import TweetSource, TweetSourceError
from pusher.state import StateStore


class FakeSource(TweetSource):
    def __init__(self, username, tweets, latest="50", mentioned_usernames=(), error=None):
        super().__init__(username, mentioned_usernames)
        self.tweets = tweets
        self.latest = latest
        self.error = error

    def initial_since_id(self):
        return self.latest

    def new_tweets(self, since_id):
        if self.error:
            raise self.error
        return [t for t in self.tweets if int(t.id) > int(since_id)]


class FakeTelegram:
    def __init__(self):
        self.sent = []
        self.texts = []

    def send_tweet(self, tweet):
        self.sent.append(tweet.id)
        self.texts.append(tweet.text)


def _config(tmp_path):
    return Config(
        watched_username="Page",
        mentioned_usernames=("me",),
        telegram_bot_token="b",
        telegram_chat_id="1",
        state_file=tmp_path / "state.json",
    )


def _tweet(tweet_id, mentions, author="Page"):
    return Tweet(id=tweet_id, author_username=author, text="hi", mentions=frozenset(mentions))


def _forwarder(tmp_path, *sources):
    config = _config(tmp_path)
    state = StateStore(config.state_file)
    telegram = FakeTelegram()
    return TweetForwarder(config, list(sources), telegram, state), telegram, state


def test_first_run_records_latest_without_notifying(tmp_path):
    source = FakeSource("Page", [_tweet("40", {"me"})], mentioned_usernames=["me"])
    forwarder, telegram, state = _forwarder(tmp_path, source)

    assert forwarder.run_once() == 0
    assert telegram.sent == []
    assert state.load_last_tweet_id("page") == "50"


def test_forwards_only_tweets_that_tag_a_mentioned_user(tmp_path):
    tweets = [
        _tweet("51", {"me"}),
        _tweet("52", {"other"}),
        _tweet("53", {"me", "friend"}),
        _tweet("54", {"friend"}),
    ]
    source = FakeSource("Page", tweets, mentioned_usernames=["Me", "@friend"])
    forwarder, telegram, state = _forwarder(tmp_path, source)
    state.save_last_tweet_id("Page", "50")

    assert forwarder.run_once() == 3
    assert telegram.sent == ["51", "53", "54"]
    assert state.load_last_tweet_id("page") == "54"

    assert forwarder.run_once() == 0
    assert len(telegram.sent) == 3


def test_source_without_mentions_forwards_every_tweet(tmp_path):
    mine = FakeSource("me", [_tweet("61", set(), "me"), _tweet("62", {"x"}, "me")])
    forwarder, telegram, state = _forwarder(tmp_path, mine)
    state.save_last_tweet_id("me", "60")

    assert forwarder.run_once() == 2
    assert telegram.sent == ["61", "62"]


def test_each_source_keeps_its_own_state_and_failures_are_isolated(tmp_path):
    broken = FakeSource("Page", [], error=TweetSourceError("down"), mentioned_usernames=["me"])
    mine = FakeSource("me", [_tweet("71", set(), "me")])
    forwarder, telegram, state = _forwarder(tmp_path, broken, mine)
    state.save_last_tweet_id("Page", "50")
    state.save_last_tweet_id("me", "70")

    assert forwarder.run_once() == 1
    assert telegram.sent == ["71"]
    assert state.load_last_tweet_id("page") == "50"
    assert state.load_last_tweet_id("me") == "71"


def test_merged_thread_moves_state_to_its_newest_tweet(tmp_path):
    thread = _tweet("81", {"me"}).with_thread([_tweet("85", set())])
    source = FakeSource("Page", [thread, _tweet("83", {"me"})], mentioned_usernames=["me"])
    forwarder, telegram, state = _forwarder(tmp_path, source)
    state.save_last_tweet_id("Page", "80")

    assert forwarder.run_once() == 2
    assert state.load_last_tweet_id("page") == "85"


def test_credits_are_removed_from_the_sent_text(tmp_path):
    config = replace(_config(tmp_path), credit_prefixes=("#show",))
    state = StateStore(config.state_file)
    state.save_last_tweet_id("page", "1")
    tweet = replace(_tweet("2", {"me"}), text="news\n\n#show with @me")
    telegram = FakeTelegram()

    TweetForwarder(config, [FakeSource("Page", [tweet])], telegram, state).run_once()

    assert telegram.texts == ["news"]
