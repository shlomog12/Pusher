from dataclasses import replace

from pusher.config import Config
from pusher.main import TweetForwarder
from pusher.models import Tweet
from pusher.sources.base import TweetSource, TweetSourceError
from pusher.state import StateStore
from pusher.telegram_client import SentTweet


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
        self.updated = []

    def send_tweet(self, tweet):
        self.sent.append(tweet.id)
        self.texts.append(tweet.text)
        return SentTweet((len(self.sent),), len(self.sent))

    def update_tweet(self, sent, tweet):
        self.updated.append((sent.message_ids, tweet.id))
        return sent


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


def test_tag_credits_are_removed_from_the_sent_text_only_when_configured(tmp_path):
    text = "news | @reporter עם הפרטים\n\n#show עם @host"
    for remove_tag_credits, expected in ((True, "news"), (False, text)):
        config = replace(_config(tmp_path / str(remove_tag_credits)), remove_tag_credits=remove_tag_credits)
        state = StateStore(config.state_file)
        state.save_last_tweet_id("page", "1")
        telegram = FakeTelegram()
        tweet = replace(_tweet("2", {"me"}), text=text)

        TweetForwarder(config, [FakeSource("Page", [tweet])], telegram, state).run_once()

        assert telegram.texts == [expected]


def test_links_are_removed_from_the_sent_text_only_when_configured(tmp_path):
    text = "news\n\nhttps://www.kan.org.il/content/kan-news/politic/live-1122206/"
    for remove_links, expected in ((True, "news"), (False, text)):
        config = replace(_config(tmp_path / str(remove_links)), remove_links=remove_links)
        state = StateStore(config.state_file)
        state.save_last_tweet_id("page", "1")
        telegram = FakeTelegram()
        tweet = replace(_tweet("2", {"me"}), text=text)

        TweetForwarder(config, [FakeSource("Page", [tweet])], telegram, state).run_once()

        assert telegram.texts == [expected]


def _edit(tweet_id, first_version_id):
    return replace(_tweet(tweet_id, {"me"}), first_version_id=first_version_id)


def test_only_the_newest_version_of_an_edited_tweet_is_sent(tmp_path):
    versions = [_tweet("91", {"me"}), _edit("92", "91"), _edit("93", "91"), _tweet("94", {"me"})]
    source = FakeSource("Page", versions, mentioned_usernames=["me"])
    forwarder, telegram, state = _forwarder(tmp_path, source)
    state.save_last_tweet_id("Page", "90")

    assert forwarder.run_once() == 2
    assert telegram.sent == ["93", "94"]
    assert state.load_last_tweet_id("page") == "94"


def test_a_later_edit_updates_the_message_already_sent(tmp_path):
    source = FakeSource("Page", [_tweet("91", {"me"})], mentioned_usernames=["me"])
    forwarder, telegram, state = _forwarder(tmp_path, source)
    state.save_last_tweet_id("Page", "90")
    forwarder.run_once()

    source.tweets.append(_edit("95", "91"))
    forwarder.run_once()

    assert telegram.sent == ["91"]
    assert telegram.updated == [((1,), "95")]
    assert state.load_sent_messages("91") == SentTweet((1,), 1).to_dict()


def test_an_edit_of_a_tweet_that_was_not_sent_is_sent(tmp_path):
    source = FakeSource("Page", [_edit("95", "91")], mentioned_usernames=["me"])
    forwarder, telegram, state = _forwarder(tmp_path, source)
    state.save_last_tweet_id("Page", "94")

    forwarder.run_once()

    assert telegram.sent == ["95"]
    assert telegram.updated == []


def _retweet(tweet_id, retweeted_id):
    return replace(
        _tweet(tweet_id, set(), author="me"), retweeted_username="Page", retweeted_id=retweeted_id
    )


def test_a_retweet_of_a_tweet_already_sent_is_skipped(tmp_path):
    page = FakeSource("Page", [_tweet("60", {"me"})], mentioned_usernames=["me"])
    mine = FakeSource("me", [_retweet("61", "60"), _retweet("62", "55")])
    forwarder, telegram, state = _forwarder(tmp_path, page, mine)
    state.save_last_tweet_id("page", "1")
    state.save_last_tweet_id("me", "1")

    assert forwarder.run_once() == 2
    assert telegram.sent == ["60", "62"]


def test_a_tweet_already_sent_as_a_retweet_is_skipped(tmp_path):
    mine = FakeSource("me", [_retweet("61", "60")])
    page = FakeSource("Page", [_tweet("60", {"me"})], mentioned_usernames=["me"])
    forwarder, telegram, state = _forwarder(tmp_path, mine, page)
    state.save_last_tweet_id("page", "1")
    state.save_last_tweet_id("me", "1")

    assert forwarder.run_once() == 1
    assert telegram.sent == ["61"]
    assert state.load_last_tweet_id("page") == "60"
