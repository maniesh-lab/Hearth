import discord
from discord.ext import commands
from discord import app_commands
from datetime import timedelta
from collections import defaultdict, deque
import time

warnings = defaultdict(list)  # user_id -> list of reasons, in-memory for now
message_log = defaultdict(lambda: deque(maxlen=5))  # user_id -> last 5 message timestamps

SPAM_WINDOW_SECONDS = 5
SPAM_MESSAGE_LIMIT = 5

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="kick", description="Kick a member")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given"):
        await member.kick(reason=reason)
        await interaction.response.send_message(f"Kicked **{member}** — {reason}")

    @app_commands.command(name="ban", description="Ban a member")
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given"):
        await member.ban(reason=reason)
        await interaction.response.send_message(f"Banned **{member}** — {reason}")

    @app_commands.command(name="mute", description="Timeout a member for a number of minutes")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mute(self, interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "No reason given"):
        until = discord.utils.utcnow() + timedelta(minutes=minutes)
        await member.timeout(until, reason=reason)
        await interaction.response.send_message(f"Muted **{member}** for {minutes}m — {reason}")

    @app_commands.command(name="warn", description="Warn a member")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given"):
        warnings[member.id].append(reason)
        count = len(warnings[member.id])
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
        log = message_log[message.author.id]
        log.append(now)

        if len(log) == SPAM_MESSAGE_LIMIT and (now - log[0]) < SPAM_WINDOW_SECONDS:
            until = discord.utils.utcnow() + timedelta(minutes=5)
            try:
                await message.author.timeout(until, reason="Automated spam filter")
                await message.channel.send(f"{message.author.mention} was muted for 5m — spamming.")
            except discord.Forbidden:
                pass
            log.clear()

    async def cog_app_command_error(self, interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You don't have permission for that.", ephemeral=True)
        else:
            print(f"[moderation] Error: {error}")

async def setup(bot):
    await bot.add_cog(Moderation(bot))