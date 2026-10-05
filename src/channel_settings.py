"""Per-channel switches that survive a restart, kept in one small JSON file."""
import json
import logging
import os
from typing import Optional

from config.config import Config

logger = logging.getLogger(__name__)


class ChannelSettings:
    """
    What the bot may do in each channel. Right now that is one switch: whether it
    reads the images and files posted just before a message. A channel that was
    never switched is on.
    """

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join(Config.STATE_DIR, "channel_settings.json")
        self._channels = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            logger.warning(f"Could not read {self.path}, using the defaults: {e}")
            return {}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._channels, f, indent=2)
        except OSError as e:
            logger.error(f"Could not save {self.path}: {e}")

    def reads_recent_posts(self, channel_id) -> bool:
        channel = self._channels.get(str(channel_id))
        return channel.get("read_recent_posts", True) if isinstance(channel, dict) else True

    def set_reads_recent_posts(self, channel_id, enabled: bool) -> None:
        self._channels.setdefault(str(channel_id), {})["read_recent_posts"] = bool(enabled)
        self._save()


_shared_settings: Optional[ChannelSettings] = None


def get_channel_settings() -> ChannelSettings:
    """The one settings store the whole bot shares."""
    global _shared_settings
    if _shared_settings is None:
        _shared_settings = ChannelSettings()
    return _shared_settings
