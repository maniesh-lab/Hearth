import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import storage
from cogs import moderation
from cogs.moderation import Moderation, moderation_problem
from conftest import make_guild, make_interaction, make_member, reply_text


@pytest.fixture
def cog(db):
    return Moderation(MagicMock(), db)


@pytest.fixture
def guild():
    return make_guild(owner_id=999, bot_rank=10)


def test_rank_checks(guild):
    mod = make_member(2, rank=5)
    interaction = make_interaction(mod, guild)
    assert moderation_problem(interaction, mod) == "You can't use that on yourself."
    assert "owner" in moderation_problem(interaction, make_member(999, rank=1))
    assert "higher than yours" in moderation_problem(interaction, make_member(3, rank=7))
    assert moderation_problem(interaction, make_member(3, rank=3)) is None


def test_owner_can_act_on_anyone_below_the_bot(guild):
    owner = make_member(999, rank=0)
    interaction = make_interaction(owner, guild)
    assert moderation_problem(interaction, make_member(3, rank=8)) is None
    assert "higher than mine" in moderation_problem(interaction, make_member(3, rank=12))


def test_kick_refused_for_higher_rank(cog, guild):
    boss = make_member(4, rank=8)
    interaction = make_interaction(make_member(2, rank=5), guild)
    asyncio.run(cog.kick.callback(cog, interaction, boss, "x"))
    boss.kick.assert_not_awaited()


def test_warnings_are_saved_and_listed(cog, guild):
    mod, bob = make_member(2, rank=5), make_member(3, rank=1)
    for reason in ("spam", "rude"):
        asyncio.run(cog.warn.callback(cog, make_interaction(mod, guild), bob, reason))

    assert cog.warning_count(guild.id, bob.id) == 2

    # A new cog on the same database = the bot after a restart
    restarted = Moderation(MagicMock(), cog.db)
    interaction = make_interaction(mod, guild)
    asyncio.run(restarted.warnings.callback(restarted, interaction, bob))
    embed = interaction.response.send_message.await_args.kwargs["embed"]
    assert "2 warnings" in embed.title
    assert "rude" in embed.description and "spam" in embed.description


def test_warnings_are_per_server(cog):
    mod, bob = make_member(2, rank=5), make_member(3, rank=1)
    asyncio.run(cog.warn.callback(cog, make_interaction(mod, make_guild(guild_id=1)), bob, "a"))
    asyncio.run(cog.warn.callback(cog, make_interaction(mod, make_guild(guild_id=2)), bob, "b"))
    assert cog.warning_count(1, bob.id) == 1
    assert cog.warning_count(2, bob.id) == 1


def test_clearwarnings(cog, guild):
    mod, bob = make_member(2, rank=5), make_member(3, rank=1)
    asyncio.run(cog.warn.callback(cog, make_interaction(mod, guild), bob, "spam"))
    interaction = make_interaction(mod, guild)
    asyncio.run(cog.clearwarnings.callback(cog, interaction, bob))
    assert "Cleared 1" in reply_text(interaction)
    assert cog.warning_count(guild.id, bob.id) == 0


def test_mod_log_records_actions_once_set(cog, guild):
    log_channel = MagicMock()
    log_channel.send = AsyncMock()
    guild.get_channel = lambda cid: log_channel if cid == 777 else None
    mod, bob = make_member(2, rank=5), make_member(3, rank=1)

    asyncio.run(cog.kick.callback(cog, make_interaction(mod, guild), bob, "before log set"))
    log_channel.send.assert_not_awaited()

    storage.set_setting(cog.db, guild.id, moderation.MODLOG_KEY, 777)
    asyncio.run(cog.ban.callback(cog, make_interaction(mod, guild), bob, "raiding"))
    embed = log_channel.send.await_args.kwargs["embed"]
    assert embed.title == "🔨 Ban"
    assert any(field.value == "raiding" for field in embed.fields)


def test_spam_filter_counts_per_server(cog):
    moderation.message_log.clear()
    author = make_member(7)

    def message(guild_id):
        m = MagicMock()
        m.author = author
        m.guild = make_guild(guild_id=guild_id)
        m.guild.get_channel = lambda cid: None
        m.channel.send = AsyncMock()
        return m

    for guild_id in (1, 1, 1, 2, 2):
        asyncio.run(cog.on_message(message(guild_id)))
    author.timeout.assert_not_awaited()

    for _ in range(2):
        asyncio.run(cog.on_message(message(1)))
    author.timeout.assert_awaited_once()
