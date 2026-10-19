import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from cogs import deadlines
from cogs.deadlines import Deadlines, parse_deadline
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


@pytest.mark.parametrize("date, clock", [("25-10-2030", None), ("2030-13-01", None), ("2030-10-25", "5pm")])
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


def test_add_and_list(cog):
    interaction = add(cog, make_member(1), "Maths assignment", future_date(5))
    assert "Added **#1 Maths assignment**" in reply_text(interaction)

    listing = make_interaction(make_member(1), make_guild())
    asyncio.run(cog.list_deadlines.callback(cog, listing))
    assert "Maths assignment" in listing.response.send_message.await_args.kwargs["embed"].description


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


def test_no_reminder_if_the_bot_was_offline_past_the_deadline(cog):
    add(cog, make_member(1), "Missed", "2099-06-10")
    due, _ = parse_deadline("2099-06-10", tz=KTM)
    assert asyncio.run(cog.send_due_reminders(due + 60)) == 0
    cog.channel.send.assert_not_awaited()


def test_only_creator_or_moderator_can_remove(cog):
    add(cog, make_member(1), "Quiz", future_date(3))

    stranger = make_member(2)
    stranger.guild_permissions.manage_messages = False
    interaction = make_interaction(stranger, make_guild())
    asyncio.run(cog.remove.callback(cog, interaction, 1))
    assert "Only the person who added it" in reply_text(interaction)

    creator = make_member(1)
    creator.guild_permissions.manage_messages = False
    interaction = make_interaction(creator, make_guild())
    asyncio.run(cog.remove.callback(cog, interaction, 1))
    assert "Removed **#1 Quiz**" in reply_text(interaction)


def test_server_limit(cog, monkeypatch):
    monkeypatch.setattr(deadlines, "MAX_UPCOMING_PER_SERVER", 2)
    add(cog, make_member(1), "A", future_date(2))
    add(cog, make_member(1), "B", future_date(2))
    interaction = add(cog, make_member(1), "C", future_date(2))
    assert "remove some first" in reply_text(interaction)
