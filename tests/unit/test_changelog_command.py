"""/changelog: what changed in each version, newest first, a few versions per page with buttons to flip."""
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from src import changelog
from src.version import __version__

SAMPLE = """# Changelog

Intro text that is not a version.

## 3.2.0 (2026-11-19 15:24)

### Added

- `/memory` shows what the bot remembers. It has codes to delete one thing, and a server view for managers.
- Notes can be private.

### Fixed

- Help no longer times out. The VM kept the bot in swap.

## 3.1.0 (2026-11-18)

### Changed

- The bot runs on Python 3.15 (was 3.12). discord.py moved up with it.

## 3.0.0 (2026-11-01)

### For whoever runs the bot

- Set AI_PROVIDER to vertex. """ + "Very long explanation. " * 30 + """
"""


@pytest.fixture
def sample(tmp_path, monkeypatch):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(SAMPLE)
    monkeypatch.setattr(changelog, "CHANGELOG_PATH", str(path))
    return path


def test_versions_are_read_newest_first_with_their_changes(sample):
    versions = changelog.read_versions()
    assert [v.number for v in versions] == ["3.2.0", "3.1.0", "3.0.0"]
    assert versions[0].day.isoformat() == "2026-11-19"
    assert [kind for kind, _ in versions[0].changes] == ["added", "added", "fixed"]


def test_a_change_is_cut_to_its_first_sentence_on_a_page(sample):
    page = changelog.pages(changelog.read_versions())[0]
    assert "`/memory` shows what the bot remembers." in page
    assert "It has codes" not in page
    # A version number like 3.15 is not the end of a sentence
    assert "The bot runs on Python 3.15 (was 3.12)." in page


def test_a_very_long_first_sentence_is_shortened():
    assert len(changelog.short("word " * 200)) <= changelog.SHORT_CHARS


def test_each_change_says_what_kind_it_is(sample):
    page = changelog.pages(changelog.read_versions())[0]
    assert "**new** Notes can be private." in page
    assert "**fixed** Help no longer times out." in page
    assert "**changed** The bot runs on Python 3.15" in page
    assert "**for hosts** Set AI_PROVIDER to vertex." in page


def test_each_version_says_when_it_was_made(sample, monkeypatch):
    # The user asked for when each update was made, not only the day (2026-11-19)
    from datetime import datetime
    from src.clock import BANGKOK
    monkeypatch.setattr(changelog, "_now", lambda: datetime(2026, 11, 21, 9, 0, tzinfo=BANGKOK))
    page = changelog.pages(changelog.read_versions())[0]
    assert "**3.2.0** · 19 Nov 2026, 3:24 pm (2 days ago)" in page
    # An old entry with only a day still shows the day
    assert "**3.1.0** · 18 Nov 2026 (3 days ago)" in page


def test_the_time_is_read_as_bangkok_time(sample):
    from src.clock import BANGKOK
    made = changelog.read_versions()[0].made
    assert (made.hour, made.minute, made.utcoffset()) == (15, 24, BANGKOK.utcoffset(None))


def test_every_released_version_in_the_real_changelog_has_a_time():
    versions = changelog.read_versions()
    untimed = [v.number for v in versions if v.made is None]
    assert untimed == []


def test_the_first_page_holds_the_newest_five_versions():
    versions = changelog.read_versions()
    first = changelog.pages(versions)[0]
    assert changelog.PER_PAGE == 5
    for version in versions[:5]:
        assert f"**{version.number}**" in first
    assert f"**{versions[5].number}**" not in first


def test_the_real_changelog_starts_with_the_running_version():
    assert changelog.read_versions()[0].number == __version__


def test_every_real_page_fits_an_embed_and_every_version_is_on_one():
    versions = changelog.read_versions()
    pages = changelog.pages(versions)
    assert all(len(page) <= changelog.PAGE_CHARS for page in pages)
    shown = "\n".join(pages)
    assert all(f"**{v.number}**" in shown for v in versions)


def test_a_page_with_huge_versions_gets_fewer_of_them(sample, monkeypatch):
    monkeypatch.setattr(changelog, "PAGE_CHARS", 300)
    pages = changelog.pages(changelog.read_versions())
    assert len(pages) > 1 and all(len(page) <= 300 for page in pages)


def test_one_version_in_full():
    text = changelog.full_version("3.27.0")
    assert text.startswith("**3.27.0** · 19 Nov 2026, ")
    # The whole change, not just its first sentence
    assert "Discord edits and deletes carry over, and DMs are not saved." in text
    assert len(text) <= changelog.PAGE_CHARS


def test_an_unknown_version_says_which_ones_exist():
    text = changelog.full_version("9.9.9")
    assert "9.9.9" in text and __version__ in text


def test_a_missing_changelog_file_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(changelog, "CHANGELOG_PATH", str(tmp_path / "nope.md"))
    assert changelog.read_versions() == []
    assert changelog.pages([]) == ["i couldnt find my changelog"]


# ── the buttons ──────────────────────────────────────────────────────────────

async def test_the_buttons_flip_between_pages():
    view = changelog.ChangelogView(["page one", "page two", "page three"])
    assert view.newer.disabled and not view.older.disabled
    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()
    await view.older.callback(interaction)
    assert view.page == 1
    embed = interaction.response.edit_message.call_args[1]["embed"]
    assert embed.description == "page two" and "page 2 of 3" in embed.footer.text
    await view.older.callback(interaction)
    assert view.older.disabled and not view.newer.disabled
    await view.newer.callback(interaction)
    assert view.page == 1


def test_a_single_page_has_no_buttons_to_press():
    view = changelog.ChangelogView(["only page"])
    assert view.newer.disabled and view.older.disabled


def test_the_embed_links_to_the_full_changelog_and_its_footer_has_no_backticks():
    embed = changelog.ChangelogView(["a", "b"]).embed()
    assert embed.url and embed.url.endswith("CHANGELOG.md")
    assert "`" not in embed.footer.text


async def test_the_buttons_stop_working_after_a_while():
    view = changelog.ChangelogView(["a", "b"])
    view.message = MagicMock()
    view.message.edit = AsyncMock()
    await view.on_timeout()
    assert view.newer.disabled and view.older.disabled
    view.message.edit.assert_awaited()


# ── the command ──────────────────────────────────────────────────────────────

@pytest.fixture
async def changelog_command(mock_bot_user):
    from src.slash_commands import SlashCommandManager
    registered = {}

    def command(**kwargs):
        def register(func):
            registered[kwargs["name"]] = func
            return func
        return register

    bot = MagicMock()
    bot.user = mock_bot_user
    bot.tree.command = command
    manager = SlashCommandManager(bot=bot, game_manager=MagicMock(), user_data_manager=MagicMock(),
                                  ai_handler=MagicMock(), message_handler=MagicMock(), music_manager=MagicMock())
    await manager._register_changelog_command()
    return registered["changelog"]


def _interaction():
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.original_response = AsyncMock(return_value=MagicMock())
    return interaction


async def test_slash_changelog_shows_the_newest_page_with_buttons(changelog_command):
    interaction = _interaction()
    await changelog_command(interaction)
    kwargs = interaction.response.send_message.call_args[1]
    assert f"**{__version__}**" in kwargs["embed"].description
    assert isinstance(kwargs["view"], changelog.ChangelogView)


async def test_slash_changelog_with_a_version_shows_it_in_full(changelog_command):
    interaction = _interaction()
    await changelog_command(interaction, "3.27.0")
    kwargs = interaction.response.send_message.call_args[1]
    assert kwargs["embed"].description.startswith("**3.27.0**")
    assert "view" not in kwargs


def test_the_help_lists_the_changelog_command():
    from src.help_menu import build_help_embed
    text = "\n".join(f.value for f in build_help_embed().fields)
    assert re.search(r"`/changelog[` ]", text)
