import os
from dotenv import load_dotenv
import discord
from discord.ext import commands
import asyncio

load_dotenv()
discord_key = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

GUILD_ID = discord.Object(id=1547528134696636436)  # your test server

@bot.event
async def on_ready():
    bot.tree.copy_global_to(guild=GUILD_ID)
    await bot.tree.sync(guild=GUILD_ID)
    print(f"{bot.user.name} is online")


@bot.tree.command(name="ping", description="Check if Hearth is alive")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("pong")


async def main():
    async with bot:
        await bot.load_extension("cogs.onboarding")
        await bot.load_extension("cogs.roles")
        await bot.load_extension("cogs.moderation")
        await bot.load_extension("cogs.utility")
        await bot.load_extension("cogs.faq")
        await bot.start(discord_key)

asyncio.run(main())