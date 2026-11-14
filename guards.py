"""
Checks shared by commands that a server can hand to people without admin
permissions (Server Settings → Integrations → Hearth).

Hearth acts with its own permissions, so these make sure nobody can use it to
do something they couldn't do themselves: hand out a role carrying powers
they lack, or post into a channel they can't post in.
"""

import discord


# discord.py's names that differ from what Discord shows in Server Settings.
DISPLAY_NAMES = {
    "read_messages": "View Channel",
    "manage_guild": "Manage Server",
    "moderate_members": "Timeout Members",
    "create_instant_invite": "Create Invite",
    "send_tts_messages": "Send Text-to-Speech Messages",
    "external_emojis": "Use External Emojis",
    "external_stickers": "Use External Stickers",
    "mention_everyone": "Mention @everyone, @here and All Roles",
    "view_guild_insights": "View Server Insights",
    "use_embedded_activities": "Use Activities",
}


def permission_names(perms):
    """["Ban Members", "View Channel"] for the permissions that are on."""
    return sorted(DISPLAY_NAMES.get(name, name.replace("_", " ").title()) for name, value in perms if value)


def short_list(names, limit=5):
    """'A, B, C' — or 'A, B, C, D, E and 12 more', so a refusal stays short."""
    if len(names) <= limit:
        return ", ".join(names)
    return f"{', '.join(names[:limit])} and {len(names) - limit} more"


def permissions_beyond(guild, role, member):
    """Names of the permissions `role` would give that `member` doesn't have —
    server-wide, or in any channel through the role's channel overrides.
    Empty for the server owner and for Administrators, who have everything."""
    perms = member.guild_permissions
    if member.id == guild.owner_id or perms.administrator:
        return []
    extra = role.permissions.value & ~perms.value
    everyone = guild.default_role
    for channel in guild.channels:
        # What holding the role adds in this channel (its server-wide
        # permissions and its overrides), compared with what the person can
        # do there — so a channel where they're denied something still counts.
        adds = channel.permissions_for(role).value & ~channel.permissions_for(everyone).value
        extra |= adds & ~channel.permissions_for(member).value
    return permission_names(discord.Permissions(extra))


def can_post_in(channel, member):
    perms = channel.permissions_for(member)
    return perms.view_channel and perms.send_messages
