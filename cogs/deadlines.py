"""
Deadline board.
- /deadlines — everyone: upcoming deadlines, soonest first, numbered #1, #2, #3…
- /deadline add "Maths assignment" 2026-10-25 [time] [channel]
- /deadline remove — pick the deadline from a dropdown (or type its number)

The numbers are positions in the list, worked out fresh each time: the soonest
deadline is always #1, and when it passes the next one becomes #1. Each server
has its own list, so every server starts at #1.

/deadline add and /deadline remove are hidden from regular members by default
(they need Manage Messages). An admin can let a role or a person use them in
Server Settings → Integrations → Hearth → /deadline, while /deadlines stays
open to everyone.

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

    # --- the list, in order ---

    def upcoming(self, guild_id, now=None):
        """This server's upcoming deadlines, soonest first. A deadline's number
        is its position here (index + 1)."""
        return self.db.execute(
            "SELECT id, title, due_at FROM deadlines WHERE guild_id = ? AND due_at > ? ORDER BY due_at, id",
            (guild_id, now if now is not None else time.time()),
        ).fetchall()

    def find(self, guild_id, choice):
        """Turn what was picked or typed in /deadline remove into a deadline row.
        "id:12" comes from the dropdown (exact, even if the list has changed);
        "3" means #3 in the list right now."""
        choice = (choice or "").strip().lstrip("#")
        rows = self.upcoming(guild_id)
        if choice.startswith("id:") and choice[3:].isdigit():
            wanted = int(choice[3:])
            for position, row in enumerate(rows, start=1):
                if row["id"] == wanted:
                    return position, row
            return None, None
        if choice.isdigit() and 1 <= int(choice) <= len(rows):
            return int(choice), rows[int(choice) - 1]
        return None, None

    # --- commands ---

    @app_commands.command(name="deadlines", description="Upcoming deadlines in this server, soonest first")
    @app_commands.guild_only()
    async def list_deadlines(self, interaction: discord.Interaction):
        rows = self.upcoming(interaction.guild.id)[:15]
        if not rows:
            await interaction.response.send_message("No upcoming deadlines.")
            return

        lines = [
            f"**#{position}** {r['title']} — <t:{int(r['due_at'])}:f> (<t:{int(r['due_at'])}:R>)"
            for position, r in enumerate(rows, start=1)
        ]
        embed = discord.Embed(title="📅 Upcoming deadlines", description="\n".join(lines), color=discord.Color.dark_teal())
        await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    deadline = app_commands.Group(
        name="deadline",
        description="Add or remove deadlines (an admin chooses who can)",
        guild_only=True,
        # Hidden from regular members until an admin allows a role or person in
        # Server Settings → Integrations → Hearth → /deadline.
        default_permissions=discord.Permissions(manage_messages=True),
    )

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
        position = next(
            (i for i, r in enumerate(self.upcoming(interaction.guild.id, now), start=1) if r["id"] == cursor.lastrowid),
            None,
        )

        due = int(due_at)
        note = "" if remind_at > now else " It's due within a day, so there won't be a separate reminder."
        await interaction.response.send_message(
            f"📌 Added **#{position} {title.strip()}** — due <t:{due}:F> (<t:{due}:R>). "
            f"I'll remind {target.mention} a day before.{note}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @deadline.command(name="remove", description="Remove a deadline — pick it from the list")
    @app_commands.describe(deadline="Start typing to pick one, or type its number from /deadlines")
    async def remove(self, interaction: discord.Interaction, deadline: str):
        position, row = self.find(interaction.guild.id, deadline)
        if row is None:
            await interaction.response.send_message(
                "I couldn't find that deadline — pick one from the list, or check `/deadlines`.", ephemeral=True
            )
            return

        self.db.execute("DELETE FROM deadlines WHERE id = ?", (row["id"],))
        self.db.commit()
        await interaction.response.send_message(
            f"Removed **#{position} {row['title']}**.", allowed_mentions=discord.AllowedMentions.none()
        )

    @remove.autocomplete("deadline")
    async def remove_choices(self, interaction: discord.Interaction, typed: str):
        """The dropdown: each upcoming deadline as "#2 Eng assignment — Mon 2 Nov".
        The value is the deadline's ID, so the right one is removed even if the
        numbers shift before the command is sent."""
        tz = server_timezone()
        typed = typed.lower().strip()
        choices = []
        for position, row in enumerate(self.upcoming(interaction.guild.id), start=1):
            day = datetime.fromtimestamp(row["due_at"], tz)
            when = f"{day:%a} {day.day} {day:%b, %H:%M}"
            title = row["title"]
            room = 100 - len(f"#{position}  — {when}")  # Discord's limit for a dropdown entry is 100
            if len(title) > room:
                title = title[: room - 1] + "…"
            label = f"#{position} {title} — {when}"
            if typed and typed not in label.lower():
                continue
            choices.append(app_commands.Choice(name=label, value=f"id:{row['id']}"))
        return choices[:25]  # Discord shows at most 25


async def setup(bot):
    await bot.add_cog(Deadlines(bot))
