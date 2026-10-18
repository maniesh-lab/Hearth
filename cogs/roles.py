"""
Self-serve roles: a reaction-role picker message plus a /role command.

Each server sets up its own list of roles with /roleadd and /roleremove,
so no code changes are needed to use Hearth in a new server.

Saved per server in data/roles.json:
    {
      "<guild_id>": {
        "message_id": 123,                 # the picker message, or null
        "roles": {"🎨": 456, "🎮": 789}    # emoji -> role ID
      }
    }
Role IDs are stored instead of names, so renaming a role doesn't break it.
"""

import json
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONFIG_PATH = DATA_DIR / "roles.json"

# Older versions of Hearth hardcoded these three roles and kept only the
# picker message IDs, in role_message_ids.json. Servers set up that way are
# moved to the new format the first time they're used (see _get_guild).
LEGACY_IDS_PATH = DATA_DIR / "role_message_ids.json"
LEGACY_ROLE_NAMES = {"🎨": "Creative", "🎮": "Gaming", "💻": "Dev"}

MAX_ROLES = 20  # Discord allows at most 20 different reactions on one message


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def normalize_emoji(text):
    """Turn whatever the admin typed into the same string Discord reports
    on reactions, e.g. '<:party:123>' for custom emoji, '🎨' for normal ones.
    Returns None if it doesn't look like an emoji at all."""
    text = text.strip()
    emoji = discord.PartialEmoji.from_str(text)
    if emoji.id:  # custom server emoji
        return str(emoji)
    # A normal emoji is a few unicode symbols with no letters, digits or spaces.
    if text and len(text) <= 8 and not any(ch.isalnum() or ch.isspace() for ch in text):
        return text
    return None


class Roles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = _read_json(CONFIG_PATH)
        self.legacy_message_ids = _read_json(LEGACY_IDS_PATH)

    def _save(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)

    def _get_guild(self, guild):
        """This server's settings, creating them on first use."""
        key = str(guild.id)
        if key not in self.config:
            entry = {"message_id": None, "roles": {}}
            legacy_message = self.legacy_message_ids.get(key)
            if legacy_message:
                # Carry an old-style setup over: same picker message, and the
                # three old roles if they exist in this server.
                entry["message_id"] = legacy_message
                for emoji, name in LEGACY_ROLE_NAMES.items():
                    role = discord.utils.get(guild.roles, name=name)
                    if role:
                        entry["roles"][emoji] = role.id
            self.config[key] = entry
            self._save()
        return self.config[key]

    def _role_problem(self, interaction, role):
        """Why this role can't be offered for self-assignment, or None if it can."""
        guild = interaction.guild
        if role.is_default():
            return "@everyone can't be used as a pickable role."
        if role.managed:
            return f"**{role.name}** is managed by an integration or bot and can't be assigned."
        if role.permissions.administrator:
            return f"**{role.name}** has Administrator permission — it can't be self-assignable."
        if role >= guild.me.top_role:
            return f"**{role.name}** is above my highest role, so I can't assign it. Move my role higher first."
        if interaction.user.id != guild.owner_id and role >= interaction.user.top_role:
            return f"**{role.name}** is equal to or above your highest role, so you can't make it pickable."
        return None

    # --- admin commands: set up this server's roles ---

    @app_commands.command(name="roleadd", description="Add a role members can pick for themselves (admin only)")
    @app_commands.describe(emoji="The emoji members react with", role="The role they get")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_roles=True)
    async def roleadd(self, interaction: discord.Interaction, emoji: str, role: discord.Role):
        key = normalize_emoji(emoji)
        if key is None:
            await interaction.response.send_message("That doesn't look like an emoji — try again with a single emoji.", ephemeral=True)
            return

        problem = self._role_problem(interaction, role)
        if problem:
            await interaction.response.send_message(problem, ephemeral=True)
            return

        settings = self._get_guild(interaction.guild)
        if key not in settings["roles"] and len(settings["roles"]) >= MAX_ROLES:
            await interaction.response.send_message(f"You can have at most {MAX_ROLES} pickable roles.", ephemeral=True)
            return

        settings["roles"][key] = role.id
        self._save()
        await interaction.response.send_message(
            f"{key} now gives **{role.name}**. Run `/rolesetup` to post an updated picker.", ephemeral=True
        )

    @app_commands.command(name="roleremove", description="Stop offering a role in the picker (admin only)")
    @app_commands.describe(emoji="The emoji of the role to remove")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_roles=True)
    async def roleremove(self, interaction: discord.Interaction, emoji: str):
        settings = self._get_guild(interaction.guild)
        key = normalize_emoji(emoji)
        if key not in settings["roles"]:
            await interaction.response.send_message("That emoji isn't in the role list. See `/rolelist`.", ephemeral=True)
            return

        del settings["roles"][key]
        self._save()
        await interaction.response.send_message(
            f"Removed {key}. Run `/rolesetup` to post an updated picker.", ephemeral=True
        )

    @app_commands.command(name="rolelist", description="Show the roles members can pick")
    @app_commands.guild_only()
    async def rolelist(self, interaction: discord.Interaction):
        lines = self._picker_lines(interaction.guild)
        if not lines:
            await interaction.response.send_message("No pickable roles yet — an admin can add some with `/roleadd`.", ephemeral=True)
            return
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    def _picker_lines(self, guild):
        settings = self._get_guild(guild)
        lines = []
        for emoji, role_id in settings["roles"].items():
            role = guild.get_role(role_id)
            if role:  # skip roles that were deleted from the server
                lines.append(f"{emoji} — {role.name}")
        return lines

    @app_commands.command(name="rolesetup", description="Post the reaction-role picker in this channel (admin only)")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_roles=True)
    async def rolesetup(self, interaction: discord.Interaction):
        lines = self._picker_lines(interaction.guild)
        if not lines:
            await interaction.response.send_message("Add at least one role with `/roleadd` first.", ephemeral=True)
            return

        # Adding several reactions can take longer than the 3 seconds Discord
        # gives us to reply, so acknowledge first and finish afterwards.
        await interaction.response.defer(ephemeral=True)

        description = "\n".join(lines) + "\n\nReact to add a role, remove your reaction to take it off."
        embed = discord.Embed(title="Pick your roles", description=description, color=discord.Color.dark_teal())
        message = await interaction.channel.send(embed=embed)

        settings = self._get_guild(interaction.guild)
        for emoji, role_id in settings["roles"].items():
            if interaction.guild.get_role(role_id):
                await message.add_reaction(emoji)

        settings["message_id"] = message.id
        self._save()
        await interaction.followup.send("Role picker posted.", ephemeral=True)

    # --- reaction picker ---

    def _role_for_reaction(self, payload):
        """The guild and role a reaction refers to, or (None, None) if it
        isn't a reaction on this server's picker message."""
        if payload.guild_id is None:
            return None, None
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return None, None
        settings = self._get_guild(guild)
        if settings["message_id"] != payload.message_id:
            return None, None
        role_id = settings["roles"].get(str(payload.emoji))
        role = guild.get_role(role_id) if role_id else None
        return guild, role

    async def _announce(self, guild, channel_id, text):
        channel = guild.get_channel(channel_id)
        if channel:
            msg = await channel.send(text)
            await msg.delete(delay=4)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.user_id == self.bot.user.id:
            return
        guild, role = self._role_for_reaction(payload)
        member = guild.get_member(payload.user_id) if guild else None
        if role and member:
            await member.add_roles(role, reason="Reaction role picker")
            await self._announce(guild, payload.channel_id, f"{member.mention} picked up **{role.name}**.")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        guild, role = self._role_for_reaction(payload)
        member = guild.get_member(payload.user_id) if guild else None
        if role and member:
            await member.remove_roles(role, reason="Reaction role picker")
            await self._announce(
                guild,
                payload.channel_id,
                f"{member.mention} removed **{role.name}**. Didn't mean to? Just tap it again to get it back.",
            )

    # --- slash command alternative to reacting ---

    @app_commands.command(name="role", description="Toggle one of this server's pickable roles on or off")
    @app_commands.guild_only()
    async def role(self, interaction: discord.Interaction, role: discord.Role):
        settings = self._get_guild(interaction.guild)
        if role.id not in settings["roles"].values():
            await interaction.response.send_message(
                f"**{role.name}** isn't a pickable role here. See `/rolelist`.", ephemeral=True
            )
            return

        if role in interaction.user.roles:
            await interaction.user.remove_roles(role, reason="/role command")
            await interaction.response.send_message(f"Removed **{role.name}**.", ephemeral=True)
        else:
            await interaction.user.add_roles(role, reason="/role command")
            await interaction.response.send_message(f"Added **{role.name}**.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Roles(bot))
