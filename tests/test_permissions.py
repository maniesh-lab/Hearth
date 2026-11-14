"""
Who can use which command. Admin commands start hidden from members who lack
the matching permission (default_permissions); each server's admins can then
let any role or person use them in Server Settings → Integrations → Hearth.
Only /kick and /ban also check the permission in the code, so that a slip in
Server Settings can't let someone kick or ban without the real permission.
"""

from unittest.mock import MagicMock

import discord
import pytest
from discord import app_commands

import storage
from cogs import faq, roles
from cogs.deadlines import Deadlines
from cogs.faq import FAQ
from cogs.moderation import Moderation
from cogs.onboarding import Onboarding
from cogs.respect import Respect, RespectStore
from cogs.roles import Roles
from cogs.utility import Utility
from main import bot

ADMIN_DEFAULTS = {
    "welcome": "manage_guild",
    "modlog": "manage_guild",
    "faqload": "manage_guild",
    "roleadd": "manage_roles",
    "roleremove": "manage_roles",
    "rolesetup": "manage_roles",
    "levelroles": "manage_roles",
    "deadline": "manage_messages",
    "mute": "moderate_members",
    "warn": "moderate_members",
    "warnings": "moderate_members",
    "clearwarnings": "moderate_members",
    "kick": "kick_members",
    "ban": "ban_members",
}
OPEN_TO_EVERYONE = {
    "ask", "deadlines", "respect", "leaderboard", "role", "rolelist", "serverinfo", "poll", "remind",
}
LOCKED_IN_CODE = {"kick": "kick_members", "ban": "ban_members"}


@pytest.fixture
def commands(tmp_path, monkeypatch):
    monkeypatch.setattr(roles, "CONFIG_PATH", tmp_path / "roles.json")
    monkeypatch.setattr(roles, "LEGACY_IDS_PATH", tmp_path / "old.json")
    monkeypatch.setattr(faq, "DATA_DIR", tmp_path / "faq")
    db = storage.connect(tmp_path / "hearth.db")
    cogs = [
        Onboarding(MagicMock(), db),
        Roles(MagicMock()),
        Moderation(MagicMock(), db),
        Utility(MagicMock()),
        FAQ(MagicMock(), embedder=object(), groq=object()),
        Respect(MagicMock(), RespectStore(tmp_path / "respect.db"), db),
        Deadlines(MagicMock(), db),
    ]
    found = {cmd.name: cmd for cog in cogs for cmd in cog.get_app_commands()}
    found["ping"] = bot.tree.get_command("ping")
    return found


def test_every_command_is_accounted_for(commands):
    assert set(commands) == set(ADMIN_DEFAULTS) | OPEN_TO_EVERYONE | {"ping"}


@pytest.mark.parametrize("name, permission", sorted(ADMIN_DEFAULTS.items()))
def test_admin_commands_start_hidden_behind_one_permission(commands, name, permission):
    assert commands[name].default_permissions == discord.Permissions(**{permission: True})


@pytest.mark.parametrize("name", sorted(OPEN_TO_EVERYONE | {"ping"}))
def test_member_commands_are_open_to_everyone(commands, name):
    assert commands[name].default_permissions is None


def all_checks(command):
    """Checks on a command and, for a group, on every subcommand."""
    found = list(getattr(command, "checks", []))
    for sub in getattr(command, "commands", []):
        found += all_checks(sub)
    return found


@pytest.mark.parametrize("name", sorted(set(ADMIN_DEFAULTS) - set(LOCKED_IN_CODE)))
def test_server_settings_decide_who_can_use_the_rest(commands, name):
    # No permission check in the code, so allowing a role in Server Settings works.
    assert all_checks(commands[name]) == []


@pytest.mark.parametrize("name, permission", sorted(LOCKED_IN_CODE.items()))
def test_kick_and_ban_still_need_the_real_permission(commands, name, permission):
    (check,) = all_checks(commands[name])
    interaction = MagicMock()
    interaction.permissions = discord.Permissions(moderate_members=True)  # allowed in Server Settings, no Kick/Ban
    with pytest.raises(app_commands.MissingPermissions):
        check(interaction)
    interaction.permissions = discord.Permissions(**{permission: True})
    assert check(interaction) is True
