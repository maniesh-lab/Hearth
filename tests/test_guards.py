"""
The shared checks that keep delegated commands from doing more than the person
running them could do themselves.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import discord

from guards import can_post_in, permissions_beyond, short_list


def member(user_id=2, **perms):
    m = MagicMock()
    m.id = user_id
    m.guild_permissions = discord.Permissions(**perms)
    return m


EVERYONE = SimpleNamespace(name="@everyone")


def channel(role_gets=None, member_perms=None, everyone_gets=None):
    """A channel where holding the role gives `role_gets`, @everyone gets
    `everyone_gets` and the member has `member_perms` (like permissions_for)."""
    c = MagicMock()

    def permissions_for(who):
        if who is EVERYONE:
            return discord.Permissions(**(everyone_gets or {}))
        if isinstance(who, SimpleNamespace):  # the role
            return discord.Permissions(**{**(everyone_gets or {}), **(role_gets or {})})
        return discord.Permissions(**(member_perms or {}))

    c.permissions_for = permissions_for
    return c


def guild(*channels, owner_id=999):
    return SimpleNamespace(owner_id=owner_id, channels=list(channels), default_role=EVERYONE)


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
    staff = channel(role_gets={"view_channel": True, "manage_messages": True}, member_perms={})
    general = channel(role_gets={"view_channel": True}, member_perms={"view_channel": True})
    helper = member(send_messages=True)
    assert permissions_beyond(guild(general, staff), role(), helper) == ["Manage Messages", "View Channel"]


def test_a_channel_where_the_person_is_denied_still_counts():
    # They have Manage Messages server-wide but are denied it in #rules; the
    # role would give it there.
    rules = channel(role_gets={"manage_messages": True}, member_perms={"view_channel": True})
    helper = member(manage_messages=True)
    assert permissions_beyond(guild(rules), role(manage_messages=True), helper) == ["Manage Messages"]


def test_what_everyone_already_has_isnt_counted():
    general = channel(role_gets={"view_channel": True}, everyone_gets={"view_channel": True}, member_perms={})
    assert permissions_beyond(guild(general), role(), member()) == []


def test_owner_and_administrators_can_hand_out_anything():
    staff = channel(role_gets={"manage_messages": True}, member_perms={})
    assert permissions_beyond(guild(staff), role(ban_members=True), member(999)) == []
    assert permissions_beyond(guild(staff), role(ban_members=True), member(administrator=True)) == []


def test_long_lists_are_cut_short():
    assert short_list(["A", "B"]) == "A, B"
    assert short_list([str(i) for i in range(8)]) == "0, 1, 2, 3, 4 and 3 more"


def test_discord_names_are_used():
    helper = member()
    assert permissions_beyond(guild(), role(moderate_members=True, mention_everyone=True), helper) == [
        "Mention @everyone, @here and All Roles", "Timeout Members",
    ]


def test_can_post_in_needs_view_and_send():
    assert can_post_in(channel(member_perms={"view_channel": True, "send_messages": True}), member())
    assert not can_post_in(channel(member_perms={"view_channel": True}), member())
    assert not can_post_in(channel(member_perms={"send_messages": True}), member())


def test_bot_never_pings_everyone_or_roles_by_default():
    from main import bot

    assert bot.allowed_mentions.everyone is False
    assert bot.allowed_mentions.roles is False
    assert bot.allowed_mentions.users is True
