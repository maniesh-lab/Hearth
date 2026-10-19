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
