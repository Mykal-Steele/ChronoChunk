"""Unit tests for channel_settings: the per-channel switches kept in the database."""
from src.channel_settings import ChannelSettings


def test_channels_read_recent_posts_until_switched_off(database):
    settings = ChannelSettings(database)
    assert settings.reads_recent_posts(42) is True

    settings.set_reads_recent_posts(42, False)
    assert settings.reads_recent_posts(42) is False
    assert settings.reads_recent_posts("42") is False
    assert settings.reads_recent_posts(43) is True

    settings.set_reads_recent_posts(42, True)
    assert settings.reads_recent_posts(42) is True


def test_the_switch_survives_a_restart(database):
    ChannelSettings(database).set_reads_recent_posts(42, False)
    assert ChannelSettings(database).reads_recent_posts(42) is False


def test_channels_let_the_bot_join_in_until_switched_off(database):
    settings = ChannelSettings(database)
    assert settings.joins_in(42) is True
    settings.set_joins_in(42, False)
    assert settings.joins_in(42) is False
    assert settings.joins_in(43) is True
    assert settings.reads_recent_posts(42) is True  # the other switch is untouched
    settings.set_reads_recent_posts(42, False)
    assert settings.joins_in(42) is False
