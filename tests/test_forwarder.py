from pusher.config import Config
from pusher.main import MentionForwarder
from pusher.models import Tweet
from pusher.state import StateStore


class FakeTwitter:
    def __init__(self, tweets, latest="50"):
        self.tweets = tweets
        self.latest = latest

    def get_user_id(self, username):
        return "123"

    def get_latest_tweet_id(self, user_id):
        return self.latest

    def iter_new_tweets(self, user_id, author_username, since_id, **_):
        return iter([t for t in self.tweets if int(t.id) > int(since_id)])


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send_tweet(self, tweet):
        self.sent.append(tweet.id)


def _config(tmp_path):
    return Config(
        twitter_bearer_token="t",
        watched_username="Page",
        my_username="me",
        telegram_bot_token="b",
        telegram_chat_id="1",
        state_file=tmp_path / "state.json",
    )


def _tweet(tweet_id, mentions):
    return Tweet(id=tweet_id, author_username="Page", text="hi", mentions=frozenset(mentions))


def test_first_run_records_latest_without_notifying(tmp_path):
    config = _config(tmp_path)
    state = StateStore(config.state_file)
    telegram = FakeTelegram()
    forwarder = MentionForwarder(config, FakeTwitter([_tweet("40", {"me"})]), telegram, state)

    assert forwarder.run_once() == 0
    assert telegram.sent == []
    assert state.load_last_tweet_id("page") == "50"


def test_forwards_only_tweets_that_mention_me(tmp_path):
    config = _config(tmp_path)
    state = StateStore(config.state_file)
    state.save_last_tweet_id("Page", "50")
    tweets = [_tweet("51", {"me"}), _tweet("52", {"other"}), _tweet("53", {"me", "other"})]
    telegram = FakeTelegram()
    forwarder = MentionForwarder(config, FakeTwitter(tweets), telegram, state)

    assert forwarder.run_once() == 2
    assert telegram.sent == ["51", "53"]
    assert state.load_last_tweet_id("page") == "53"

    assert forwarder.run_once() == 0
    assert telegram.sent == ["51", "53"]
