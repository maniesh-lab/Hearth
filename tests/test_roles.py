import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs import roles
from cogs.roles import Roles, normalize_emoji, parse_role_pairs
from conftest import Rank, make_guild, make_interaction, make_member, reply_text


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


# --- /roleadd with several roles, and the picker that updates itself ----------

def role(role_id, name, rank=2, admin=False):
    r = MagicMock()
    r.id = role_id
    r.name = name
    r.position = rank
    r.managed = False
    r.permissions.administrator = admin
    r.is_default = lambda: False
    r.__ge__ = lambda self, other: Rank(self.position) >= other
    return r


def picker_message(message_id=500):
    message = MagicMock()
    message.id = message_id
    message.reactions = []
    message.edit = AsyncMock()
    message.delete = AsyncMock()

    async def add_reaction(emoji):
        message.reactions.append(SimpleNamespace(emoji=emoji, me=True))

    async def remove_reaction(emoji, member):
        message.reactions = [r for r in message.reactions if r.emoji != emoji]

    message.add_reaction = AsyncMock(side_effect=add_reaction)
    message.remove_reaction = AsyncMock(side_effect=remove_reaction)
    return message


@pytest.fixture
def server():
    gamer, artist, coder = role(11, "Gamer"), role(12, "Artist"), role(13, "Coder")
    boss = role(14, "Boss", admin=True)
    guild = make_guild()
    guild.roles = [gamer, artist, coder, boss]
    by_id = {r.id: r for r in guild.roles}
    guild.get_role = lambda rid: by_id.get(rid)

    channel = MagicMock()
    channel.id = 77
    posted = []

    async def send(embed=None):
        message = picker_message(500 + len(posted))
        posted.append(message)
        return message

    channel.send = AsyncMock(side_effect=send)

    async def fetch_message(message_id):
        for message in posted:
            if message.id == message_id:
                return message
        raise roles.discord.NotFound(MagicMock(status=404, reason="gone"), "gone")

    channel.fetch_message = AsyncMock(side_effect=fetch_message)
    guild.get_channel = lambda cid: channel if cid == 77 else None
    return SimpleNamespace(guild=guild, channel=channel, posted=posted, gamer=gamer, artist=artist, coder=coder)


@pytest.fixture
def cog(tmp_path, monkeypatch):
    monkeypatch.setattr(roles, "DATA_DIR", tmp_path)
    monkeypatch.setattr(roles, "CONFIG_PATH", tmp_path / "roles.json")
    monkeypatch.setattr(roles, "LEGACY_IDS_PATH", tmp_path / "old.json")
    return Roles(MagicMock())


def admin_does(server, command, *args):
    interaction = make_interaction(make_member(1, rank=9), server.guild)
    interaction.channel = server.channel
    asyncio.run(command.callback(command.binding, interaction, *args))
    return reply_text(interaction)


def test_parse_mentions_names_and_glued_pairs(server):
    pairs, problems = parse_role_pairs("🎮 <@&11> 🎨@Artist <:party:123456789012345678> coder", server.guild)
    assert [(e, r.name) for e, r in pairs] == [
        ("🎮", "Gamer"), ("🎨", "Artist"), ("<:party:123456789012345678>", "Coder"),
    ]
    assert problems == []


@pytest.mark.parametrize(
    "text, problem",
    [
        ("🎮 @Gamer 🎨", "nothing to pair with"),
        ("hello @Gamer", "isn't an emoji"),
        ("🎮 @Nobody", "couldn't find the role"),
        ("", "Write an emoji and a role"),
    ],
)
def test_parse_problems(server, text, problem):
    _, problems = parse_role_pairs(text, server.guild)
    assert problem in problems[0]


def test_roleadd_several_at_once(cog, server):
    reply = admin_does(server, cog.roleadd, "🎮 <@&11> 🎨 <@&12> 💻 <@&13>")
    assert "Added: 🎮 → **Gamer**, 🎨 → **Artist**, 💻 → **Coder**" in reply
    assert "Run `/rolesetup`" in reply  # no picker posted yet
    assert cog._get_guild(server.guild)["roles"] == {"🎮": 11, "🎨": 12, "💻": 13}


def test_new_emoji_for_a_listed_role_moves_it(cog, server):
    admin_does(server, cog.roleadd, "🎮 <@&11>")
    admin_does(server, cog.roleadd, "🕹️ <@&11>")
    assert cog._get_guild(server.guild)["roles"] == {"🕹️": 11}


def test_unsafe_roles_are_skipped_but_the_rest_are_added(cog, server):
    reply = admin_does(server, cog.roleadd, "👑 @Boss 🎮 @Gamer")
    assert "Added: 🎮 → **Gamer**" in reply
    assert "Administrator" in reply
    assert cog._get_guild(server.guild)["roles"] == {"🎮": 11}


def test_picker_updates_itself_after_rolesetup(cog, server):
    admin_does(server, cog.roleadd, "🎮 @Gamer")
    assert "From now on" in admin_does(server, cog.rolesetup)
    picker = server.posted[0]
    assert [r.emoji for r in picker.reactions] == ["🎮"]

    reply = admin_does(server, cog.roleadd, "🎨 @Artist")
    assert "The picker has been updated." in reply
    assert [r.emoji for r in picker.reactions] == ["🎮", "🎨"]
    assert "🎨 — Artist" in picker.edit.await_args.kwargs["embed"].description

    reply = admin_does(server, cog.roleremove, "🎮")
    assert "The picker has been updated." in reply
    assert [r.emoji for r in picker.reactions] == ["🎨"]
    assert "Gamer" not in picker.edit.await_args.kwargs["embed"].description
    assert len(server.posted) == 1  # same message the whole time


def test_rolesetup_again_replaces_the_old_picker(cog, server):
    admin_does(server, cog.roleadd, "🎮 @Gamer")
    admin_does(server, cog.rolesetup)
    admin_does(server, cog.rolesetup)
    server.posted[0].delete.assert_awaited_once()
    assert cog._get_guild(server.guild)["message_id"] == server.posted[1].id


def test_deleted_picker_asks_for_rolesetup(cog, server):
    admin_does(server, cog.roleadd, "🎮 @Gamer")
    admin_does(server, cog.rolesetup)
    server.posted.clear()  # someone deleted the picker message
    assert "Run `/rolesetup`" in admin_does(server, cog.roleadd, "🎨 @Artist")
