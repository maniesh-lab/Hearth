import json
from pathlib import Path
import discord
from discord.ext import commands
from discord import app_commands

ROLE_MAP = {
    "🎨": "Creative",
    "🎮": "Gaming",
    "💻": "Dev",
}

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "role_message_ids.json"

def _load_ids() -> dict:
    try:
        with open(DATA_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_ids(ids: dict):
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_PATH, "w") as f:
        json.dump(ids, f)

class Roles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.role_message_ids = _load_ids()  # {guild_id (str): message_id}

    @app_commands.command(name="rolesetup", description="Post the reaction-role message (admin only)")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def rolesetup(self, interaction: discord.Interaction):
        description = "\n".join(f"{emoji} — {name}" for emoji, name in ROLE_MAP.items())
        description += "\n\nReact to add a role, remove your reaction to take it off."
        embed = discord.Embed(title="Pick your roles", description=description, color=discord.Color.dark_teal())
        message = await interaction.channel.send(embed=embed)
        for emoji in ROLE_MAP:
            await message.add_reaction(emoji)
        self.role_message_ids[str(interaction.guild.id)] = message.id
        _save_ids(self.role_message_ids)
        await interaction.response.send_message("Role message posted.", ephemeral=True)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.user_id == self.bot.user.id or payload.guild_id is None:
            return
        if self.role_message_ids.get(str(payload.guild_id)) != payload.message_id:
            return
        role_name = ROLE_MAP.get(str(payload.emoji))
        if not role_name:
            return
        guild = self.bot.get_guild(payload.guild_id)
        role = discord.utils.get(guild.roles, name=role_name)
        member = guild.get_member(payload.user_id)
        if role and member:
            await member.add_roles(role)
            channel = guild.get_channel(payload.channel_id)
            msg = await channel.send(f"{member.mention} picked up **{role_name}**.")
            await msg.delete(delay=4)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        if payload.guild_id is None:
            return
        if self.role_message_ids.get(str(payload.guild_id)) != payload.message_id:
            return
        role_name = ROLE_MAP.get(str(payload.emoji))
        if not role_name:
            return
        guild = self.bot.get_guild(payload.guild_id)
        role = discord.utils.get(guild.roles, name=role_name)
        member = guild.get_member(payload.user_id)
        if role and member:
            await member.remove_roles(role)
            channel = guild.get_channel(payload.channel_id)
            msg = await channel.send(f"{member.mention} removed **{role_name}**.Didn't mean to? Just tap on it again to get it back.")
            await msg.delete(delay=4)

    @app_commands.command(name="role", description="Toggle a role on or off for yourself")
    @app_commands.choices(role=[app_commands.Choice(name=n, value=n) for n in ROLE_MAP.values()])
    async def role(self, interaction: discord.Interaction, role: app_commands.Choice[str]):
        target = discord.utils.get(interaction.guild.roles, name=role.value)
        if not target:
            await interaction.response.send_message(f"'{role.value}' role doesn't exist here yet.", ephemeral=True)
            return
        if target in interaction.user.roles:
            await interaction.user.remove_roles(target)
            await interaction.response.send_message(f"Removed **{role.value}**.", ephemeral=True)
        else:
            await interaction.user.add_roles(target)
            await interaction.response.send_message(f"Added **{role.value}**.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Roles(bot))