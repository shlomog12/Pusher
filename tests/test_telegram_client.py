import json

import pytest

from pusher.models import Media, MediaType, Tweet
from pusher.telegram_client import (
    SentTweet,
    MAX_CAPTION_LENGTH,
    MAX_MESSAGE_LENGTH,
    TelegramClient,
    split_text,
)


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
        return FakeResponse({"ok": True, "result": {"message_id": len(self.posts)}})

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


def test_text_message_shows_link_preview(session):
    TelegramClient("token", "42", session=session).send_tweet(_tweet("read https://example.com/a"))

    data = session.posts[0]["data"]
    assert "disable_web_page_preview" not in data
    assert json.loads(data["link_preview_options"]) == {"prefer_large_media": True}


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


def test_split_text_escapes_html_and_respects_limit():
    parts = split_text("<b>&</b> " * 2000, limit=500)

    assert len(parts) > 1
    assert all(len(part) <= 500 for part in parts)
    assert all("<b>" not in part for part in parts)
    assert "".join(parts).count("&amp;") == 2000


def test_split_text_prefers_paragraph_breaks():
    first, second = "a" * 300, "b" * 300
    assert split_text(f"{first}\n\n{second}", limit=400) == [first, second]


def test_message_is_only_the_tweet_text():
    assert split_text(_tweet().text) == ["hello @me"]


def test_long_thread_text_is_split_into_several_messages(session):
    tweet = _tweet(text="\n\n".join(["word " * 100] * 20))
    TelegramClient("token", "42", session=session).send_tweet(tweet)

    texts = [p["data"]["text"] for p in session.posts]
    assert len(texts) > 1
    assert all(len(text) <= MAX_MESSAGE_LENGTH for text in texts)
    assert sum(text.count("word") for text in texts) == 2000


def test_more_than_ten_media_are_sent_as_several_albums(session):
    tweet = _tweet(media=(PHOTO,) * 11)
    TelegramClient("token", "42", session=session).send_tweet(tweet)

    assert [p["method"] for p in session.posts] == ["sendMediaGroup", "sendPhoto"]
    assert len(json.loads(session.posts[0]["data"]["media"])) == 10
    assert "caption" not in session.posts[1]["data"]


def test_media_only_tweet_is_sent_without_caption(session):
    TelegramClient("token", "42", session=session).send_tweet(_tweet(text="", media=(PHOTO,)))

    assert [p["method"] for p in session.posts] == ["sendPhoto"]
    assert "caption" not in session.posts[0]["data"]


def test_send_tweet_returns_the_sent_messages(session):
    client = TelegramClient("token", "42", session=session)

    assert client.send_tweet(_tweet()) == SentTweet((1,), 1)
    assert client.send_tweet(_tweet(media=(PHOTO,))) == SentTweet((2,), 2, (PHOTO.url,))


def test_media_group_message_ids_are_returned():
    group = {"ok": True, "result": [{"message_id": 7}, {"message_id": 8}]}
    session = FakeSession([group])

    sent = TelegramClient("token", "42", session=session).send_tweet(_tweet(media=(PHOTO, VIDEO)))

    assert sent == SentTweet((7, 8), 7, (PHOTO.url, VIDEO.url))


def test_long_text_with_media_has_no_single_text_message(session):
    sent = TelegramClient("token", "42", session=session).send_tweet(
        _tweet("x" * (MAX_CAPTION_LENGTH + 1), media=(PHOTO,))
    )

    assert sent == SentTweet((1, 2), None, (PHOTO.url,))


def test_update_edits_the_text_in_place(session):
    client = TelegramClient("token", "42", session=session)

    sent = client.update_tweet(SentTweet((5,), 5), _tweet("fixed"))

    post = session.posts[0]
    assert post["method"] == "editMessageText"
    assert post["data"]["message_id"] == 5
    assert post["data"]["text"] == "fixed"
    assert sent == SentTweet((5,), 5)


def test_update_edits_the_caption_when_the_media_is_the_same(session):
    client = TelegramClient("token", "42", session=session)

    client.update_tweet(SentTweet((5, 6), 5, (PHOTO.url, VIDEO.url)), _tweet("fixed", (PHOTO, VIDEO)))

    assert [p["method"] for p in session.posts] == ["editMessageCaption"]
    assert session.posts[0]["data"]["caption"] == "fixed"


def test_unchanged_text_counts_as_updated():
    session = FakeSession([{"ok": False, "description": "Bad Request: message is not modified"}])

    sent = TelegramClient("token", "42", session=session).update_tweet(SentTweet((5,), 5), _tweet())

    assert sent == SentTweet((5,), 5)
    assert len(session.posts) == 1


def test_update_with_other_media_replaces_the_messages(session):
    client = TelegramClient("token", "42", session=session)

    sent = client.update_tweet(SentTweet((5,), 5), _tweet(media=(PHOTO,)))

    assert [p["method"] for p in session.posts] == ["sendPhoto", "deleteMessage"]
    assert session.posts[1]["data"]["message_id"] == 5
    assert sent == SentTweet((1,), 1, (PHOTO.url,))


def test_failed_edit_sends_the_tweet_again():
    session = FakeSession([{"ok": False, "description": "Bad Request: message to edit not found"}])

    TelegramClient("token", "42", session=session).update_tweet(SentTweet((5,), 5), _tweet())

    assert [p["method"] for p in session.posts] == [
        "editMessageText",
        "sendMessage",
        "deleteMessage",
    ]
