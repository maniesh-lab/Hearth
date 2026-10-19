import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import storage
from cogs.onboarding import Onboarding, auto_role_problem, render_welcome
from conftest import Rank, make_guild, make_interaction, make_member, reply_text


def role(role_id, rank, admin=False, managed=False, default=False):
    r = MagicMock()
    r.id = role_id
    r.name = f"role{role_id}"
    r.position = rank
    r.managed = managed
    r.permissions.administrator = admin
    r.is_default = lambda: default
    r.__ge__ = lambda self, other: Rank(self.position) >= other
    return r


def test_render_fills_placeholders_and_ignores_other_braces():
    member = make_member(1)
    member.guild = make_guild()
    text = render_welcome("Hi {user}, welcome to {server}! You're #{count}. {unknown}", member)
    assert text == "Hi <@1>, welcome to College! You're #42. {unknown}"


def test_auto_role_rules():
    guild = make_guild(owner_id=999, bot_rank=10)
    admin = make_member(2, rank=8)
    assert auto_role_problem(guild, role(1, 3), admin) is None
    assert "Administrator" in auto_role_problem(guild, role(1, 3, admin=True), admin)
    assert "above my highest role" in auto_role_problem(guild, role(1, 11), admin)
    assert "above your highest role" in auto_role_problem(guild, role(1, 9), admin)
    assert "@everyone" in auto_role_problem(guild, role(1, 0, default=True), admin)


@pytest.fixture
def setup(db):
    cog = Onboarding(MagicMock(), db)
    guild = make_guild()
    welcome_channel = MagicMock()
    welcome_channel.send = AsyncMock()
    student = role(30, 2)
    guild.get_channel = lambda cid: welcome_channel if cid == 20 else None
    guild.get_role = lambda rid: student if rid == 30 else None
    guild.system_channel = MagicMock()
    guild.system_channel.send = AsyncMock()
    return cog, guild, welcome_channel, student


def new_member(guild):
    member = make_member(5)
    member.guild = guild
    return member


def test_defaults_use_system_channel_and_no_role(setup):
    cog, guild, welcome_channel, _ = setup
    member = new_member(guild)
    asyncio.run(cog.on_member_join(member))
    guild.system_channel.send.assert_awaited_once()
    member.add_roles.assert_not_awaited()


def test_custom_channel_message_and_role(setup):
    cog, guild, welcome_channel, student = setup
    storage.set_setting(cog.db, guild.id, "welcome_channel", 20)
    storage.set_setting(cog.db, guild.id, "welcome_message", "Namaste {user}!")
    storage.set_setting(cog.db, guild.id, "welcome_role", 30)

    member = new_member(guild)
    asyncio.run(cog.on_member_join(member))

    member.add_roles.assert_awaited_once()
    assert member.add_roles.await_args.args[0] is student
    embed = welcome_channel.send.await_args.kwargs["embed"]
    assert embed.description == "Namaste <@5>!"
    guild.system_channel.send.assert_not_awaited()


def test_join_still_welcomed_when_dms_are_closed(setup):
    cog, guild, _, _ = setup
    member = new_member(guild)
    response = MagicMock(status=403, reason="Forbidden")
    member.send = AsyncMock(side_effect=discord.Forbidden(response, "closed"))
    asyncio.run(cog.on_member_join(member))
    guild.system_channel.send.assert_awaited_once()


def test_settings_need_manage_server(setup):
    cog, guild, _, _ = setup
    user = make_member(2)
    user.guild_permissions.manage_guild = False
    interaction = make_interaction(user, guild)
    asyncio.run(cog.set_message.callback(cog, interaction, "hi"))
    assert "Manage Server" in reply_text(interaction)
    assert storage.get_setting(cog.db, guild.id, "welcome_message") is None
