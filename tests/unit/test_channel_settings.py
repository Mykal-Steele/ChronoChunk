"""Unit tests for channel_settings: the per-channel switches kept in a JSON file."""
from src.channel_settings import ChannelSettings


def test_channels_read_recent_posts_until_switched_off(tmp_path):
    settings = ChannelSettings(str(tmp_path / "channels.json"))
    assert settings.reads_recent_posts(42) is True

    settings.set_reads_recent_posts(42, False)
    assert settings.reads_recent_posts(42) is False
    assert settings.reads_recent_posts("42") is False
    assert settings.reads_recent_posts(43) is True

    settings.set_reads_recent_posts(42, True)
    assert settings.reads_recent_posts(42) is True


def test_the_switch_survives_a_restart(tmp_path):
    path = str(tmp_path / "state" / "channels.json")
    ChannelSettings(path).set_reads_recent_posts(42, False)
    assert ChannelSettings(path).reads_recent_posts(42) is False


def test_a_broken_file_falls_back_to_the_defaults(tmp_path):
    path = tmp_path / "channels.json"
    path.write_text("{ not json", encoding="utf-8")
    assert ChannelSettings(str(path)).reads_recent_posts(42) is True
    path.write_text('["a list, not channels"]', encoding="utf-8")
    assert ChannelSettings(str(path)).reads_recent_posts(42) is True
