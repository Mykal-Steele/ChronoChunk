"""Per-channel switches that survive a restart."""
from typing import Optional

from src.database import Database, get_database


class ChannelSettings:
    """
    What the bot may do in each channel: whether it reads the images and files
    posted just before a message, and whether it may answer a message nobody
    addressed to it. A channel that was never switched is on for both.
    """

    def __init__(self, database: Optional[Database] = None):
        self._database = database or get_database()

    def reads_recent_posts(self, channel_id) -> bool:
        rows = self._database.query(
            "SELECT read_recent_posts FROM channel_settings WHERE channel_id = ?", (str(channel_id),))
        return bool(rows[0]["read_recent_posts"]) if rows else True

    def set_reads_recent_posts(self, channel_id, enabled: bool) -> None:
        self._database.execute(
            "INSERT INTO channel_settings(channel_id, read_recent_posts) VALUES (?, ?) "
            "ON CONFLICT(channel_id) DO UPDATE SET read_recent_posts = excluded.read_recent_posts",
            (str(channel_id), int(bool(enabled))))


    def joins_in(self, channel_id) -> bool:
        rows = self._database.query("SELECT join_in FROM channel_settings WHERE channel_id = ?", (str(channel_id),))
        return bool(rows[0]["join_in"]) if rows else True

    def set_joins_in(self, channel_id, enabled: bool) -> None:
        self._database.execute(
            "INSERT INTO channel_settings(channel_id, join_in) VALUES (?, ?) "
            "ON CONFLICT(channel_id) DO UPDATE SET join_in = excluded.join_in",
            (str(channel_id), int(bool(enabled))))


_shared_settings: Optional[ChannelSettings] = None


def get_channel_settings() -> ChannelSettings:
    """The one settings store the whole bot shares."""
    global _shared_settings
    if _shared_settings is None:
        _shared_settings = ChannelSettings()
    return _shared_settings
