"""
Welcoming new members: a DM, a public welcome message, and (optionally) a
role given automatically on join, like "Student".

Each server sets its own with /welcome:
- /welcome channel #general  — where the public welcome goes (default: the server's system channel)
- /welcome message ...       — the text; {user}, {server} and {count} are filled in
- /welcome role @Student     — role given on join (leave empty to turn off)
- /welcome show / test       — check the setup, or preview it on yourself

Settings are saved in data/hearth.db.
"""

import discord
from discord import app_commands
from discord.ext import commands

import storage

DEFAULT_MESSAGE = "Welcome, {user} — glad to have you here."
MAX_MESSAGE = 500


def render_welcome(template, member):
    """Fill in {user}, {server} and {count}. Plain replace instead of
    str.format, so other braces someone types can't crash it."""
    return (
        template.replace("{user}", member.mention)
        .replace("{server}", member.guild.name)
        .replace("{count}", str(member.guild.member_count))
    )


def auto_role_problem(guild, role, admin):
    """Why this role can't be handed out on join, or None if it can."""
    if role.is_default():
        return "@everyone can't be used as the welcome role."
    if role.managed:
        return f"**{role.name}** is managed by an integration or bot and can't be assigned."
    if role.permissions.administrator:
        return f"**{role.name}** has Administrator permission — it can't be given to everyone who joins."
    if role >= guild.me.top_role:
        return f"**{role.name}** is above my highest role, so I can't assign it. Move my role higher first."
    if admin.id != guild.owner_id and role >= admin.top_role:
        return f"**{role.name}** is equal to or above your highest role."
    return None


class Onboarding(commands.Cog):
    def __init__(self, bot, db=None):
        self.bot = bot
        self.db = db or storage.connect()

    def _welcome_channel(self, guild):
        channel_id = storage.get_setting(self.db, guild.id, "welcome_channel")
        if channel_id is not None:
            channel = guild.get_channel(int(channel_id))
            if channel is not None:
                return channel
        return guild.system_channel

    def _welcome_embed(self, member):
        template = storage.get_setting(self.db, member.guild.id, "welcome_message", DEFAULT_MESSAGE)
        embed = discord.Embed(
            title="Someone new just walked in",
            description=render_welcome(template, member),
            color=discord.Color.dark_teal(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Member #{member.guild.member_count}")
        return embed

    async def welcome(self, member):
        """Everything that happens when someone joins: role, DM, public welcome."""
        role_id = storage.get_setting(self.db, member.guild.id, "welcome_role")
        if role_id is not None:
            role = member.guild.get_role(int(role_id))
            if role is not None:
                try:
                    await member.add_roles(role, reason="Welcome role")
                except discord.HTTPException:
                    pass  # e.g. the role was moved above the bot's own

        try:
            dm_embed = discord.Embed(
                title=f"Welcome to {member.guild.name}",
                description="Glad you're here. Take a look around, and reach out if you need anything.",
                color=discord.Color.dark_teal(),
            )
            await member.send(embed=dm_embed)
        except discord.HTTPException:
            pass  # DMs closed

        channel = self._welcome_channel(member.guild)
        if channel is not None:
            try:
                await channel.send(
                    embed=self._welcome_embed(member),
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
            except discord.HTTPException:
                pass  # bot can't post in that channel

    @commands.Cog.listener()
    async def on_member_join(self, member):
        await self.welcome(member)

    # --- /welcome settings ---

    group = app_commands.Group(
        name="welcome",
        description="Set up how new members are welcomed",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    async def _check_admin(self, interaction):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("You need Manage Server to change this.", ephemeral=True)
            return False
        return True

    @group.command(name="channel", description="Where the public welcome message is posted")
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await self._check_admin(interaction):
            return
        storage.set_setting(self.db, interaction.guild.id, "welcome_channel", channel.id)
        await interaction.response.send_message(f"New members will be welcomed in {channel.mention}.", ephemeral=True)

    @group.command(name="message", description="The welcome text — {user}, {server} and {count} are filled in")
    @app_commands.describe(text='e.g. "Hey {user}, welcome to {server}! Grab your roles in #roles."')
    async def set_message(self, interaction: discord.Interaction, text: app_commands.Range[str, 1, MAX_MESSAGE]):
        if not await self._check_admin(interaction):
            return
        storage.set_setting(self.db, interaction.guild.id, "welcome_message", text)
        preview = render_welcome(text, interaction.user)
        await interaction.response.send_message(f"Welcome message saved. Preview:\n> {preview}", ephemeral=True)

    @group.command(name="role", description="Role given to everyone who joins (leave empty to turn off)")
    async def set_role(self, interaction: discord.Interaction, role: discord.Role | None = None):
        if not await self._check_admin(interaction):
            return
        if role is None:
            storage.delete_setting(self.db, interaction.guild.id, "welcome_role")
            await interaction.response.send_message("New members won't get a role automatically.", ephemeral=True)
            return
        problem = auto_role_problem(interaction.guild, role, interaction.user)
        if problem:
            await interaction.response.send_message(problem, ephemeral=True)
            return
        storage.set_setting(self.db, interaction.guild.id, "welcome_role", role.id)
        await interaction.response.send_message(f"New members will get **{role.name}** when they join.", ephemeral=True)

    @group.command(name="show", description="Show this server's welcome setup")
    async def show(self, interaction: discord.Interaction):
        guild = interaction.guild
        channel = self._welcome_channel(guild)
        role_id = storage.get_setting(self.db, guild.id, "welcome_role")
        role = guild.get_role(int(role_id)) if role_id else None
        template = storage.get_setting(self.db, guild.id, "welcome_message", DEFAULT_MESSAGE)
        await interaction.response.send_message(
            f"**Channel:** {channel.mention if channel else 'none (set one with `/welcome channel`)'}\n"
            f"**Role on join:** {role.name if role else 'none'}\n"
            f"**Message:** {template}",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @group.command(name="test", description="Preview the welcome as if you just joined (no role or DM)")
    async def test(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Here's how a new member will be welcomed:",
            embed=self._welcome_embed(interaction.user),
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(Onboarding(bot))
