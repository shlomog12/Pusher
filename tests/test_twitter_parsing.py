from pusher.models import MediaType
from pusher.twitter_client import parse_tweet


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
