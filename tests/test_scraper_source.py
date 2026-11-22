from pusher.models import MediaType
from pusher.sources.scraper import ScraperTweetSource, parse_timeline


def _user(screen_name):
    # X has used both layouts; cover the newer "core" one here.
    return {"user_results": {"result": {"core": {"screen_name": screen_name}}}}


def _tweet_result(tweet_id, author, text, mentions=(), media=(), urls=(), **legacy_extra):
    legacy = {
        "id_str": tweet_id,
        "full_text": text,
        "created_at": "Sun Nov 22 10:00:00 +0000 2026",
        "entities": {
            "user_mentions": [{"screen_name": m} for m in mentions],
            "urls": list(urls),
        },
        **legacy_extra,
    }
    if media:
        legacy["extended_entities"] = {"media": list(media)}
    return {"__typename": "Tweet", "rest_id": tweet_id, "core": _user(author), "legacy": legacy}


def _entry(result):
    return {"content": {"itemContent": {"tweet_results": {"result": result}}}}


def _payload(*results):
    return {
        "data": {"user": {"result": {"timeline": {"timeline": {"instructions": [
            {"type": "TimelineAddEntries", "entries": [_entry(r) for r in results]}
        ]}}}}}
    }


PHOTO = {"type": "photo", "url": "https://t.co/pic", "media_url_https": "https://pbs.twimg.com/a.jpg"}
VIDEO = {
    "type": "video",
    "url": "https://t.co/pic",
    "media_url_https": "https://pbs.twimg.com/thumb.jpg",
    "video_info": {"variants": [
        {"content_type": "application/x-mpegURL", "url": "https://video/x.m3u8"},
        {"content_type": "video/mp4", "bitrate": 832000, "url": "https://video/mid.mp4"},
        {"content_type": "video/mp4", "bitrate": 2176000, "url": "https://video/high.mp4"},
    ]},
}


def test_parses_text_mentions_and_media():
    result = _tweet_result(
        "200", "Page", "Hey @Me &amp; see https://t.co/x https://t.co/pic",
        mentions=["Me"],
        media=[PHOTO, VIDEO],
        urls=[{"url": "https://t.co/x", "expanded_url": "https://example.com"}],
    )
    [tweet] = parse_timeline([_payload(result)], "page")

    assert tweet.id == "200"
    assert tweet.text == "Hey @Me & see https://example.com"
    assert tweet.mentions_user("me")
    assert [m.type for m in tweet.media] == [MediaType.PHOTO, MediaType.VIDEO]
    assert tweet.media[1].url == "https://video/high.mp4"


def test_skips_other_authors_and_filters_retweets_and_replies():
    own = _tweet_result("300", "page", "own tweet")
    other = _tweet_result("301", "someone", "conversation parent")
    reply = _tweet_result("302", "page", "a reply", in_reply_to_status_id_str="1")
    retweet = _tweet_result(
        "303", "page", "RT @x: hi",
        retweeted_status_result={"result": _tweet_result("10", "x", "hi @me", mentions=["me"])},
    )
    payload = _payload(own, other, reply, retweet)

    assert [t.id for t in parse_timeline([payload], "page")] == ["300", "302"]
    assert [t.id for t in parse_timeline([payload], "page", include_replies=False)] == ["300"]

    with_retweets = parse_timeline([payload], "page", include_retweets=True)
    assert [t.id for t in with_retweets] == ["300", "302", "303"]
    assert with_retweets[-1].text == "hi @me"
    assert with_retweets[-1].mentions_user("me")


def test_prefers_note_tweet_and_unwraps_visibility_results():
    result = _tweet_result("400", "page", "truncated…")
    result["note_tweet"] = {"note_tweet_results": {"result": {
        "text": "the full long text for @me",
        "entity_set": {"user_mentions": [{"screen_name": "me"}], "urls": []},
    }}}
    wrapped = {"__typename": "TweetWithVisibilityResults", "tweet": result}

    [tweet] = parse_timeline([_payload(wrapped)], "page")
    assert tweet.text == "the full long text for @me"
    assert tweet.mentions_user("me")


class FakeFetcher:
    def __init__(self, payloads):
        self.payloads = payloads

    def fetch(self, username, include_replies):
        return self.payloads


def test_scraper_source_returns_tweets_after_since_id_oldest_first():
    payload = _payload(*(_tweet_result(i, "page", "t") for i in ("503", "501", "502")))
    source = ScraperTweetSource("page", FakeFetcher([payload, payload]))

    assert source.latest_tweet_id() == "503"
    assert [t.id for t in source.new_tweets("501")] == ["502", "503"]
