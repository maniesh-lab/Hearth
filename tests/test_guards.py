"""
The shared checks that keep delegated commands from doing more than the person
running them could do themselves.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import discord

from guards import can_post_in, permissions_beyond


def member(user_id=2, **perms):
    m = MagicMock()
    m.id = user_id
    m.guild_permissions = discord.Permissions(**perms)
    return m


def channel(role_allows=None, member_perms=None):
    """A channel whose overrides for the role allow `role_allows`, and where
    the member has `member_perms`."""
    c = MagicMock()
    allow = discord.PermissionOverwrite(**(role_allows or {}))
    c.overwrites_for = lambda target: allow
    c.permissions_for = lambda who: discord.Permissions(**(member_perms or {}))
    return c


def guild(*channels, owner_id=999):
    return SimpleNamespace(owner_id=owner_id, channels=list(channels))


def role(**perms):
    return SimpleNamespace(permissions=discord.Permissions(**perms))


def test_a_role_with_nothing_extra_is_fine():
    helper = member(send_messages=True, view_channel=True)
    assert permissions_beyond(guild(), role(send_messages=True), helper) == []


def test_server_wide_permissions_the_person_lacks_are_listed():
    helper = member(send_messages=True)
    assert permissions_beyond(guild(), role(ban_members=True, kick_members=True), helper) == [
        "Ban Members", "Kick Members",
    ]


def test_channel_overrides_count_too():
    # A role with no server-wide permissions that unlocks a private #staff channel.
    staff = channel(role_allows={"view_channel": True, "manage_messages": True}, member_perms={})
    general = channel(role_allows={"view_channel": True}, member_perms={"view_channel": True})
    helper = member(send_messages=True)
    assert permissions_beyond(guild(general, staff), role(), helper) == ["Manage Messages", "View Channel"]


def test_owner_and_administrators_can_hand_out_anything():
    staff = channel(role_allows={"manage_messages": True}, member_perms={})
    assert permissions_beyond(guild(staff), role(ban_members=True), member(999)) == []
    assert permissions_beyond(guild(staff), role(ban_members=True), member(administrator=True)) == []


def test_can_post_in_needs_view_and_send():
    assert can_post_in(channel(member_perms={"view_channel": True, "send_messages": True}), member())
    assert not can_post_in(channel(member_perms={"view_channel": True}), member())
    assert not can_post_in(channel(member_perms={"send_messages": True}), member())


def test_bot_never_pings_everyone_or_roles_by_default():
    from main import bot

    assert bot.allowed_mentions.everyone is False
    assert bot.allowed_mentions.roles is False
    assert bot.allowed_mentions.users is True
