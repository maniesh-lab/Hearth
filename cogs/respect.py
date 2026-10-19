"""
Respect points: saying thanks to someone gives them +1 respect.

How a thank-you is counted:
- The message says thanks (thanks, thank you, ty, thx, ...)
- AND it's aimed at someone: they're @mentioned, or the message is a reply to them
- You can't thank yourself or a bot

Normal thank-yous always count, even several in a row. Two rules stop abuse:
- Spam: 5 thank-you messages within 10 minutes pauses that person's thanks
  for 24 hours (the 5th doesn't count), and Hearth says so in the channel
- Farming: one person can give the same person at most 10 points a day

Points add up to levels. Reaching a new level is announced in the channel.

Optional level roles (/levelroles on|off): Hearth creates one role per level
("Helper (25+ respect)"), shown separately in the member list, and swaps a
member's role when they level up. On servers with Boost Level 2 the level
emoji becomes the role icon beside their name; elsewhere it goes in the
role name instead. The roles have no color, so names keep their usual color.

Saved per server in data/respect.db (SQLite), so each server has its own
scores and leaderboard. Level-role settings live in data/hearth.db.
"""

import re
import sqlite3
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

import storage

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "respect.db"

SPAM_MESSAGES = 5               # this many thank-you messages...
SPAM_WINDOW_SECONDS = 10 * 60    # ...within 10 minutes counts as spam
SPAM_PAUSE_SECONDS = 24 * 60 * 60  # the spammer's thanks stop counting for a day
DAILY_LIMIT_PER_PAIR = 10        # max points one person can give the same person per day
DAY_SECONDS = 24 * 60 * 60
MAX_THANKED_PER_MESSAGE = 5  # "thanks @a @b @c ..." counts for at most 5 people

# (points needed, level name, emoji). Level 1 starts at the very first thank-you.
LEVELS = [
    (1, "Newbie", "🌱"),
    (25, "Helper", "🤝"),
    (50, "Contributor", "🧩"),
    (100, "Regular", "⭐"),
    (500, "Veteran", "🔥"),
    (1000, "Legend", "👑"),
]

LEVEL_ROLES_SETTING = "level_roles"  # "on" while level roles are turned on
ROLE_ID_SETTING = "level_role_{}"    # the role ID for each level number

# Whole words only, so "type" or "thanksgiving" don't count.
THANKS_RE = re.compile(
    r"\b(thanks|thank[\s-]*you|thank[\s-]*u|thanx|thnx|thx|tysm|tyvm|ty)\b",  # "thank-you" too
    re.IGNORECASE,
)
# "no thanks" is a polite refusal, not a thank-you.
NO_THANKS_RE = re.compile(r"\bno\s+(thanks|thank[\s-]*you|thx)\b", re.IGNORECASE)


def says_thanks(text):
    return bool(THANKS_RE.search(text)) and not NO_THANKS_RE.search(text)


def level_for(points):
    """Return (level number, level name) for a point total. Level 0 = no points yet."""
    number, name = 0, "No level yet"
    for i, (needed, level_name, _) in enumerate(LEVELS, start=1):
        if points >= needed:
            number, name = i, level_name
    return number, name


def next_level(points):
    """Return (points needed, level name) for the next level, or None at the top."""
    for needed, name, _ in LEVELS:
        if points < needed:
            return needed, name
    return None


def level_emoji(number):
    """The emoji for a level number ("" for level 0)."""
    return LEVELS[number - 1][2] if number >= 1 else ""


def level_label(number, name):
    """e.g. "🤝 Helper" — the emoji and name shown in every message."""
    emoji = level_emoji(number)
    return f"{emoji} {name}" if emoji else name


def level_role_name(number, with_emoji):
    """e.g. "Helper (25+ respect)". The emoji goes in the name only when the
    server can't show it as a role icon."""
    needed, name, emoji = LEVELS[number - 1]
    base = f"{name} ({needed}+ respect)"
    return f"{emoji} {base}" if with_emoji else base


class RespectStore:
    """All the SQL lives here, so the cog below only deals with Discord."""

    def __init__(self, path=DB_PATH):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS respect (
                guild_id INTEGER NOT NULL,
                user_id  INTEGER NOT NULL,
                points   INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            );
            -- every point given, for the daily limit per pair
            CREATE TABLE IF NOT EXISTS gives (
                guild_id    INTEGER NOT NULL,
                giver_id    INTEGER NOT NULL,
                receiver_id INTEGER NOT NULL,
                given_at    REAL    NOT NULL
            );
            -- every thank-you message, for spam detection
            CREATE TABLE IF NOT EXISTS thank_messages (
                guild_id INTEGER NOT NULL,
                giver_id INTEGER NOT NULL,
                sent_at  REAL    NOT NULL
            );
            -- people whose thanks are paused for spamming
            CREATE TABLE IF NOT EXISTS paused (
                guild_id INTEGER NOT NULL,
                giver_id INTEGER NOT NULL,
                until    REAL    NOT NULL,
                PRIMARY KEY (guild_id, giver_id)
            );
            -- the old one-hour cooldown table, no longer used
            DROP TABLE IF EXISTS last_thanks;
            """
        )
        self.db.commit()

    def points(self, guild_id, user_id):
        row = self.db.execute(
            "SELECT points FROM respect WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        return row[0] if row else 0

    def is_paused(self, guild_id, giver_id, now):
        row = self.db.execute(
            "SELECT until FROM paused WHERE guild_id = ? AND giver_id = ?", (guild_id, giver_id)
        ).fetchone()
        return row is not None and now < row[0]

    def pause(self, guild_id, giver_id, until):
        self.db.execute(
            """
            INSERT INTO paused (guild_id, giver_id, until) VALUES (?, ?, ?)
            ON CONFLICT (guild_id, giver_id) DO UPDATE SET until = excluded.until
            """,
            (guild_id, giver_id, until),
        )
        self.db.commit()

    def record_thank_message(self, guild_id, giver_id, now):
        """Remember one thank-you message. Returns how many this person sent
        in the last SPAM_WINDOW_SECONDS, including this one."""
        self.db.execute("DELETE FROM thank_messages WHERE sent_at < ?", (now - SPAM_WINDOW_SECONDS,))
        self.db.execute(
            "INSERT INTO thank_messages (guild_id, giver_id, sent_at) VALUES (?, ?, ?)",
            (guild_id, giver_id, now),
        )
        self.db.commit()
        return self.db.execute(
            "SELECT COUNT(*) FROM thank_messages WHERE guild_id = ? AND giver_id = ? AND sent_at > ?",
            (guild_id, giver_id, now - SPAM_WINDOW_SECONDS),
        ).fetchone()[0]

    def given_today(self, guild_id, giver_id, receiver_id, now):
        """Points this person gave that person in the last 24 hours."""
        return self.db.execute(
            "SELECT COUNT(*) FROM gives WHERE guild_id = ? AND giver_id = ? AND receiver_id = ? AND given_at > ?",
            (guild_id, giver_id, receiver_id, now - DAY_SECONDS),
        ).fetchone()[0]

    def give(self, guild_id, giver_id, receiver_id, now):
        """Add 1 point and record it. Returns the receiver's new total."""
        self.db.execute(
            """
            INSERT INTO respect (guild_id, user_id, points) VALUES (?, ?, 1)
            ON CONFLICT (guild_id, user_id) DO UPDATE SET points = points + 1
            """,
            (guild_id, receiver_id),
        )
        self.db.execute("DELETE FROM gives WHERE given_at < ?", (now - DAY_SECONDS,))
        self.db.execute(
            "INSERT INTO gives (guild_id, giver_id, receiver_id, given_at) VALUES (?, ?, ?, ?)",
            (guild_id, giver_id, receiver_id, now),
        )
        self.db.commit()
        return self.points(guild_id, receiver_id)

    def all_points(self, guild_id):
        return self.db.execute(
            "SELECT user_id, points FROM respect WHERE guild_id = ? AND points > 0", (guild_id,)
        ).fetchall()

    def top(self, guild_id, limit=10):
        return self.db.execute(
            "SELECT user_id, points FROM respect WHERE guild_id = ? AND points > 0 "
            "ORDER BY points DESC, user_id LIMIT ?",
            (guild_id, limit),
        ).fetchall()

    def rank(self, guild_id, user_id):
        """1 = top of the server. None if they have no points."""
        mine = self.points(guild_id, user_id)
        if mine == 0:
            return None
        above = self.db.execute(
            "SELECT COUNT(*) FROM respect WHERE guild_id = ? AND points > ?",
            (guild_id, mine),
        ).fetchone()[0]
        return above + 1


class Respect(commands.Cog):
    def __init__(self, bot, store=None, db=None):
        self.bot = bot
        self.store = store or RespectStore()
        self.db = db or storage.connect()

    # --- level roles ---

    def roles_on(self, guild_id):
        return storage.get_setting(self.db, guild_id, LEVEL_ROLES_SETTING) == "on"

    def _level_roles(self, guild):
        """{level number: Role} for the level roles that still exist."""
        roles = {}
        for number in range(1, len(LEVELS) + 1):
            role_id = storage.get_setting(self.db, guild.id, ROLE_ID_SETTING.format(number))
            role = guild.get_role(int(role_id)) if role_id else None
            if role is not None:
                roles[number] = role
        return roles

    async def _set_level_role(self, member, number, roles):
        """Give the member their level's role and take away the other level
        roles, so they only ever have one. Returns False if Discord refused."""
        wanted = roles.get(number)
        old = [role for n, role in roles.items() if n != number and role in member.roles]
        try:
            if old:
                await member.remove_roles(*old, reason="Respect level changed")
            if wanted is not None and wanted not in member.roles:
                await member.add_roles(wanted, reason=f"Reached respect level {number}")
        except discord.HTTPException:
            return False  # e.g. the level roles were moved above Hearth's own role
        return True

    async def _thanked_people(self, message):
        """Everyone this thank-you is aimed at: @mentions, plus the person replied to."""
        people = list(message.mentions)

        ref = message.reference
        if ref is not None and ref.message_id:
            replied = ref.resolved
            if not isinstance(replied, discord.Message):
                # Not in the bot's cache (e.g. an old message) — fetch it.
                try:
                    replied = await message.channel.fetch_message(ref.message_id)
                except discord.HTTPException:
                    replied = None
            if replied is not None:
                people.append(replied.author)

        unique = []
        seen = set()
        for person in people:
            if person.bot or person.id == message.author.id or person.id in seen:
                continue
            seen.add(person.id)
            unique.append(person)
        return unique[:MAX_THANKED_PER_MESSAGE]

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None:
            return
        if not says_thanks(message.content):
            return

        people = await self._thanked_people(message)
        if not people:
            return  # "thanks" not aimed at anyone, so it isn't a thank-you to count

        now = time.time()
        guild_id = message.guild.id
        giver = message.author

        if self.store.is_paused(guild_id, giver.id, now):
            return

        if self.store.record_thank_message(guild_id, giver.id, now) >= SPAM_MESSAGES:
            self.store.pause(guild_id, giver.id, now + SPAM_PAUSE_SECONDS)
            try:
                await message.channel.send(
                    f"⏳ **{giver.display_name}** is sending thanks too fast — "
                    "their respect is paused for 24 hours."
                )
            except discord.HTTPException:
                pass
            return

        for person in people:
            if self.store.given_today(guild_id, giver.id, person.id, now) >= DAILY_LIMIT_PER_PAIR:
                continue

            before, _ = level_for(self.store.points(guild_id, person.id))
            total = self.store.give(guild_id, giver.id, person.id, now)
            after, name = level_for(total)

            if after > before and self.roles_on(guild_id):
                member = message.guild.get_member(person.id)
                if member is not None:
                    await self._set_level_role(member, after, self._level_roles(message.guild))

            if after > before:
                try:
                    await message.channel.send(
                        f"🎉 {person.mention} reached **Level {after} — {level_label(after, name)}** "
                        f"({total} respect)!"
                    )
                except discord.HTTPException:
                    pass  # e.g. the bot can't post in this channel

    @app_commands.command(name="respect", description="Show someone's respect points and level")
    @app_commands.describe(member="Who to check (leave empty for yourself)")
    @app_commands.guild_only()
    async def respect(self, interaction: discord.Interaction, member: discord.Member | None = None):
        member = member or interaction.user
        points = self.store.points(interaction.guild.id, member.id)
        number, name = level_for(points)

        if points == 0:
            text = f"**{member.display_name}** has no respect points yet. Thank them when they help you!"
        else:
            rank = self.store.rank(interaction.guild.id, member.id)
            text = (
                f"**{member.display_name}** — {level_emoji(number)} Level {number} ({name}) · "
                f"{points} respect · #{rank} in this server"
            )
            upcoming = next_level(points)
            if upcoming:
                needed, next_name = upcoming
                next_number = level_for(needed)[0]
                text += f"\n{needed - points} more to reach {level_label(next_number, next_name)}."
            else:
                text += "\nTop level reached. 👑"

        await interaction.response.send_message(text)

    @app_commands.command(name="leaderboard", description="Top 10 most respected members in this server")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction):
        rows = self.store.top(interaction.guild.id)
        if not rows:
            await interaction.response.send_message("No respect points yet — thank someone who helped you!")
            return

        lines = []
        for place, (user_id, points) in enumerate(rows, start=1):
            member = interaction.guild.get_member(user_id)
            who = member.display_name if member else f"Former member ({user_id})"
            number, name = level_for(points)
            lines.append(f"**{place}.** {level_emoji(number)} {who} — {points} ({name})")

        embed = discord.Embed(title="🏆 Respect leaderboard", description="\n".join(lines), color=discord.Color.dark_teal())
        await interaction.response.send_message(embed=embed)


    levelroles = app_commands.Group(
        name="levelroles",
        description="Give members a role for their respect level",
        guild_only=True,
        default_permissions=discord.Permissions(manage_roles=True),
    )

    async def _check_roles_admin(self, interaction):
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("You need Manage Roles to change this.", ephemeral=True)
            return False
        if not interaction.guild.me.guild_permissions.manage_roles:
            await interaction.response.send_message("I need the Manage Roles permission first.", ephemeral=True)
            return False
        return True

    @levelroles.command(name="on", description="Create the level roles and give everyone their current one")
    async def level_roles_on(self, interaction: discord.Interaction):
        if not await self._check_roles_admin(interaction):
            return
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True, thinking=True)

        icons = "ROLE_ICONS" in guild.features  # Boost Level 2
        roles = self._level_roles(guild)
        created = 0
        try:
            # Legend first: each new role is created at the bottom, so creating
            # them top-down leaves Newbie lowest and Legend highest.
            for number in range(len(LEVELS), 0, -1):
                if number in roles:
                    continue  # already exists (running "on" again repairs missing ones)
                options = {
                    "name": level_role_name(number, with_emoji=not icons),
                    "permissions": discord.Permissions.none(),
                    "hoist": True,  # shown separately in the member list
                    "mentionable": False,
                    "reason": "Hearth respect level roles",
                }
                if icons:
                    options["display_icon"] = level_emoji(number)
                role = await guild.create_role(**options)
                storage.set_setting(self.db, guild.id, ROLE_ID_SETTING.format(number), role.id)
                roles[number] = role
                created += 1
        except discord.HTTPException:
            await interaction.followup.send(
                "I couldn't create the level roles. Check that I have Manage Roles and try again.", ephemeral=True
            )
            return

        storage.set_setting(self.db, guild.id, LEVEL_ROLES_SETTING, "on")

        given = failed = 0
        for user_id, points in self.store.all_points(guild.id):
            member = guild.get_member(user_id)
            if member is None:
                continue  # left the server
            if await self._set_level_role(member, level_for(points)[0], roles):
                given += 1
            else:
                failed += 1

        text = (
            f"Level roles are on. Created {created} role(s) and gave {given} member(s) their level role. "
            "They show separately in the member list, and change automatically on each level-up."
        )
        if icons:
            text += "\nEach role has its level emoji as a role icon."
        else:
            text += "\nRole icons need Boost Level 2, so the emoji is in the role name for now."
        if failed:
            text += f"\n⚠️ I couldn't update {failed} member(s). Move my role above the level roles."
        await interaction.followup.send(text, ephemeral=True)

    @levelroles.command(name="off", description="Stop giving level roles and delete them (points are kept)")
    async def level_roles_off(self, interaction: discord.Interaction):
        if not await self._check_roles_admin(interaction):
            return
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True, thinking=True)

        deleted = failed = 0
        for role in self._level_roles(guild).values():
            try:
                await role.delete(reason="Hearth level roles turned off")
                deleted += 1
            except discord.HTTPException:
                failed += 1
        for number in range(1, len(LEVELS) + 1):
            storage.delete_setting(self.db, guild.id, ROLE_ID_SETTING.format(number))
        storage.delete_setting(self.db, guild.id, LEVEL_ROLES_SETTING)

        text = f"Level roles are off. Deleted {deleted} role(s). Points and levels are kept."
        if failed:
            text += f"\n⚠️ I couldn't delete {failed} role(s) — remove them in Server Settings → Roles."
        await interaction.followup.send(text, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Respect(bot))
