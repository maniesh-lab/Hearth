"""
Respect points: saying thanks to someone gives them +1 respect.

How a thank-you is counted:
- The message says thanks (thanks, thank you, ty, thx, ...)
- AND it's aimed at someone: they're @mentioned, or the message is a reply to them
- You can't thank yourself or a bot
- The same person can give the same person +1 only once an hour,
  so points can't be farmed by spamming "thanks"

Points add up to levels. Reaching a new level is announced in the channel.

Saved per server in data/respect.db (SQLite), so each server has its own
scores and leaderboard.
"""

import re
import sqlite3
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "respect.db"

COOLDOWN_SECONDS = 60 * 60  # same giver -> same receiver: once an hour
MAX_THANKED_PER_MESSAGE = 5  # "thanks @a @b @c ..." counts for at most 5 people

# (points needed, level name). Level 1 starts at the very first thank-you.
LEVELS = [
    (1, "Newbie"),
    (25, "Helper"),
    (50, "Contributor"),
    (100, "Regular"),
    (500, "Veteran"),
    (1000, "Legend"),
]

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
    for i, (needed, level_name) in enumerate(LEVELS, start=1):
        if points >= needed:
            number, name = i, level_name
    return number, name


def next_level(points):
    """Return (points needed, level name) for the next level, or None at the top."""
    for needed, name in LEVELS:
        if points < needed:
            return needed, name
    return None


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
            -- when each person last thanked each other person (for the cooldown)
            CREATE TABLE IF NOT EXISTS last_thanks (
                guild_id    INTEGER NOT NULL,
                giver_id    INTEGER NOT NULL,
                receiver_id INTEGER NOT NULL,
                thanked_at  REAL    NOT NULL,
                PRIMARY KEY (guild_id, giver_id, receiver_id)
            );
            """
        )
        self.db.commit()

    def points(self, guild_id, user_id):
        row = self.db.execute(
            "SELECT points FROM respect WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        return row[0] if row else 0

    def on_cooldown(self, guild_id, giver_id, receiver_id, now):
        row = self.db.execute(
            "SELECT thanked_at FROM last_thanks WHERE guild_id = ? AND giver_id = ? AND receiver_id = ?",
            (guild_id, giver_id, receiver_id),
        ).fetchone()
        return row is not None and now - row[0] < COOLDOWN_SECONDS

    def give(self, guild_id, giver_id, receiver_id, now):
        """Add 1 point and record the time. Returns the receiver's new total."""
        self.db.execute(
            """
            INSERT INTO respect (guild_id, user_id, points) VALUES (?, ?, 1)
            ON CONFLICT (guild_id, user_id) DO UPDATE SET points = points + 1
            """,
            (guild_id, receiver_id),
        )
        self.db.execute(
            """
            INSERT INTO last_thanks (guild_id, giver_id, receiver_id, thanked_at) VALUES (?, ?, ?, ?)
            ON CONFLICT (guild_id, giver_id, receiver_id) DO UPDATE SET thanked_at = excluded.thanked_at
            """,
            (guild_id, giver_id, receiver_id, now),
        )
        self.db.commit()
        return self.points(guild_id, receiver_id)

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
    def __init__(self, bot, store=None):
        self.bot = bot
        self.store = store or RespectStore()

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

        now = time.time()
        guild_id = message.guild.id

        for person in await self._thanked_people(message):
            if self.store.on_cooldown(guild_id, message.author.id, person.id, now):
                continue

            before, _ = level_for(self.store.points(guild_id, person.id))
            total = self.store.give(guild_id, message.author.id, person.id, now)
            after, name = level_for(total)

            if after > before:
                try:
                    await message.channel.send(
                        f"🎉 {person.mention} reached **Level {after} — {name}** "
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
            text = f"**{member.display_name}** — Level {number} ({name}) · {points} respect · #{rank} in this server"
            upcoming = next_level(points)
            if upcoming:
                needed, next_name = upcoming
                text += f"\n{needed - points} more to reach {next_name}."
            else:
                text += "\nTop level reached."

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
            _, name = level_for(points)
            lines.append(f"**{place}.** {who} — {points} ({name})")

        embed = discord.Embed(title="🏆 Respect leaderboard", description="\n".join(lines), color=discord.Color.dark_teal())
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(Respect(bot))
