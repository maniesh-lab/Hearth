import asyncio
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

import storage
from cogs import deadlines
from cogs.deadlines import Deadlines, parse_deadline, parse_time
from conftest import make_guild, make_interaction, make_member, reply_text

KTM = ZoneInfo("Asia/Kathmandu")


def ktm(*args):
    return datetime(*args, tzinfo=KTM).timestamp()


def test_date_only_is_due_end_of_day_reminded_9am_day_before():
    due, remind = parse_deadline("2030-10-25", tz=KTM)
    assert due == ktm(2030, 10, 25, 23, 59)
    assert remind == ktm(2030, 10, 24, 9, 0)


def test_with_time_reminds_24h_before():
    due, remind = parse_deadline("2030-10-25", "17:30", tz=KTM)
    assert due == ktm(2030, 10, 25, 17, 30)
    assert remind == due - 24 * 3600


@pytest.mark.parametrize(
    "text, expected",
    [
        ("17:00", (17, 0)),
        ("09:05", (9, 5)),
        ("5:00 PM", (17, 0)),
        ("5pm", (17, 0)),
        ("5:30pm", (17, 30)),
        ("5.30 p.m.", (17, 30)),
        ("9 AM", (9, 0)),
        ("12pm", (12, 0)),
        ("12:15 am", (0, 15)),
    ],
)
def test_time_accepts_12_and_24_hour(text, expected):
    assert parse_time(text) == expected


def test_pm_time_sets_the_due_hour():
    due, _ = parse_deadline("2030-10-25", "5:00 PM", tz=KTM)
    assert due == ktm(2030, 10, 25, 17, 0)


@pytest.mark.parametrize(
    "date, clock",
    [("25-10-2030", None), ("2030-13-01", None), ("2030-10-25", "13pm"), ("2030-10-25", "0am"),
     ("2030-10-25", "25:00"), ("2030-10-25", "5:75pm"), ("2030-10-25", "evening")],
)
def test_bad_input_gives_a_readable_error(date, clock):
    with pytest.raises(ValueError):
        parse_deadline(date, clock, tz=KTM)


@pytest.fixture
def cog(db, monkeypatch):
    monkeypatch.setenv("HEARTH_TIMEZONE", "Asia/Kathmandu")
    bot = MagicMock()
    channel = MagicMock()
    channel.id = 50
    channel.mention = "#assignments"
    channel.send = AsyncMock()
    bot.get_channel = lambda cid: channel if cid == 50 else None
    cog = Deadlines(bot, db)
    cog.channel = channel
    return cog


def add(cog, user, title, date, clock=None, guild=None):
    interaction = make_interaction(user, guild or make_guild())
    interaction.channel = cog.channel
    asyncio.run(cog.add.callback(cog, interaction, title, date, clock, None))
    return interaction


def future_date(days):
    return (datetime.now(KTM) + timedelta(days=days)).strftime("%Y-%m-%d")


def listing_text(cog, guild=None):
    interaction = make_interaction(make_member(1), guild or make_guild())
    asyncio.run(cog.list_deadlines.callback(cog, interaction))
    call = interaction.response.send_message.await_args
    return call.kwargs["embed"].description if "embed" in call.kwargs else call.args[0]


def test_numbers_are_positions_soonest_first(cog):
    add(cog, make_member(1), "Later essay", future_date(10))
    reply = reply_text(add(cog, make_member(1), "Sooner quiz", future_date(3)))
    assert "Added **#1 Sooner quiz**" in reply  # added second, but it's due first

    lines = listing_text(cog).split("\n")
    assert lines[0].startswith("**#1** Sooner quiz")
    assert lines[1].startswith("**#2** Later essay")


def test_when_number_1_passes_number_2_moves_up(cog):
    add(cog, make_member(1), "Quiz", "2099-06-10")
    add(cog, make_member(1), "Essay", "2099-06-20")
    quiz_due, _ = parse_deadline("2099-06-10", tz=KTM)
    rows = cog.upcoming(1, now=quiz_due + 60)
    assert [r["title"] for r in rows] == ["Essay"]  # Essay is #1 now


def test_each_server_starts_at_1(cog):
    add(cog, make_member(1), "Class quiz", future_date(3), guild=make_guild(guild_id=1))
    reply = reply_text(add(cog, make_member(1), "Club meetup", future_date(5), guild=make_guild(guild_id=2)))
    assert "Added **#1 Club meetup**" in reply
    assert "Class quiz" not in listing_text(cog, make_guild(guild_id=2))


def test_empty_list(cog):
    assert listing_text(cog) == "No upcoming deadlines."


def test_past_date_is_refused(cog):
    interaction = add(cog, make_member(1), "Old", "2020-01-01")
    assert "already passed" in reply_text(interaction)


def test_reminder_sent_once_when_its_time_comes(cog):
    add(cog, make_member(1), "Lab report", "2099-06-10", "17:00")
    due, remind = parse_deadline("2099-06-10", "17:00", tz=KTM)

    assert asyncio.run(cog.send_due_reminders(remind - 60)) == 0   # too early
    assert asyncio.run(cog.send_due_reminders(remind + 60)) == 1   # reminder time
    assert "Lab report" in cog.channel.send.await_args.args[0]
    assert asyncio.run(cog.send_due_reminders(remind + 120)) == 0  # never twice


def test_add_is_refused_where_the_bot_cant_post(cog):
    cog.channel.permissions_for = MagicMock(return_value=MagicMock(view_channel=True, send_messages=False))
    interaction = add(cog, make_member(1), "Private", "2099-06-10")
    assert "can't post in #assignments" in reply_text(interaction)
    assert cog.upcoming(1) == []


def test_reminder_reaches_a_channel_missing_from_the_cache(cog):
    add(cog, make_member(1), "Lab report", "2099-06-10", "17:00")
    _, remind = parse_deadline("2099-06-10", "17:00", tz=KTM)
    cog.bot.get_channel = lambda cid: None
    cog.bot.fetch_channel = AsyncMock(return_value=cog.channel)
    assert asyncio.run(cog.send_due_reminders(remind + 60)) == 1


def test_no_reminder_if_the_bot_was_offline_past_the_deadline(cog):
    add(cog, make_member(1), "Missed", "2099-06-10")
    due, _ = parse_deadline("2099-06-10", tz=KTM)
    assert asyncio.run(cog.send_due_reminders(due + 60)) == 0
    cog.channel.send.assert_not_awaited()


def choices(cog, typed=""):
    interaction = make_interaction(make_member(1), make_guild())
    return asyncio.run(cog.remove_choices(interaction, typed))


def remove(cog, value):
    interaction = make_interaction(make_member(1), make_guild())
    asyncio.run(cog.remove.callback(cog, interaction, value))
    return reply_text(interaction)


def test_dropdown_shows_number_title_and_date(cog):
    add(cog, make_member(1), "Eng assignment", "2099-11-02")
    add(cog, make_member(1), "Maths quiz", "2099-11-06", "10:00")
    names = [c.name for c in choices(cog)]
    assert names == ["#1 Eng assignment — Mon 2 Nov, 23:59", "#2 Maths quiz — Fri 6 Nov, 10:00"]
    assert [c.name for c in choices(cog, "maths")] == ["#2 Maths quiz — Fri 6 Nov, 10:00"]


def test_dropdown_removes_the_picked_one_even_if_numbers_shift(cog):
    add(cog, make_member(1), "Essay", "2099-11-20")
    picked = choices(cog)[0].value  # Essay, shown as #1

    add(cog, make_member(1), "Quiz", "2099-11-10")  # now Quiz is #1 and Essay is #2
    assert remove(cog, picked) == "Removed **#2 Essay**."
    assert [r["title"] for r in cog.upcoming(1)] == ["Quiz"]


def test_typed_number_means_that_position_now(cog):
    add(cog, make_member(1), "Essay", "2099-11-20")
    add(cog, make_member(1), "Quiz", "2099-11-10")
    assert remove(cog, "#2") == "Removed **#2 Essay**."


@pytest.mark.parametrize("value", ["7", "id:999", "hello", ""])
def test_unknown_deadline(cog, value):
    add(cog, make_member(1), "Quiz", "2099-11-10")
    assert "couldn't find that deadline" in remove(cog, value)


def test_a_deadline_from_another_server_cant_be_removed(cog):
    add(cog, make_member(1), "Club meetup", "2099-11-10", guild=make_guild(guild_id=2))
    club_id = cog.upcoming(2)[0]["id"]
    assert "couldn't find" in remove(cog, f"id:{club_id}")  # remove() runs in server 1


def test_who_can_add_and_remove_is_up_to_the_admin(cog):
    # /deadline add|remove start hidden behind Manage Messages; the admin can
    # allow roles in Server Settings. /deadlines has no such limit.
    assert cog.deadline.default_permissions.manage_messages is True
    assert cog.list_deadlines.default_permissions is None


def test_server_limit(cog, monkeypatch):
    monkeypatch.setattr(deadlines, "MAX_UPCOMING_PER_SERVER", 2)
    add(cog, make_member(1), "A", future_date(2))
    add(cog, make_member(1), "B", future_date(2))
    interaction = add(cog, make_member(1), "C", future_date(2))
    assert "remove some first" in reply_text(interaction)


def make_role(role_id=77, mentionable=True, everyone=False):
    role = MagicMock()
    role.id = role_id
    role.mention = "@everyone" if everyone else f"<@&{role_id}>"
    role.mentionable = mentionable
    role.is_default.return_value = everyone
    return role


def reminder_for(cog, roles, title="Lab report", ping=None, user=None):
    """Add a deadline (optionally with a ping role) and fire its reminder."""
    guild = make_guild()
    guild.get_role = lambda rid: roles.get(rid)
    cog.bot.get_guild = lambda gid: guild
    interaction = make_interaction(user or make_member(1), guild)
    interaction.channel = cog.channel
    asyncio.run(cog.add.callback(cog, interaction, title, "2099-06-10", "17:00", None, ping))
    _, remind = parse_deadline("2099-06-10", "17:00", tz=KTM)
    asyncio.run(cog.send_due_reminders(remind + 60))
    return interaction


def test_reminder_pings_nobody_by_default(cog):
    reminder_for(cog, {})
    call = cog.channel.send.await_args
    assert call.args[0].startswith("⏰ Reminder")
    assert call.kwargs["allowed_mentions"].roles is False


def test_reminder_pings_the_server_default_role(cog):
    students = make_role(77)
    storage.set_setting(cog.db, 1, deadlines.PING_KEY, 77)
    reply = reply_text(reminder_for(cog, {77: students}))
    assert "and ping <@&77>" in reply
    call = cog.channel.send.await_args
    assert call.args[0].startswith("<@&77> ⏰ Reminder")
    assert call.kwargs["allowed_mentions"].roles == [students]


def test_a_deadlines_own_role_wins_over_the_default(cog):
    storage.set_setting(cog.db, 1, deadlines.PING_KEY, 77)
    section = make_role(88)
    reminder_for(cog, {77: make_role(77), 88: section}, ping=section)
    assert cog.channel.send.await_args.args[0].startswith("<@&88>")


def test_everyone_ping_allows_only_everyone(cog):
    everyone = make_role(1, everyone=True)
    storage.set_setting(cog.db, 1, deadlines.PING_KEY, 1)
    reminder_for(cog, {1: everyone})
    call = cog.channel.send.await_args
    assert call.args[0].startswith("@everyone")
    assert call.kwargs["allowed_mentions"].everyone is True


def test_a_deleted_role_just_means_no_ping(cog):
    storage.set_setting(cog.db, 1, deadlines.PING_KEY, 77)
    reminder_for(cog, {})
    assert cog.channel.send.await_args.args[0].startswith("⏰ Reminder")


def test_cant_pick_a_role_you_couldnt_ping_yourself(cog):
    cog.channel.permissions_for = MagicMock(return_value=MagicMock(mention_everyone=False))
    interaction = reminder_for(cog, {}, ping=make_role(77, mentionable=False))
    assert "You can't ping" in reply_text(interaction)
    assert cog.upcoming(1) == []


def set_ping(cog, role, manage_guild=True, bot_can_mention=True):
    user = make_member(1)
    user.guild_permissions.manage_guild = manage_guild
    guild = make_guild()
    guild.me.guild_permissions.mention_everyone = bot_can_mention
    interaction = make_interaction(user, guild)
    asyncio.run(cog.set_ping.callback(cog, interaction, role))
    return reply_text(interaction)


def test_setting_the_ping_role_needs_manage_server(cog):
    assert "Manage Server" in set_ping(cog, make_role(77), manage_guild=False)
    assert storage.get_setting(cog.db, 1, deadlines.PING_KEY) is None


def test_set_and_clear_the_ping_role(cog):
    assert set_ping(cog, make_role(77)) == "Reminders will ping <@&77>."
    assert storage.get_setting(cog.db, 1, deadlines.PING_KEY) == "77"
    assert "won't ping anyone" in set_ping(cog, None)
    assert storage.get_setting(cog.db, 1, deadlines.PING_KEY) is None


def test_warns_when_the_ping_wouldnt_notify(cog):
    assert "won't notify anyone" in set_ping(cog, make_role(77, mentionable=False), bot_can_mention=False)
    assert "won't notify anyone" not in set_ping(cog, make_role(77, mentionable=True), bot_can_mention=False)


def test_old_databases_get_the_ping_column(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE deadlines (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,"
        " title TEXT NOT NULL, due_at REAL NOT NULL, remind_at REAL NOT NULL, created_by INTEGER NOT NULL,"
        " reminded INTEGER NOT NULL DEFAULT 0)"
    )
    old.close()
    columns = {r["name"] for r in storage.connect(path).execute("PRAGMA table_info(deadlines)")}
    assert "ping_role_id" in columns
