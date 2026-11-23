from pusher.models import Media, MediaType, Tweet, merge_threads, strip_credits

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


CREDITS = ("#מהדורתכאןחדשות", "כאן חדשות ברשת ב'")


def test_strip_credits_removes_a_closing_credit_but_keeps_tags_in_the_text():
    text = (
        'בליכוד דרשו ממפלגת ישר להסדיר את הגיוס | @yaara_shapira עם הפרטים\n\n'
        "#מהדורתכאןחדשות עם @talberman"
    )

    assert strip_credits(text, CREDITS) == (
        "בליכוד דרשו ממפלגת ישר להסדיר את הגיוס | @yaara_shapira עם הפרטים"
    )


def test_strip_credits_removes_the_whole_credit_paragraph_and_keeps_the_link_after_it():
    text = (
        "נציגו של נפתלי בנט לא נכח במו\"מ \n\n"
        "כאן חדשות ברשת ב' 🎙️| #הבוקרהזה\n@yaara_shapira \n\n"
        "https://www.kan.org.il/content/kan-news/politic/1122204/"
    )

    assert strip_credits(text, CREDITS) == (
        "נציגו של נפתלי בנט לא נכח במו\"מ\n\n"
        "https://www.kan.org.il/content/kan-news/politic/1122204/"
    )


def test_strip_credits_cuts_a_credit_line_inside_a_paragraph():
    text = "main text\n  #מהדורתכאןחדשות עם @talberman\n@host"

    assert strip_credits(text, CREDITS) == "main text"


def test_strip_credits_ignores_case_and_leaves_text_without_credits_unchanged():
    assert strip_credits("news\n\n#Show with @host", ("#show",)) == "news"
    assert strip_credits("news #show", ("#show",)) == "news #show"
    assert strip_credits("news\n\n#show", ()) == "news\n\n#show"


def test_without_credits_keeps_the_mentions():
    tweet = _tweet("1", "news\n\n#show with @me", mentions=frozenset({"me"}))

    stripped = tweet.without_credits(("#show",))

    assert stripped.text == "news"
    assert stripped.mentions_user("me")
    assert tweet.without_credits(("#other",)) is tweet
