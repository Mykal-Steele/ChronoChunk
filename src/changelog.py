"""
What /changelog shows: CHANGELOG.md as pages of versions, newest first, with buttons to
flip through them. A page holds the newest PER_PAGE versions, each change cut to its
first sentence so a page stays easy to scan. /changelog version:<number> shows one in full.
"""
import os
import re
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Tuple

import discord

from src.version import __version__

CHANGELOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "CHANGELOG.md")
CHANGELOG_URL = "https://github.com/Mykal-Steele/ChronoChunk/blob/main/CHANGELOG.md"

# Five versions with their changes in one line each come to about 2,500 characters: one
# screen on a laptop, and well under an embed's 4,096. A page with very long versions
# holds fewer, so it never goes over PAGE_CHARS.
PER_PAGE = 5
PAGE_CHARS = 3900
# How long one change may be on a page before it is cut
SHORT_CHARS = 200
# How long the buttons keep working, in seconds
BUTTON_SECONDS = 600

# What each "### " heading is called on a page
KINDS = {
    "added": "new",
    "changed": "changed",
    "fixed": "fixed",
    "removed": "removed",
    "for whoever runs the bot": "for hosts",
    "upgrading": "for hosts",
    "changed, and breaking for whoever runs the bot": "breaking",
}


@dataclass
class Version:
    number: str
    day: date
    # (heading, the change) in the order they are written
    changes: List[Tuple[str, str]] = field(default_factory=list)


def read_versions() -> List[Version]:
    """Every dated version in CHANGELOG.md, newest first. Empty when the file is missing."""
    try:
        with open(CHANGELOG_PATH, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []
    versions = []
    for section in re.split(r"^## ", text, flags=re.MULTILINE)[1:]:
        title, _, body = section.partition("\n")
        found = re.match(r"(\S+) \((\d{4}-\d{2}-\d{2})\)", title.strip())
        if not found:
            continue
        version = Version(found.group(1), date.fromisoformat(found.group(2)))
        kind = ""
        for line in body.splitlines():
            if line.startswith("### "):
                kind = line[4:].strip().lower()
            elif line.startswith("- "):
                version.changes.append((kind, line[2:].strip()))
        versions.append(version)
    return versions


def short(change: str) -> str:
    """The first sentence of a change, cut at a word if it is still too long."""
    # A sentence ends at ". " and friends, so "3.15 (was 3.12)." is not cut in the middle
    first = re.split(r"(?<=[.!?])\s+(?=[A-Z`\"(])", change, maxsplit=1)[0].strip()
    if len(first) <= SHORT_CHARS:
        return first
    cut = first[:SHORT_CHARS - 3].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "..."


def _kind(heading: str) -> str:
    return KINDS.get(heading, heading or "change")


def _heading(version: Version) -> str:
    return f"**{version.number}** · {version.day.day} {version.day:%b %Y}"


def _block(version: Version) -> str:
    lines = [_heading(version)]
    lines += [f"- **{_kind(kind)}** {short(change)}" for kind, change in version.changes]
    return "\n".join(lines)


def pages(versions: List[Version]) -> List[str]:
    """The versions as pages of text, PER_PAGE to a page unless that would pass PAGE_CHARS."""
    if not versions:
        return ["i couldnt find my changelog"]
    result: List[str] = []
    current: List[str] = []
    for version in versions:
        block = _block(version)
        if len(block) > PAGE_CHARS:
            block = block[:PAGE_CHARS - 3].rsplit("\n", 1)[0] + "\n..."
        joined = "\n\n".join(current + [block])
        if current and (len(current) >= PER_PAGE or len(joined) > PAGE_CHARS):
            result.append("\n\n".join(current))
            current = []
        current.append(block)
    result.append("\n\n".join(current))
    return result


def full_version(number: str) -> str:
    """One version with every change in full, or which versions there are when it is unknown."""
    versions = read_versions()
    for version in versions:
        if version.number == number.strip().lstrip("v"):
            lines = [_heading(version)]
            lines += [f"- **{_kind(kind)}** {change}" for kind, change in version.changes]
            text = "\n".join(lines)
            if len(text) > PAGE_CHARS:
                ending = f"\n...the rest is at <{CHANGELOG_URL}>"
                text = text[:PAGE_CHARS - len(ending)].rsplit("\n", 1)[0] + ending
            return text
    newest = ", ".join(v.number for v in versions[:5]) or __version__
    return f"theres no version {number} in my changelog. the newest are {newest}"


def embed_for(text: str, footer: str = "") -> discord.Embed:
    embed = discord.Embed(title="what changed", url=CHANGELOG_URL, description=text, color=0x9B59B6)
    # A footer shows no formatting, so no backticks in it
    embed.set_footer(text=footer or f"ChronoChunk v{__version__}")
    return embed


class ChangelogView(discord.ui.View):
    """Newer and older buttons under the changelog. Anyone in the channel can flip the pages."""

    def __init__(self, page_texts: List[str]):
        super().__init__(timeout=BUTTON_SECONDS)
        self.page_texts = page_texts
        self.page = 0
        self.message: Optional[discord.Message] = None
        self.newer = discord.ui.Button(label="◀ newer", style=discord.ButtonStyle.secondary)
        self.older = discord.ui.Button(label="older ▶", style=discord.ButtonStyle.secondary)
        self.newer.callback = self._newer
        self.older.callback = self._older
        self.add_item(self.newer)
        self.add_item(self.older)
        self._update()

    def _update(self) -> None:
        self.newer.disabled = self.page == 0
        self.older.disabled = self.page >= len(self.page_texts) - 1

    def embed(self) -> discord.Embed:
        footer = (f"page {self.page + 1} of {len(self.page_texts)} · newest first · "
                  f"/changelog version:<number> shows one in full")
        return embed_for(self.page_texts[self.page], footer)

    async def _flip(self, interaction: discord.Interaction, step: int) -> None:
        self.page = max(0, min(len(self.page_texts) - 1, self.page + step))
        self._update()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _newer(self, interaction: discord.Interaction) -> None:
        await self._flip(interaction, -1)

    async def _older(self, interaction: discord.Interaction) -> None:
        await self._flip(interaction, 1)

    async def on_timeout(self) -> None:
        self.newer.disabled = True
        self.older.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
