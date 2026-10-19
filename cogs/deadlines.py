"""
Deadline board: /deadline add "Maths assignment" 2026-10-25 [time] [channel]

The bot posts a reminder in the deadline's channel a day before it's due:
- with a time: 24 hours before
- date only: at 9:00 the day before (the deadline counts as end of that day)

Dates and times are read in HEARTH_TIMEZONE (default Asia/Kathmandu). In
Discord, the times are shown with <t:...> timestamps, so every member sees
them in their own local time.

Saved in data/hearth.db, so deadlines and reminders survive restarts. If the
bot was offline when a reminder was due, it's sent as soon as the bot is back,
as long as the deadline itself hasn't passed.
"""

import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands, tasks

import storage

MAX_TITLE = 100
MAX_UPCOMING_PER_SERVER = 50
KEEP_PAST_DAYS = 7  # finished deadlines are cleaned up after a week


def server_timezone():
    try:
        return ZoneInfo(os.getenv("HEARTH_TIMEZONE", "Asia/Kathmandu"))
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Kathmandu")


def parse_deadline(date_text, time_text=None, tz=None):
    """Turn "2026-10-25" (+ optional "17:00") into (due_at, remind_at) UTC timestamps.

    Raises ValueError with a message meant for the user if the input is wrong.
    """
    tz = tz or server_timezone()
    try:
        day = datetime.strptime(date_text.strip(), "%Y-%m-%d")
    except ValueError:
        raise ValueError("Write the date as YYYY-MM-DD, e.g. 2026-10-25.")

    if time_text:
        try:
            clock = datetime.strptime(time_text.strip(), "%H:%M")
        except ValueError:
            raise ValueError("Write the time as HH:MM in 24-hour form, e.g. 17:00.")
        due = day.replace(hour=clock.hour, minute=clock.minute, tzinfo=tz)
        remind = due - timedelta(hours=24)
    else:
        due = day.replace(hour=23, minute=59, tzinfo=tz)
        remind = (day - timedelta(days=1)).replace(hour=9, minute=0, tzinfo=tz)

    return due.timestamp(), remind.timestamp()


class Deadlines(commands.Cog):
    def __init__(self, bot, db=None):
        self.bot = bot
        self.db = db or storage.connect()

    async def cog_load(self):
        self.reminder_loop.start()

    async def cog_unload(self):
        self.reminder_loop.cancel()

    # --- the background job ---

    @tasks.loop(minutes=1)
    async def reminder_loop(self):
        await self.send_due_reminders(time.time())

    @reminder_loop.before_loop
    async def _wait_for_login(self):
        await self.bot.wait_until_ready()

    async def send_due_reminders(self, now):
        """Post every reminder whose time has come. Returns how many were sent."""
        rows = self.db.execute(
            "SELECT * FROM deadlines WHERE reminded = 0 AND remind_at <= ?", (now,)
        ).fetchall()

        sent = 0
        for row in rows:
            # Mark it first, so a failed send can never repeat every minute.
            self.db.execute("UPDATE deadlines SET reminded = 1 WHERE id = ?", (row["id"],))
            self.db.commit()

            if row["due_at"] <= now:
                continue  # the bot was offline past the deadline itself — too late to remind

            channel = self.bot.get_channel(row["channel_id"])
            if channel is None:
                continue
            due = int(row["due_at"])
            try:
                await channel.send(
                    f"⏰ Reminder: **{row['title']}** is due <t:{due}:R> (<t:{due}:f>).",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                sent += 1
            except discord.HTTPException:
                pass

        self.db.execute("DELETE FROM deadlines WHERE due_at < ?", (now - KEEP_PAST_DAYS * 86400,))
        self.db.commit()
        return sent

    # --- commands ---

    deadline = app_commands.Group(name="deadline", description="Shared deadlines with automatic reminders", guild_only=True)

    @deadline.command(name="add", description="Add a deadline; the bot reminds this channel a day before")
    @app_commands.describe(
        title="What's due, e.g. Maths assignment",
        date="Due date as YYYY-MM-DD",
        due_time="Optional due time as HH:MM (24-hour)",
        channel="Where to post the reminder (default: this channel)",
    )
    async def add(
        self,
        interaction: discord.Interaction,
        title: app_commands.Range[str, 1, MAX_TITLE],
        date: str,
        due_time: str | None = None,
        channel: discord.TextChannel | None = None,
    ):
        try:
            due_at, remind_at = parse_deadline(date, due_time)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        now = time.time()
        if due_at <= now:
            await interaction.response.send_message("That date has already passed.", ephemeral=True)
            return

        upcoming = self.db.execute(
            "SELECT COUNT(*) FROM deadlines WHERE guild_id = ? AND due_at > ?", (interaction.guild.id, now)
        ).fetchone()[0]
        if upcoming >= MAX_UPCOMING_PER_SERVER:
            await interaction.response.send_message(
                f"This server already has {MAX_UPCOMING_PER_SERVER} upcoming deadlines — remove some first.", ephemeral=True
            )
            return

        target = channel or interaction.channel
        cursor = self.db.execute(
            "INSERT INTO deadlines (guild_id, channel_id, title, due_at, remind_at, created_by) VALUES (?, ?, ?, ?, ?, ?)",
            (interaction.guild.id, target.id, title.strip(), due_at, remind_at, interaction.user.id),
        )
        self.db.commit()

        due = int(due_at)
        note = "" if remind_at > now else " It's due within a day, so there won't be a separate reminder."
        await interaction.response.send_message(
            f"📌 Added **#{cursor.lastrowid} {title.strip()}** — due <t:{due}:F> (<t:{due}:R>). "
            f"I'll remind {target.mention} a day before.{note}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @deadline.command(name="list", description="Show upcoming deadlines in this server")
    async def list_deadlines(self, interaction: discord.Interaction):
        rows = self.db.execute(
            "SELECT id, title, due_at FROM deadlines WHERE guild_id = ? AND due_at > ? ORDER BY due_at LIMIT 15",
            (interaction.guild.id, time.time()),
        ).fetchall()
        if not rows:
            await interaction.response.send_message("No upcoming deadlines. Add one with `/deadline add`.")
            return

        lines = [f"**#{r['id']}** {r['title']} — <t:{int(r['due_at'])}:f> (<t:{int(r['due_at'])}:R>)" for r in rows]
        embed = discord.Embed(title="📅 Upcoming deadlines", description="\n".join(lines), color=discord.Color.dark_teal())
        await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @deadline.command(name="remove", description="Remove a deadline by its number (yours, or any if you can manage messages)")
    @app_commands.describe(number="The deadline's number from /deadline list")
    async def remove(self, interaction: discord.Interaction, number: int):
        row = self.db.execute(
            "SELECT id, title, created_by FROM deadlines WHERE id = ? AND guild_id = ?", (number, interaction.guild.id)
        ).fetchone()
        if row is None:
            await interaction.response.send_message(f"There's no deadline #{number} in this server.", ephemeral=True)
            return

        can_manage = interaction.user.guild_permissions.manage_messages
        if row["created_by"] != interaction.user.id and not can_manage:
            await interaction.response.send_message(
                "Only the person who added it, or someone with Manage Messages, can remove it.", ephemeral=True
            )
            return

        self.db.execute("DELETE FROM deadlines WHERE id = ?", (number,))
        self.db.commit()
        await interaction.response.send_message(
            f"Removed **#{number} {row['title']}**.", allowed_mentions=discord.AllowedMentions.none()
        )


async def setup(bot):
    await bot.add_cog(Deadlines(bot))
