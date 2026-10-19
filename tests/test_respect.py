import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.respect import Respect, RespectStore, level_for, next_level, says_thanks
from conftest import make_guild, make_interaction, make_member


@pytest.mark.parametrize("text", ["thanks!", "Thank you so much", "ty", "thx bro", "tysm", "THANK U"])
def test_thank_you_words_count(text):
    assert says_thanks(text)


@pytest.mark.parametrize("text", ["type this", "thanksgiving plans", "no thanks", "nice work"])
def test_other_words_dont_count(text):
    assert not says_thanks(text)


@pytest.mark.parametrize(
    "points, level",
    [(0, 0), (1, 1), (24, 1), (25, 2), (50, 3), (100, 4), (499, 4), (500, 5), (1000, 6), (5000, 6)],
)
def test_levels(points, level):
    assert level_for(points)[0] == level


def test_next_level():
    assert next_level(24) == (25, "Helper")
    assert next_level(1000) is None


@pytest.fixture
def cog(tmp_path):
    return Respect(MagicMock(), RespectStore(tmp_path / "respect.db"))


def thanks_message(author, text, mentions=(), guild_id=1):
    message = MagicMock()
    message.author = author
    message.content = text
    message.mentions = list(mentions)
    message.guild.id = guild_id
    message.reference = None
    message.channel.send = AsyncMock()
    return message


def test_thanks_gives_a_point_and_announces_level_1(cog):
    alice, bob = make_member(1), make_member(2)
    message = thanks_message(alice, "thanks @bob", [bob])
    asyncio.run(cog.on_message(message))
    assert cog.store.points(1, 2) == 1
    assert "Level 1" in message.channel.send.await_args.args[0]


def test_same_person_only_once_an_hour(cog):
    alice, bob = make_member(1), make_member(2)
    asyncio.run(cog.on_message(thanks_message(alice, "thanks", [bob])))
    asyncio.run(cog.on_message(thanks_message(alice, "thanks again", [bob])))
    assert cog.store.points(1, 2) == 1


def test_no_points_for_yourself_or_bots(cog):
    alice, robot = make_member(1), make_member(9, bot=True)
    asyncio.run(cog.on_message(thanks_message(alice, "thanks", [alice, robot])))
    assert cog.store.points(1, 1) == 0
    assert cog.store.points(1, 9) == 0


def test_points_are_per_server(cog):
    alice, bob = make_member(1), make_member(2)
    asyncio.run(cog.on_message(thanks_message(alice, "thanks", [bob], guild_id=1)))
    asyncio.run(cog.on_message(thanks_message(alice, "thanks", [bob], guild_id=2)))
    assert cog.store.points(1, 2) == 1
    assert cog.store.points(2, 2) == 1


def test_leaderboard_and_respect_commands(cog):
    bob = make_member(2)
    for giver in range(100, 125):  # 25 different people -> Level 2
        asyncio.run(cog.on_message(thanks_message(make_member(giver), "thank you", [bob])))

    guild = make_guild()
    guild.get_member = lambda uid: bob if uid == 2 else None
    interaction = make_interaction(make_member(1), guild)

    asyncio.run(cog.respect.callback(cog, interaction, bob))
    assert "Level 2 (Helper) · 25 respect · #1" in interaction.response.send_message.await_args.args[0]

    asyncio.run(cog.leaderboard.callback(cog, interaction))
    assert "user2 — 25 (Helper)" in interaction.response.send_message.await_args.kwargs["embed"].description
