from pusher.models import MediaType
from pusher.sources.api import parse_tweet


def _raw_tweet(**overrides):
    tweet = {
        "id": "100",
        "text": "Thanks @Me &amp; friends https://t.co/link https://t.co/pic",
        "entities": {
            "mentions": [{"username": "Me"}],
            "urls": [
                {"url": "https://t.co/link", "expanded_url": "https://example.com"},
                {"url": "https://t.co/pic", "media_key": "3_1"},
            ],
        },
        "attachments": {"media_keys": ["3_1", "7_1"]},
    }
    tweet.update(overrides)
    return tweet


MEDIA = {
    "3_1": {"media_key": "3_1", "type": "photo", "url": "https://pbs.twimg.com/a.jpg"},
    "7_1": {
        "media_key": "7_1",
        "type": "video",
        "preview_image_url": "https://pbs.twimg.com/thumb.jpg",
        "variants": [
            {"content_type": "application/x-mpegURL", "url": "https://video/x.m3u8"},
            {"content_type": "video/mp4", "bit_rate": 256000, "url": "https://video/low.mp4"},
            {"content_type": "video/mp4", "bit_rate": 2176000, "url": "https://video/high.mp4"},
        ],
    },
}


def test_parse_tweet_cleans_text_and_detects_mentions():
    tweet = parse_tweet(_raw_tweet(), "page", MEDIA)

    assert tweet.text == "Thanks @Me & friends https://example.com"
    assert tweet.mentions_user("me")
    assert not tweet.mentions_user("someone_else")
    assert tweet.url == "https://x.com/page/status/100"


def test_parse_tweet_extracts_photo_and_best_video_variant():
    tweet = parse_tweet(_raw_tweet(), "page", MEDIA)

    assert [m.type for m in tweet.media] == [MediaType.PHOTO, MediaType.VIDEO]
    assert tweet.media[0].url == "https://pbs.twimg.com/a.jpg"
    assert tweet.media[1].url == "https://video/high.mp4"


def test_parse_tweet_prefers_long_form_note_text():
    raw = _raw_tweet(
        entities={},
        note_tweet={
            "text": "A very long tweet for @me",
            "entities": {"mentions": [{"username": "me"}]},
        },
    )
    tweet = parse_tweet(raw, "page", {})

    assert tweet.text == "A very long tweet for @me"
    assert tweet.mentions_user("ME")


def test_animated_gif_maps_to_animation():
    media = {"16_1": {"type": "animated_gif", "variants": [
        {"content_type": "video/mp4", "bit_rate": 0, "url": "https://video/gif.mp4"}
    ]}}
    tweet = parse_tweet(_raw_tweet(attachments={"media_keys": ["16_1"]}), "page", media)

    assert tweet.media[0].type is MediaType.ANIMATION


# --- ApiTweetSource (recent search) ---

from datetime import datetime, timedelta, timezone

import pytest

from pusher.sources.api import (
    ApiTweetSource,
    TwitterApiError,
    datetime_from_snowflake,
    snowflake_from_datetime,
)


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)
        self.headers = {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.headers = {}
        self.calls = []
        self._responses = list(responses)

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return self._responses.pop(0)


def _source(session, **kwargs):
    return ApiTweetSource("Page", "me", "token", session=session, **kwargs)


def _page(ids, next_token=None):
    meta = {"next_token": next_token} if next_token else {}
    return FakeResponse(200, {"data": [{"id": i, "text": "hi @me"} for i in ids], "meta": meta})


def test_search_query_filters_by_author_and_mention():
    assert _source(None).search_query() == "from:Page @me -is:retweet"
    assert (
        _source(None, include_retweets=True, include_replies=False).search_query()
        == "from:Page @me -is:reply"
    )


def test_new_tweets_searches_since_id_and_follows_pagination():
    session = FakeSession([_page(["105", "104"], next_token="abc"), _page(["103"])])
    tweets = _source(session).new_tweets("100")

    assert [t.id for t in tweets] == ["103", "104", "105"]
    url, params = session.calls[0]
    assert url.endswith("/tweets/search/recent")
    assert params["query"] == "from:Page @me -is:retweet"
    assert params["since_id"] == "100"
    assert session.calls[1][1]["next_token"] == "abc"


def test_rejected_since_id_falls_back_to_start_time():
    old_id = snowflake_from_datetime(datetime.now(timezone.utc) - timedelta(days=30))
    recent_id = snowflake_from_datetime(datetime.now(timezone.utc) - timedelta(days=1))
    session = FakeSession(
        [FakeResponse(400, {"title": "Invalid Request"}), _page([recent_id, "999"])]
    )

    tweets = _source(session).new_tweets(old_id)

    assert [t.id for t in tweets] == [recent_id]
    retry_params = session.calls[1][1]
    assert "since_id" not in retry_params
    start = datetime.strptime(retry_params["start_time"], "%Y-%m-%dT%H:%M:%SZ")
    assert start.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc) - timedelta(days=7)


def test_other_errors_are_raised():
    session = FakeSession([FakeResponse(401, {"title": "Unauthorized"})])
    with pytest.raises(TwitterApiError):
        _source(session).new_tweets("100")


def test_initial_since_id_uses_current_time_without_api_calls():
    session = FakeSession([])
    start_id = _source(session).initial_since_id()

    assert session.calls == []
    age = datetime.now(timezone.utc) - datetime_from_snowflake(start_id)
    assert timedelta(0) <= age < timedelta(seconds=5)


def test_snowflake_round_trip_and_epoch():
    assert datetime_from_snowflake("0") == datetime(
        2010, 11, 4, 1, 42, 54, 657000, tzinfo=timezone.utc
    )
    moment = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    assert datetime_from_snowflake(snowflake_from_datetime(moment)) == moment
