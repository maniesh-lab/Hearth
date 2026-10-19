"""
Moderation: kick, ban, mute (timeout), warn, an automatic spam filter,
saved warnings (/warnings, /clearwarnings) and an optional mod-log channel
(/modlog set) where every action is recorded.

Warnings and the mod-log setting live in data/hearth.db, so they survive
restarts. The spam counter stays in memory on purpose: it only looks at the
last few seconds of messages.
"""

import time
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

import storage

SPAM_WINDOW_SECONDS = 5
SPAM_MESSAGE_LIMIT = 5
MAX_TIMEOUT_MINUTES = 40320  # Discord's limit: 28 days
MODLOG_KEY = "modlog_channel"

# Keyed by (guild_id, user_id) so messages sent in two different servers
# don't add up toward one spam count.
message_log = defaultdict(lambda: deque(maxlen=SPAM_MESSAGE_LIMIT))


def moderation_problem(interaction: discord.Interaction, member: discord.Member):
    """Return why the person running the command can't act on `member`,
    or None if they can.

    Discord's permission check only asks "can this person kick at all?".
    It doesn't stop a moderator from kicking someone ranked above them, as
    long as the bot itself ranks higher — so that check happens here.
    """
    guild = interaction.guild
    if member.id == interaction.user.id:
        return "You can't use that on yourself."
    if member.id == guild.me.id:
        return "I can't use that on myself."
    if member.id == guild.owner_id:
        return "The server owner can't be moderated."
    if interaction.user.id != guild.owner_id and member.top_role >= interaction.user.top_role:
        return f"**{member}** has a role equal to or higher than yours."
    if member.top_role >= guild.me.top_role:
        return f"**{member}** has a role equal to or higher than mine — move my role above theirs first."
    return None


class Moderation(commands.Cog):
    def __init__(self, bot, db=None):
        self.bot = bot
        self.db = db or storage.connect()

    async def _refuse_if_needed(self, interaction, member):
        problem = moderation_problem(interaction, member)
        if problem:
            await interaction.response.send_message(problem, ephemeral=True)
            return True
        return False

    # --- mod log ---

    async def log_action(self, guild, action, target, moderator, reason):
        """Post a record of a moderation action to the server's mod-log channel, if one is set."""
        channel_id = storage.get_setting(self.db, guild.id, MODLOG_KEY)
        if channel_id is None:
            return
        channel = guild.get_channel(int(channel_id))
        if channel is None:
            return
        embed = discord.Embed(title=action, color=discord.Color.dark_red(), timestamp=discord.utils.utcnow())
        embed.add_field(name="Member", value=f"{target.mention} ({target})", inline=False)
        embed.add_field(name="By", value=moderator.mention if moderator else "Hearth (automatic)", inline=False)
        embed.add_field(name="Reason", value=reason[:1000] or "No reason given", inline=False)
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass  # e.g. the bot can't post in that channel any more

    modlog = app_commands.Group(
        name="modlog",
        description="Where moderation actions are recorded",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @modlog.command(name="set", description="Record kicks, bans, mutes and warnings in a channel")
    async def modlog_set(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("You need Manage Server to change this.", ephemeral=True)
            return
        storage.set_setting(self.db, interaction.guild.id, MODLOG_KEY, channel.id)
        await interaction.response.send_message(f"Moderation actions will be recorded in {channel.mention}.", ephemeral=True)

    @modlog.command(name="off", description="Stop recording moderation actions")
    async def modlog_off(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("You need Manage Server to change this.", ephemeral=True)
            return
        storage.delete_setting(self.db, interaction.guild.id, MODLOG_KEY)
        await interaction.response.send_message("Mod log turned off.", ephemeral=True)

    # --- actions ---

    @app_commands.command(name="kick", description="Kick a member")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given"):
        if await self._refuse_if_needed(interaction, member):
            return
        await member.kick(reason=reason)
        await interaction.response.send_message(f"Kicked **{member}** — {reason}")
        await self.log_action(interaction.guild, "👢 Kick", member, interaction.user, reason)

    @app_commands.command(name="ban", description="Ban a member")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given"):
        if await self._refuse_if_needed(interaction, member):
            return
        await member.ban(reason=reason)
        await interaction.response.send_message(f"Banned **{member}** — {reason}")
        await self.log_action(interaction.guild, "🔨 Ban", member, interaction.user, reason)

    @app_commands.command(name="mute", description="Timeout a member for a number of minutes (max 28 days)")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mute(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        minutes: app_commands.Range[int, 1, MAX_TIMEOUT_MINUTES],
        reason: str = "No reason given",
    ):
        if await self._refuse_if_needed(interaction, member):
            return
        until = discord.utils.utcnow() + timedelta(minutes=minutes)
        await member.timeout(until, reason=reason)
        await interaction.response.send_message(f"Muted **{member}** for {minutes}m — {reason}")
        await self.log_action(interaction.guild, f"🔇 Mute ({minutes}m)", member, interaction.user, reason)

    @app_commands.command(name="warn", description="Warn a member (saved, see /warnings)")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given"):
        if await self._refuse_if_needed(interaction, member):
            return
        self.db.execute(
            "INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            (interaction.guild.id, member.id, interaction.user.id, reason, time.time()),
        )
        self.db.commit()
        count = self.warning_count(interaction.guild.id, member.id)

        await interaction.response.send_message(f"Warned **{member}** ({count} total) — {reason}")
        await self.log_action(interaction.guild, f"⚠️ Warning #{count}", member, interaction.user, reason)
        try:
            await member.send(f"You were warned in **{interaction.guild.name}**: {reason}")
        except discord.Forbidden:
            pass

    def warning_count(self, guild_id, user_id):
        return self.db.execute(
            "SELECT COUNT(*) FROM warnings WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        ).fetchone()[0]

    @app_commands.command(name="warnings", description="Show a member's warnings")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warnings(self, interaction: discord.Interaction, member: discord.Member):
        rows = self.db.execute(
            "SELECT moderator_id, reason, created_at FROM warnings WHERE guild_id = ? AND user_id = ? "
            "ORDER BY created_at DESC LIMIT 10",
            (interaction.guild.id, member.id),
        ).fetchall()
        if not rows:
            await interaction.response.send_message(f"**{member}** has no warnings.", ephemeral=True)
            return

        total = self.warning_count(interaction.guild.id, member.id)
        lines = [f"<t:{int(r['created_at'])}:d> — {r['reason']} (by <@{r['moderator_id']}>)" for r in rows]
        title = f"⚠️ {member} — {total} warning{'s' if total != 1 else ''}"
        if total > len(rows):
            title += f" (latest {len(rows)})"
        embed = discord.Embed(title=title, description="\n".join(lines), color=discord.Color.orange())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="clearwarnings", description="Delete all of a member's warnings")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(moderate_members=True)
    async def clearwarnings(self, interaction: discord.Interaction, member: discord.Member):
        cursor = self.db.execute(
            "DELETE FROM warnings WHERE guild_id = ? AND user_id = ?", (interaction.guild.id, member.id)
        )
        self.db.commit()
        await interaction.response.send_message(f"Cleared {cursor.rowcount} warning(s) for **{member}**.", ephemeral=True)
        if cursor.rowcount:
            await self.log_action(interaction.guild, "🧹 Warnings cleared", member, interaction.user, f"{cursor.rowcount} removed")

    # --- spam filter ---

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return

        now = time.time()
        log = message_log[(message.guild.id, message.author.id)]
        log.append(now)

        if len(log) == SPAM_MESSAGE_LIMIT and (now - log[0]) < SPAM_WINDOW_SECONDS:
            until = discord.utils.utcnow() + timedelta(minutes=5)
            try:
                await message.author.timeout(until, reason="Automated spam filter")
                await message.channel.send(f"{message.author.mention} was muted for 5m — spamming.")
                await self.log_action(message.guild, "🔇 Auto-mute (5m)", message.author, None, "Spam filter")
            except discord.Forbidden:
                pass  # e.g. the spammer ranks above the bot
            log.clear()


async def setup(bot):
    await bot.add_cog(Moderation(bot))
