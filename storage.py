"""
One small SQLite database (data/hearth.db) shared by the features that need
to remember things: deadlines, warnings, and per-server settings like the
welcome channel or the mod-log channel.

Each cog opens its own connection with connect(); SQLite handles several
connections to the same file fine for a bot this size.
"""

import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "hearth.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id INTEGER NOT NULL,
    key      TEXT    NOT NULL,
    value    TEXT    NOT NULL,
    PRIMARY KEY (guild_id, key)
);

CREATE TABLE IF NOT EXISTS warnings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    reason       TEXT    NOT NULL,
    created_at   REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS deadlines (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    title      TEXT    NOT NULL,
    due_at     REAL    NOT NULL,   -- UTC timestamp
    remind_at  REAL    NOT NULL,   -- UTC timestamp
    created_by INTEGER NOT NULL,
    reminded   INTEGER NOT NULL DEFAULT 0
);
"""


def connect(path=DB_PATH):
    """Open the database, creating the file and tables the first time."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row  # rows behave like dicts: row["title"]
    db.executescript(SCHEMA)
    db.commit()
    return db


def get_setting(db, guild_id, key, default=None):
    row = db.execute(
        "SELECT value FROM guild_settings WHERE guild_id = ? AND key = ?", (guild_id, key)
    ).fetchone()
    return row["value"] if row else default


def set_setting(db, guild_id, key, value):
    db.execute(
        """
        INSERT INTO guild_settings (guild_id, key, value) VALUES (?, ?, ?)
        ON CONFLICT (guild_id, key) DO UPDATE SET value = excluded.value
        """,
        (guild_id, key, str(value)),
    )
    db.commit()


def delete_setting(db, guild_id, key):
    db.execute("DELETE FROM guild_settings WHERE guild_id = ? AND key = ?", (guild_id, key))
    db.commit()
