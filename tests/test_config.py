import pytest

from pusher.config import Config, ConfigError, Feed

BASE_ENV = {
    "TWITTER_WATCHED_USERNAME": "@page",
    "TELEGRAM_BOT_TOKEN": "b",
    "TELEGRAM_CHAT_ID": "1",
}
SETTINGS = [
    "TWITTER_MY_USERNAME",
    "TWITTER_MENTIONED_USERNAMES",
    "FORWARD_MY_TWEETS",
    "FORWARD_MY_RETWEETS",
    "INCLUDE_REPLIES",
]


@pytest.fixture
def env(monkeypatch, tmp_path):
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)
    for name, value in BASE_ENV.items():
        monkeypatch.setenv(name, value)

    def load(**values):
        for name, value in values.items():
            monkeypatch.setenv(name, value)
        return Config.from_env(str(tmp_path / "missing.env"))

    return load


def test_mentioned_usernames_are_parsed_as_a_list(env):
    config = env(TWITTER_MENTIONED_USERNAMES="@alice, bob ,,carol")

    assert config.mentioned_usernames == ("alice", "bob", "carol")
    assert config.feeds() == [Feed("page", ("alice", "bob", "carol"), False, False)]


def test_mentioned_usernames_default_to_my_username(env):
    config = env(TWITTER_MY_USERNAME="@me")

    assert config.mentioned_usernames == ("me",)
    assert len(config.feeds()) == 1


def test_forward_my_tweets_adds_a_feed_for_my_account(env):
    config = env(
        TWITTER_MY_USERNAME="me",
        TWITTER_MENTIONED_USERNAMES="me,friend",
        FORWARD_MY_TWEETS="true",
        FORWARD_MY_RETWEETS="true",
    )

    assert config.feeds()[1] == Feed("me", (), include_retweets=True, include_replies=False)


def test_include_replies_applies_to_every_feed(env):
    config = env(TWITTER_MY_USERNAME="me", FORWARD_MY_TWEETS="true", INCLUDE_REPLIES="true")

    assert [feed.include_replies for feed in config.feeds()] == [True, True]


def test_missing_mentioned_usernames_raise(env):
    with pytest.raises(ConfigError, match="TWITTER_MENTIONED_USERNAMES"):
        env()


def test_forward_my_tweets_requires_my_username(env):
    with pytest.raises(ConfigError, match="TWITTER_MY_USERNAME"):
        env(TWITTER_MENTIONED_USERNAMES="friend", FORWARD_MY_TWEETS="true")


def test_credit_prefixes_are_parsed_as_a_list(env, monkeypatch):
    monkeypatch.delenv("CREDIT_PREFIXES", raising=False)
    assert env(TWITTER_MENTIONED_USERNAMES="me").credit_prefixes == ()

    config = env(CREDIT_PREFIXES=" #מהדורתכאןחדשות , כאן חדשות ברשת ב' ,")

    assert config.credit_prefixes == ("#מהדורתכאןחדשות", "כאן חדשות ברשת ב'")
