import time
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

SPAM_WINDOW_SECONDS = 5
SPAM_MESSAGE_LIMIT = 5
MAX_TIMEOUT_MINUTES = 40320  # Discord's limit: 28 days

# Both are keyed by (guild_id, user_id) so each server keeps its own records:
# a warning in one server never shows up in another, and messages sent in
# two different servers don't add up toward one spam count.
warnings = defaultdict(list)  # (guild_id, user_id) -> list of reasons, in-memory for now
message_log = defaultdict(lambda: deque(maxlen=SPAM_MESSAGE_LIMIT))  # (guild_id, user_id) -> recent timestamps


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
    def __init__(self, bot):
        self.bot = bot

    async def _refuse_if_needed(self, interaction, member):
        problem = moderation_problem(interaction, member)
        if problem:
            await interaction.response.send_message(problem, ephemeral=True)
            return True
        return False

    @app_commands.command(name="kick", description="Kick a member")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given"):
        if await self._refuse_if_needed(interaction, member):
            return
        await member.kick(reason=reason)
        await interaction.response.send_message(f"Kicked **{member}** — {reason}")

    @app_commands.command(name="ban", description="Ban a member")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given"):
        if await self._refuse_if_needed(interaction, member):
            return
        await member.ban(reason=reason)
        await interaction.response.send_message(f"Banned **{member}** — {reason}")

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

    @app_commands.command(name="warn", description="Warn a member")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given"):
        if await self._refuse_if_needed(interaction, member):
            return
        key = (interaction.guild.id, member.id)
        warnings[key].append(reason)
        count = len(warnings[key])
        await interaction.response.send_message(f"Warned **{member}** ({count} total) — {reason}")
        try:
            await member.send(f"You were warned in **{interaction.guild.name}**: {reason}")
        except discord.Forbidden:
            pass

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
            except discord.Forbidden:
                pass  # e.g. the spammer ranks above the bot
            log.clear()


async def setup(bot):
    await bot.add_cog(Moderation(bot))
