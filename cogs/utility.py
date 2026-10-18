import asyncio

import discord
from discord import app_commands
from discord.ext import commands

MAX_REMINDER_MINUTES = 10080  # one week


class Utility(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="serverinfo", description="Show info about this server")
    @app_commands.guild_only()
    async def serverinfo(self, interaction: discord.Interaction):
        guild = interaction.guild
        embed = discord.Embed(title=guild.name, color=discord.Color.dark_teal())
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Members", value=guild.member_count)
        embed.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, style="D"))
        embed.add_field(name="Owner", value=guild.owner.mention if guild.owner else "Unknown")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="poll", description="Post a quick yes/no poll")
    @app_commands.guild_only()
    async def poll(self, interaction: discord.Interaction, question: str):
        embed = discord.Embed(title="📊 Poll", description=question, color=discord.Color.dark_teal())
        embed.set_footer(text=f"Started by {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)
        message = await interaction.original_response()
        await message.add_reaction("👍")
        await message.add_reaction("👎")

    @app_commands.command(name="remind", description="Get reminded after a number of minutes (max one week)")
    async def remind(
        self,
        interaction: discord.Interaction,
        minutes: app_commands.Range[int, 1, MAX_REMINDER_MINUTES],
        message: str,
    ):
        await interaction.response.send_message(f"Got it — I'll remind you in {minutes}m.", ephemeral=True)
        # Reminders live in memory only: if the bot restarts before this
        # wakes up, the reminder is lost (see Notes in the README).
        await asyncio.sleep(minutes * 60)
        try:
            await interaction.user.send(f"⏰ Reminder: {message}")
        except discord.Forbidden:
            # DMs are closed — fall back to the channel the command was used in.
            channel = interaction.channel
            if channel is not None:
                try:
                    await channel.send(f"{interaction.user.mention} ⏰ Reminder: {message}")
                except discord.HTTPException:
                    pass


async def setup(bot):
    await bot.add_cog(Utility(bot))
