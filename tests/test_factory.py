import pytest

from pusher.config import Config, ConfigError
from pusher.sources import ApiTweetSource, ScraperTweetSource, TweetSourceFactory


def _config(**overrides):
    values = dict(
        watched_username="page",
        mentioned_usernames=("me",),
        telegram_bot_token="b",
        telegram_chat_id="1",
    )
    values.update(overrides)
    return Config(**values)


def test_creates_api_source():
    [source] = TweetSourceFactory.create_all(_config(tweet_source="api", twitter_bearer_token="t"))
    assert isinstance(source, ApiTweetSource)
    assert source.mentioned_usernames == ("me",)


def test_creates_scraper_source():
    [source] = TweetSourceFactory.create_all(
        _config(tweet_source="scraper", scraper_auth_token="c")
    )
    assert isinstance(source, ScraperTweetSource)


def test_creates_a_source_per_feed():
    config = _config(
        twitter_bearer_token="t",
        my_username="me",
        forward_my_tweets=True,
        forward_my_retweets=True,
    )
    watched, mine = TweetSourceFactory.create_all(config)

    assert (watched.username, watched.mentioned_usernames) == ("page", ("me",))
    assert (mine.username, mine.mentioned_usernames, mine.include_retweets) == ("me", (), True)


def test_missing_source_credentials_raise_config_error():
    with pytest.raises(ConfigError, match="TWITTER_BEARER_TOKEN"):
        TweetSourceFactory.create_all(_config(tweet_source="api"))
    with pytest.raises(ConfigError, match="X_AUTH_TOKEN"):
        TweetSourceFactory.create_all(_config(tweet_source="scraper"))


def test_unknown_source_raises_config_error():
    with pytest.raises(ConfigError, match="Unknown TWEET_SOURCE"):
        TweetSourceFactory.create_all(_config(tweet_source="carrier-pigeon"))
