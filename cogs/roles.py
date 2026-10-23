"""
Self-serve roles: a reaction-role picker message plus a /role command.

Each server sets up its own list of roles with /roleadd and /roleremove,
so no code changes are needed to use Hearth in a new server.

- /roleadd takes one or several pairs at once: "🎮 @Gamer 🎨 @Artist".
  Adding a role that's already on the list with a new emoji moves it.
- /rolesetup posts the picker once. After that, /roleadd and /roleremove
  update that same message (text and reactions) by themselves.

Saved per server in data/roles.json:
    {
      "<guild_id>": {
        "message_id": 123,                 # the picker message, or null
        "channel_id": 321,                 # where it is, so it can be edited
        "roles": {"🎨": 456, "🎮": 789}    # emoji -> role ID
      }
    }
Role IDs are stored instead of names, so renaming a role doesn't break it.
"""

import json
import re
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


# Pieces of a /roleadd line: a role mention <@&123>, a custom emoji <:name:123>,
# or any other word (a normal emoji, or @RoleName typed without picking it).
TOKEN_RE = re.compile(r"<@&\d+>|<a?:\w+:\d+>|@[^\s<]+|[^\s<@]+")


def find_role(guild, text):
    """A role from "<@&123>" or "@Name" / "Name" (if exactly one role has that name)."""
    mention = re.fullmatch(r"<@&(\d+)>", text)
    if mention:
        return guild.get_role(int(mention.group(1)))
    name = text.lstrip("@").lower()
    matches = [r for r in guild.roles if r.name.lower() == name]
    return matches[0] if len(matches) == 1 else None


def parse_role_pairs(text, guild):
    """Turn "🎮 @Gamer 🎨 @Artist" into ([(emoji, role), ...], [problems])."""
    tokens = TOKEN_RE.findall(text)
    pairs, problems = [], []
    if not tokens:
        return pairs, ["Write an emoji and a role, e.g. `🎮 @Gamer 🎨 @Artist`."]
    if len(tokens) % 2:
        problems.append(f"`{tokens[-1]}` has nothing to pair with — write emoji, role, emoji, role…")
        tokens = tokens[:-1]
    for emoji_text, role_text in zip(tokens[::2], tokens[1::2]):
        emoji = normalize_emoji(emoji_text)
        role = find_role(guild, role_text)
        if emoji is None:
            problems.append(f"`{emoji_text}` isn't an emoji.")
        elif role is None:
            problems.append(f"I couldn't find the role `{role_text}` — pick it from the @ list.")
        else:
            pairs.append((emoji, role))
    return pairs, problems


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
            entry = {"message_id": None, "channel_id": None, "roles": {}}
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

    @app_commands.command(name="roleadd", description="Add pickable roles — one or more: 🎮 @Gamer 🎨 @Artist (admin only)")
    @app_commands.describe(roles="Emoji then role, as many pairs as you like, e.g. 🎮 @Gamer 🎨 @Artist")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_roles=True)
    async def roleadd(self, interaction: discord.Interaction, roles: str):
        pairs, problems = parse_role_pairs(roles, interaction.guild)
        settings = self._get_guild(interaction.guild)

        added = []
        for emoji, role in pairs:
            problem = self._role_problem(interaction, role)
            if problem:
                problems.append(problem)
                continue
            # The same role under a new emoji: move it rather than listing it twice.
            for old_emoji, role_id in list(settings["roles"].items()):
                if role_id == role.id and old_emoji != emoji:
                    del settings["roles"][old_emoji]
            if emoji not in settings["roles"] and len(settings["roles"]) >= MAX_ROLES:
                problems.append(f"Skipped **{role.name}** — you can have at most {MAX_ROLES} pickable roles.")
                continue
            settings["roles"][emoji] = role.id
            added.append(f"{emoji} → **{role.name}**")

        if not added:
            await interaction.response.send_message("\n".join(problems) or "Nothing to add.", ephemeral=True)
            return

        self._save()
        await interaction.response.defer(ephemeral=True)  # updating the picker can take a few seconds
        lines = ["Added: " + ", ".join(added)] + problems + [await self._refresh_note(interaction.guild)]
        await interaction.followup.send("\n".join(lines), ephemeral=True)

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
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(f"Removed {key}. " + await self._refresh_note(interaction.guild), ephemeral=True)

    # --- keeping the picker message up to date ---

    def _picker_text(self, guild):
        return "\n".join(self._picker_lines(guild)) + "\n\nReact to add a role, remove your reaction to take it off."

    async def _picker_message(self, guild):
        """The posted picker message, or None if there isn't one we can reach."""
        settings = self._get_guild(guild)
        if not settings.get("message_id") or not settings.get("channel_id"):
            return None
        channel = guild.get_channel(settings["channel_id"])
        if channel is None:
            return None
        try:
            return await channel.fetch_message(settings["message_id"])
        except discord.HTTPException:
            return None  # deleted, or the bot can't see the channel any more

    async def refresh_picker(self, guild):
        """Make the posted picker match the list: same text, one reaction per
        role. Returns True if a picker was found and updated."""
        message = await self._picker_message(guild)
        if message is None:
            return False

        settings = self._get_guild(guild)
        wanted = [emoji for emoji, role_id in settings["roles"].items() if guild.get_role(role_id)]
        embed = discord.Embed(title="Pick your roles", description=self._picker_text(guild), color=discord.Color.dark_teal())
        try:
            await message.edit(embed=embed)
            present = {str(r.emoji) for r in message.reactions if r.me}
            for emoji in present - set(wanted):
                await message.remove_reaction(emoji, guild.me)
            for emoji in wanted:
                if emoji not in present:
                    await message.add_reaction(emoji)
        except discord.HTTPException:
            return False
        return True

    async def _refresh_note(self, guild):
        if await self.refresh_picker(guild):
            return "The picker has been updated."
        return "Run `/rolesetup` in your roles channel to post the picker — after that it updates by itself."

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

    @app_commands.command(name="rolesetup", description="Post the role picker in this channel — replaces the old one (admin only)")
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

        # Only one picker per server: take the old one down (it's the bot's own
        # message, so no extra permission is needed).
        old = await self._picker_message(interaction.guild)
        if old is not None:
            try:
                await old.delete()
            except discord.HTTPException:
                pass

        embed = discord.Embed(
            title="Pick your roles", description=self._picker_text(interaction.guild), color=discord.Color.dark_teal()
        )
        message = await interaction.channel.send(embed=embed)

        settings = self._get_guild(interaction.guild)
        for emoji, role_id in settings["roles"].items():
            if interaction.guild.get_role(role_id):
                await message.add_reaction(emoji)

        settings["message_id"] = message.id
        settings["channel_id"] = interaction.channel.id
        self._save()
        await interaction.followup.send(
            "Role picker posted. From now on `/roleadd` and `/roleremove` update it by themselves.", ephemeral=True
        )

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
