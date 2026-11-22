from pusher.sources.base import is_credit_line, strip_credit_lines

NEWS = (
    'איזנקוט מתכנן להכריז | פרסום של @glick_sh\n'
    '\n'
    '#מהדורתכאןחדשות עם @talberman'
)


def test_trailing_credit_line_is_removed():
    assert strip_credit_lines(NEWS) == "איזנקוט מתכנן להכריז | פרסום של @glick_sh"


def test_credit_lines_with_several_tags_and_connectors():
    assert is_credit_line("#חדשות עם @a ו@b")
    assert is_credit_line("#news | @anchor, @editor")
    assert strip_credit_lines("text\n#a\n#b with @c") == "text"


def test_lines_with_other_words_are_kept():
    for text in (
        "text\n#breaking: the minister resigned",
        "text\nצילום: @photographer",
        "text\nתודה ל@someone #חדשות",
        "the whole tweet #inline",
    ):
        assert strip_credit_lines(text) == text


def test_mentions_only_line_is_kept():
    assert strip_credit_lines("text\n@someone") == "text\n@someone"


def test_only_the_end_is_stripped():
    text = "#intro with @x\nmain text"
    assert strip_credit_lines(text) == text


def test_tweet_made_only_of_tags_is_kept():
    assert strip_credit_lines("#a\n#b") == "#a\n#b"
