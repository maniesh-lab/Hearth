import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.respect import Respect, RespectStore, level_for, level_label, next_level, says_thanks
from conftest import make_guild, make_interaction, make_member, reply_text


@pytest.mark.parametrize("text", ["thanks!", "Thank you so much", "thank-you!", "thankyou", "ty", "thx bro", "tysm", "THANK U"])
def test_thank_you_words_count(text):
    assert says_thanks(text)


@pytest.mark.parametrize("text", ["type this", "thanksgiving plans", "no thanks", "no thank-you", "nice work"])
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


def test_every_level_has_an_emoji_in_its_label():
    assert level_label(1, "Newbie") == "🌱 Newbie"
    assert level_label(6, "Legend") == "👑 Legend"
    assert level_label(0, "No level yet") == "No level yet"


@pytest.fixture
def cog(tmp_path, db):
    return Respect(MagicMock(), RespectStore(tmp_path / "respect.db"), db)


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
    assert "Level 1 — 🌱 Newbie" in message.channel.send.await_args.args[0]


@pytest.fixture
def clock(monkeypatch):
    """A fake clock for the respect cog: clock.now is the time in seconds."""
    from cogs import respect

    class Clock:
        now = 1_000_000.0

    monkeypatch.setattr(respect.time, "time", lambda: Clock.now)
    return Clock


def thank(cog, giver, receiver):
    message = thanks_message(giver, "thanks", [receiver])
    asyncio.run(cog.on_message(message))
    return message


def test_thanking_the_same_person_twice_in_a_row_counts_twice(cog, clock):
    alice, bob = make_member(1), make_member(2, name="Bob")
    thank(cog, alice, bob)
    clock.now += 60
    second = thank(cog, alice, bob)
    assert cog.store.points(1, 2) == 2
    assert second.channel.send.await_args.args[0] == "**+1** respect for **Bob** · total: **2**"


def test_one_message_thanking_two_people_gets_one_reply_with_both(cog, clock):
    alice, bob, carol = make_member(1), make_member(2, name="Bob"), make_member(3, name="Carol")
    give_points(cog, 2, 5)
    message = thanks_message(alice, "thanks both", [bob, carol])
    asyncio.run(cog.on_message(message))

    message.channel.send.assert_awaited_once()
    lines = message.channel.send.await_args.args[0].split("\n")
    assert lines[0] == "**+1** respect for **Bob** · total: **6**"
    assert lines[1].startswith("🎉 <@3> reached **Level 1 — 🌱 Newbie**")


def test_daily_limit_is_silent(cog, clock):
    alice, bob = make_member(1), make_member(2)
    for _ in range(10):
        thank(cog, alice, bob)
        clock.now += 3 * 60
    eleventh = thank(cog, alice, bob)
    eleventh.channel.send.assert_not_awaited()


def test_five_thanks_in_ten_minutes_pauses_the_sender_for_a_day(cog, clock):
    alice, bob, carol = make_member(1, name="Alice"), make_member(2), make_member(3)
    for _ in range(4):
        thank(cog, alice, bob)
        clock.now += 60
    fifth = thank(cog, alice, bob)

    assert cog.store.points(1, 2) == 4  # the 5th doesn't count
    assert "**Alice** is sending thanks too fast" in fifth.channel.send.await_args.args[0]

    clock.now += 60 * 60
    thank(cog, alice, carol)  # paused for everyone, not just Bob
    assert cog.store.points(1, 3) == 0

    clock.now += 24 * 60 * 60
    thank(cog, alice, carol)
    assert cog.store.points(1, 3) == 1


def test_thanks_spread_out_are_not_spam(cog, clock):
    alice, bob = make_member(1), make_member(2)
    for _ in range(6):  # every 3 minutes: never 5 inside 10 minutes
        thank(cog, alice, bob)
        clock.now += 3 * 60
    assert cog.store.points(1, 2) == 6


def test_one_person_gives_the_same_person_at_most_10_a_day(cog, clock):
    alice, bob, carol = make_member(1), make_member(2), make_member(3)
    for _ in range(12):
        thank(cog, alice, bob)
        clock.now += 3 * 60
    assert cog.store.points(1, 2) == 10

    thank(cog, alice, carol)  # other people still get Alice's thanks
    assert cog.store.points(1, 3) == 1

    clock.now += 24 * 60 * 60
    thank(cog, alice, bob)
    assert cog.store.points(1, 2) == 11


def test_thanks_aimed_at_nobody_dont_count_toward_spam(cog, clock):
    alice, bob = make_member(1), make_member(2)
    for _ in range(10):
        asyncio.run(cog.on_message(thanks_message(alice, "thanks everyone")))
    thank(cog, alice, bob)
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
    text = interaction.response.send_message.await_args.args[0]
    assert "🤝 Level 2 (Helper) · 25 respect · #1" in text
    assert "25 more to reach 🧩 Contributor." in text

    asyncio.run(cog.leaderboard.callback(cog, interaction))
    assert "🤝 user2 — 25 (Helper)" in interaction.response.send_message.await_args.kwargs["embed"].description


# --- level roles ---------------------------------------------------------

def role_guild(features=(), members=()):
    """A server where create_role hands back numbered fake roles."""
    guild = make_guild()
    guild.features = list(features)
    guild.me.guild_permissions.manage_roles = True
    made = {}

    async def create_role(**options):
        role = MagicMock(name=options["name"])
        role.id = 100 + len(made)
        role.options = options
        role.delete = AsyncMock()
        made[role.id] = role
        return role

    guild.create_role = AsyncMock(side_effect=create_role)
    guild.get_role = lambda role_id: made.get(role_id)
    by_id = {m.id: m for m in members}
    guild.get_member = lambda user_id: by_id.get(user_id)
    return guild, made


def role_member(user_id):
    member = make_member(user_id)
    member.roles = []
    member.guild_permissions.manage_roles = True

    async def add(*roles, reason=None):
        member.roles.extend(roles)

    async def remove(*roles, reason=None):
        for role in roles:
            member.roles.remove(role)

    member.add_roles = AsyncMock(side_effect=add)
    member.remove_roles = AsyncMock(side_effect=remove)
    return member


def give_points(cog, user_id, points):
    for giver in range(points):
        cog.store.give(1, 1000 + giver, user_id, now=0)


def test_turning_roles_on_creates_six_hoisted_roles_and_hands_them_out(cog):
    bob = role_member(2)
    give_points(cog, 2, 25)
    guild, made = role_guild(members=[bob])
    interaction = make_interaction(role_member(1), guild)

    asyncio.run(cog.level_roles_on.callback(cog, interaction))

    names = [call.kwargs["name"] for call in guild.create_role.await_args_list]
    assert names[0] == "👑 Legend (1000+ respect)"  # top level created first...
    assert names[-1] == "🌱 Newbie (1+ respect)"  # ...so Newbie ends up lowest
    first = guild.create_role.await_args_list[0].kwargs
    assert first["hoist"] is True and "display_icon" not in first and "color" not in first
    assert [r.options["name"] for r in bob.roles] == ["🤝 Helper (25+ respect)"]
    assert "Role icons need Boost Level 2" in interaction.followup.send.await_args.args[0]


def test_boosted_servers_get_role_icons_instead_of_emoji_names(cog):
    guild, made = role_guild(features=["ROLE_ICONS"])
    asyncio.run(cog.level_roles_on.callback(cog, make_interaction(role_member(1), guild)))
    legend = guild.create_role.await_args_list[0].kwargs
    assert legend["name"] == "Legend (1000+ respect)"
    assert legend["display_icon"] == "👑"


def test_level_up_swaps_the_role(cog):
    bob = role_member(2)
    guild, made = role_guild(members=[bob])
    asyncio.run(cog.level_roles_on.callback(cog, make_interaction(role_member(1), guild)))

    give_points(cog, 2, 1)  # Newbie
    asyncio.run(cog._set_level_role(bob, 1, cog._level_roles(guild)))
    give_points(cog, 2, 23)  # 24 points: one thank-you away from Helper

    message = thanks_message(make_member(50), "thank you", [bob])
    message.guild = guild
    asyncio.run(cog.on_message(message))

    assert [r.options["name"] for r in bob.roles] == ["🤝 Helper (25+ respect)"]


def test_no_roles_when_level_roles_are_off(cog):
    bob = role_member(2)
    guild, made = role_guild(members=[bob])
    message = thanks_message(make_member(50), "thanks", [bob])
    message.guild = guild
    asyncio.run(cog.on_message(message))
    assert cog.store.points(1, 2) == 1
    bob.add_roles.assert_not_awaited()


def test_turning_roles_off_deletes_them_and_keeps_points(cog):
    give_points(cog, 2, 3)
    guild, made = role_guild(members=[role_member(2)])
    admin = role_member(1)
    asyncio.run(cog.level_roles_on.callback(cog, make_interaction(admin, guild)))
    asyncio.run(cog.level_roles_off.callback(cog, make_interaction(admin, guild)))

    assert all(role.delete.await_count == 1 for role in made.values())
    assert not cog.roles_on(1)
    assert cog.store.points(1, 2) == 3


def test_level_roles_need_manage_roles(cog):
    guild, made = role_guild()
    user = role_member(1)
    user.guild_permissions.manage_roles = False
    interaction = make_interaction(user, guild)
    asyncio.run(cog.level_roles_on.callback(cog, interaction))
    assert "Manage Roles" in reply_text(interaction)
    guild.create_role.assert_not_awaited()
