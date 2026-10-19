import pytest

from cogs.roles import normalize_emoji


@pytest.mark.parametrize(
    "typed, saved",
    [
        ("🎨", "🎨"),
        (" 🎮 ", "🎮"),
        ("👍🏽", "👍🏽"),
        ("<:party:123456789012345678>", "<:party:123456789012345678>"),
        ("<a:dance:123456789012345678>", "<a:dance:123456789012345678>"),
    ],
)
def test_emoji_are_accepted(typed, saved):
    assert normalize_emoji(typed) == saved


@pytest.mark.parametrize("typed", ["hello", "a", "", "role"])
def test_words_are_not_emoji(typed):
    assert normalize_emoji(typed) is None
