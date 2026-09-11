import discord
from discord.ext import commands

class Onboarding(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member):
        # Personal DM
        try:
            dm_embed = discord.Embed(
                title=f"Welcome to {member.guild.name}",
                description="Glad you're here. Take a look around, and reach out if you need anything.",
                color=discord.Color.dark_teal()
            )
            await member.send(embed=dm_embed)
        except discord.Forbidden:
            pass

        # Public channel announcement
        channel = member.guild.system_channel
        if channel:
            channel_embed = discord.Embed(
                title="Someone new just walked in",
                description=f"Welcome, {member.mention} — glad to have you here.",
                color=discord.Color.dark_teal()
            )
            channel_embed.set_thumbnail(url=member.display_avatar.url)
            channel_embed.set_footer(text=f"Member #{member.guild.member_count}")
            await channel.send(embed=channel_embed)

async def setup(bot):
    await bot.add_cog(Onboarding(bot))