from pusher.state import MAX_REMEMBERED_TWEETS, StateStore


def test_sent_messages_are_kept_next_to_the_last_tweet_ids(tmp_path):
    state = StateStore(tmp_path / "state.json")
    state.save_last_tweet_id("Page", "10")
    state.save_sent_messages("10", {"message_ids": [1]})

    assert state.load_last_tweet_id("page") == "10"
    assert state.load_sent_messages("10") == {"message_ids": [1]}
    assert state.load_sent_messages("11") is None


def test_only_the_newest_sent_tweets_are_remembered(tmp_path):
    state = StateStore(tmp_path / "state.json")
    for tweet_id in range(1, MAX_REMEMBERED_TWEETS + 2):
        state.save_sent_messages(str(tweet_id), {"message_ids": [tweet_id]})

    assert state.load_sent_messages("1") is None
    assert state.load_sent_messages("2") is not None
