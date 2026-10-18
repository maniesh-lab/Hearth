import asyncio
import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

COGS = [
    "cogs.onboarding",
    "cogs.roles",
    "cogs.moderation",
    "cogs.utility",
    "cogs.faq",
]


class Hearth(commands.Bot):
    async def setup_hook(self):
        # setup_hook runs once, before the bot connects. on_ready can fire
        # again on every reconnect, so loading cogs and syncing slash
        # commands there would repeat the work (and can hit rate limits).
        for cog in COGS:
            await self.load_extension(cog)
        await self.tree.sync()


intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = Hearth(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"{bot.user.name} is online")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """One place that turns any slash-command failure into a clear reply,
    so users never see 'The application did not respond'."""
    if isinstance(error, app_commands.MissingPermissions):
        message = "You don't have permission to use that command."
    elif isinstance(error, app_commands.NoPrivateMessage):
        message = "That command only works inside a server."
    elif isinstance(error, app_commands.CommandInvokeError) and isinstance(error.original, discord.Forbidden):
        message = (
            "I don't have permission to do that. Make sure my role is above "
            "the roles or members involved, and that I have the right permission."
        )
    else:
        name = interaction.command.name if interaction.command else "unknown"
        print(f"[error] /{name}: {error!r}")
        message = "Something went wrong running that command — try again in a moment."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


@bot.tree.command(name="ping", description="Check if Hearth is alive")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("pong")


async def main():
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN is missing — add it to your .env file.")
    async with bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
