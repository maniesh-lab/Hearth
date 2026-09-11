import discord
from discord.ext import commands
from discord import app_commands
import asyncio

class Utility(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="serverinfo", description="Show info about this server")
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
    async def poll(self, interaction: discord.Interaction, question: str):
        embed = discord.Embed(title="📊 Poll", description=question, color=discord.Color.dark_teal())
        embed.set_footer(text=f"Started by {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)
        message = await interaction.original_response()
        await message.add_reaction("👍")
        await message.add_reaction("👎")

    @app_commands.command(name="remind", description="Get reminded after a number of minutes")
    async def remind(self, interaction: discord.Interaction, minutes: int, message: str):
        await interaction.response.send_message(f"Got it — I'll remind you in {minutes}m.", ephemeral=True)
        await asyncio.sleep(minutes * 60)
        try:
            await interaction.user.send(f"⏰ Reminder: {message}")
        except discord.Forbidden:
            channel = interaction.channel
            await channel.send(f"{interaction.user.mention} ⏰ Reminder: {message}")

async def setup(bot):
    await bot.add_cog(Utility(bot))