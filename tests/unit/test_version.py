"""The version in the code and the newest entry of the changelog have to say the same thing."""
import re
from pathlib import Path

from src.version import __version__

CHANGELOG = Path(__file__).resolve().parents[2] / "CHANGELOG.md"


def test_version_is_three_numbers():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def test_newest_changelog_entry_is_the_current_version():
    entries = re.findall(r"^## (\d+\.\d+\.\d+) \(\d{4}-\d{2}-\d{2}\)$", CHANGELOG.read_text(encoding="utf-8"), re.MULTILINE)
    assert entries, "no version headings found in CHANGELOG.md"
    assert entries[0] == __version__


def test_changelog_versions_go_from_newest_to_oldest():
    entries = re.findall(r"^## (\d+\.\d+\.\d+) \(", CHANGELOG.read_text(encoding="utf-8"), re.MULTILINE)
    numbers = [tuple(int(part) for part in entry.split(".")) for entry in entries]
    assert numbers == sorted(numbers, reverse=True) and len(set(numbers)) == len(numbers)
