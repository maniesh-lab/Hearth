"""
Shared test helpers. Nothing here talks to Discord, Gemini or Groq: Discord
objects are stand-ins (MagicMock), and each test gets its own temporary
database, so tests can run anywhere, including GitHub Actions.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import storage  # noqa: E402  (needs the path above)


class Rank:
    """A stand-in for a Discord role position that supports >= like real roles."""

    def __init__(self, position):
        self.position = position

    def __ge__(self, other):
        return self.position >= other.position


@pytest.fixture
def db(tmp_path):
    return storage.connect(tmp_path / "hearth.db")


def make_member(user_id, rank=1, bot=False, name=None):
    member = MagicMock()
    member.id = user_id
    member.bot = bot
    member.top_role = Rank(rank)
    member.mention = f"<@{user_id}>"
    member.display_name = name or f"user{user_id}"
    member.__str__ = lambda self: self.display_name
    member.send = AsyncMock()
    member.add_roles = AsyncMock()
    member.kick = AsyncMock()
    member.ban = AsyncMock()
    member.timeout = AsyncMock()
    return member


def make_guild(guild_id=1, owner_id=999, bot_rank=10):
    guild = MagicMock()
    guild.id = guild_id
    guild.name = "College"
    guild.owner_id = owner_id
    guild.member_count = 42
    guild.me = make_member(555, rank=bot_rank, bot=True)
    return guild


def make_interaction(user, guild):
    interaction = MagicMock()
    interaction.user = user
    interaction.guild = guild
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def reply_text(interaction):
    """The text the bot replied with (first positional argument)."""
    call = interaction.response.send_message.await_args or interaction.followup.send.await_args
    return call.args[0] if call.args else ""
