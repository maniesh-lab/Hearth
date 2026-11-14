"""
Checks shared by commands that a server can hand to people without admin
permissions (Server Settings → Integrations → Hearth).

Hearth acts with its own permissions, so these make sure nobody can use it to
do something they couldn't do themselves: hand out a role carrying powers
they lack, or post into a channel they can't post in.
"""

import discord


# discord.py's names that differ from what Discord shows in Server Settings.
DISPLAY_NAMES = {"read_messages": "View Channel", "manage_guild": "Manage Server"}


def permission_names(perms):
    """["Ban Members", "View Channel"] for the permissions that are on."""
    return sorted(DISPLAY_NAMES.get(name, name.replace("_", " ").title()) for name, value in perms if value)


def permissions_beyond(guild, role, member):
    """Names of the permissions `role` would give that `member` doesn't have —
    server-wide, or in any channel through the role's channel overrides.
    Empty for the server owner and for Administrators, who have everything."""
    perms = member.guild_permissions
    if member.id == guild.owner_id or perms.administrator:
        return []
    extra = role.permissions.value & ~perms.value
    for channel in guild.channels:
        allow, _ = channel.overwrites_for(role).pair()
        if allow.value:
            extra |= allow.value & ~channel.permissions_for(member).value
    return permission_names(discord.Permissions(extra))


def can_post_in(channel, member):
    perms = channel.permissions_for(member)
    return perms.view_channel and perms.send_messages
