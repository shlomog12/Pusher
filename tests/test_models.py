from pusher.models import Media, MediaType, Tweet, merge_threads

PHOTO = Media(MediaType.PHOTO, "https://pbs.twimg.com/a.jpg")


def _tweet(tweet_id, text, parent=None, parent_author="page", **extra):
    return Tweet(
        id=tweet_id,
        author_username="page",
        text=text,
        in_reply_to_id=parent,
        in_reply_to_username=parent_author if parent else None,
        **extra,
    )


def test_merge_threads_joins_self_replies_into_the_first_tweet():
    tweets = [
        _tweet("1", "1/ start", mentions=frozenset({"me"})),
        _tweet("2", "standalone"),
        _tweet("3", "2/ next", parent="1", media=(PHOTO,)),
        _tweet("4", "reply to someone", parent="99", parent_author="someone"),
        _tweet("5", "3/ end", parent="3"),
    ]

    thread, standalone, reply = merge_threads(tweets)

    assert thread.id == "1"
    assert thread.text == "1/ start\n\n2/ next\n\n3/ end"
    assert thread.media == (PHOTO,)
    assert thread.mentions_user("me")
    assert thread.thread_ids == ("3", "5")
    assert thread.last_id == "5"
    assert [standalone.id, reply.id] == ["2", "4"]
    assert standalone.last_id == "2"


def test_self_reply_without_its_parent_stays_on_its_own():
    [tweet] = merge_threads([_tweet("7", "4/ added later", parent="1")])

    assert tweet.id == "7"
    assert tweet.is_self_reply
