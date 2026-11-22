import json

import pytest

from pusher.models import Media, MediaType, Tweet
from pusher.telegram_client import MAX_CAPTION_LENGTH, TelegramClient, format_tweet


class FakeResponse:
    def __init__(self, payload, content=b""):
        self._payload = payload
        self.status_code = 200
        self.text = json.dumps(payload)
        self._content = content

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass

    def iter_content(self, _size):
        yield self._content

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeSession:
    def __init__(self, responses=None):
        self.posts = []
        self.gets = []
        self._responses = list(responses or [])

    def post(self, url, data=None, files=None, timeout=None):
        self.posts.append({"method": url.rsplit("/", 1)[-1], "data": data, "files": files})
        if self._responses:
            return FakeResponse(self._responses.pop(0))
        return FakeResponse({"ok": True, "result": {}})

    def get(self, url, stream=False, timeout=None):
        self.gets.append(url)
        return FakeResponse({}, content=b"bytes")


def _tweet(text="hello @me", media=()):
    return Tweet(id="1", author_username="page", text=text, mentions=frozenset({"me"}), media=media)


PHOTO = Media(MediaType.PHOTO, "https://pbs.twimg.com/a.jpg")
VIDEO = Media(MediaType.VIDEO, "https://video/high.mp4")


@pytest.fixture
def session():
    return FakeSession()


def test_text_only_tweet_sends_message(session):
    TelegramClient("token", "42", session=session).send_tweet(_tweet())

    assert [p["method"] for p in session.posts] == ["sendMessage"]
    assert "hello @me" in session.posts[0]["data"]["text"]


def test_single_video_sent_with_caption(session):
    TelegramClient("token", "42", session=session).send_tweet(_tweet(media=(VIDEO,)))

    post = session.posts[0]
    assert post["method"] == "sendVideo"
    assert post["data"]["video"] == VIDEO.url
    assert "hello @me" in post["data"]["caption"]


def test_multiple_media_sent_as_group(session):
    TelegramClient("token", "42", session=session).send_tweet(_tweet(media=(PHOTO, VIDEO)))

    post = session.posts[0]
    assert post["method"] == "sendMediaGroup"
    entries = json.loads(post["data"]["media"])
    assert [e["type"] for e in entries] == ["photo", "video"]
    assert "caption" in entries[0] and "caption" not in entries[1]


def test_long_text_sent_separately_from_media(session):
    tweet = _tweet(text="x" * (MAX_CAPTION_LENGTH + 10), media=(PHOTO,))
    TelegramClient("token", "42", session=session).send_tweet(tweet)

    assert [p["method"] for p in session.posts] == ["sendMessage", "sendPhoto"]
    assert "caption" not in session.posts[1]["data"]


def test_falls_back_to_upload_when_url_is_rejected():
    session = FakeSession(responses=[{"ok": False, "description": "failed to get HTTP URL content"}])
    TelegramClient("token", "42", session=session).send_tweet(_tweet(media=(VIDEO,)))

    assert [p["method"] for p in session.posts] == ["sendVideo", "sendVideo"]
    assert session.posts[1]["files"]["video"] == ("high.mp4", b"bytes")


def test_falls_back_to_links_when_upload_fails():
    failure = {"ok": False, "description": "Request Entity Too Large"}
    session = FakeSession(responses=[failure, failure])
    TelegramClient("token", "42", session=session).send_tweet(_tweet(media=(VIDEO,)))

    assert [p["method"] for p in session.posts] == ["sendVideo", "sendVideo", "sendMessage"]
    assert VIDEO.url in session.posts[2]["data"]["text"]


def test_format_tweet_escapes_html_and_respects_limit():
    tweet = _tweet(text="<b>&</b> " * 2000)
    formatted = format_tweet(tweet, max_length=500)

    assert len(formatted) <= 500
    assert "<b>&</b>" not in formatted
    assert formatted.endswith("…")



def test_message_is_only_the_tweet_text():
    assert format_tweet(_tweet()) == "hello @me"


def test_media_only_tweet_is_sent_without_caption(session):
    TelegramClient("token", "42", session=session).send_tweet(_tweet(text="", media=(PHOTO,)))

    assert [p["method"] for p in session.posts] == ["sendPhoto"]
    assert "caption" not in session.posts[0]["data"]
