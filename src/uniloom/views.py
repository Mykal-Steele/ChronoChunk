"""
The buttons under a reply that used Uniloom (docs/uniloom-plan.md, sections 1 and 6). Only
the person whose account the reply used can press them: someone else in the channel gets
a note that only they can see.
"""
import logging
from typing import Any, Awaitable, Callable, List, Optional

import discord

from src.uniloom.oauth import LOGIN_SECONDS
from src.uniloom.state import PendingWrite

logger = logging.getLogger(__name__)

# How long the buttons work. A sign-in link works as long, so the two run out together
BUTTON_SECONDS = LOGIN_SECONDS
# The confirm message, with room under Discord's 2000 characters
QUESTION_CHARS = 1900
CANCELLED = "\n-# cancelled, nothing changed"


def _cut(text: str, room: int) -> str:
    return text if len(text) <= room else text[:max(1, room - 3)].rstrip() + "..."


class _OwnedView(discord.ui.View):
    """Buttons for one person, switched off after BUTTON_SECONDS."""

    def __init__(self, owner_id: str):
        super().__init__(timeout=BUTTON_SECONDS)
        self.owner_id = str(owner_id)
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) == self.owner_id:
            return True
        await interaction.response.send_message(
            "those buttons are for whoever asked. ask me urself and u get ur own", ephemeral=True)
        return False

    def _switch_off(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    async def on_timeout(self) -> None:
        self._switch_off()
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class _LinkView(discord.ui.View):
    def __init__(self, url: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Sign in to Uniloom", style=discord.ButtonStyle.link, url=url))


async def send_sign_in_link(interaction: discord.Interaction, uniloom: Any,
                            on_connected: Optional[Callable[[], Awaitable[None]]] = None) -> None:
    """
    A private sign-in link for whoever pressed or typed. Once they sign in they hear who
    they are connected as, then on_connected runs (for the request that needed it).
    """
    await interaction.response.defer(ephemeral=True, thinking=True)

    async def connected(account: Any) -> None:
        shown = getattr(account, "name", "") or "ur account"
        try:
            await interaction.followup.send(f"connected to Uniloom as {shown}", ephemeral=True)
        except discord.HTTPException as e:
            logger.warning(f"Could not say a Uniloom sign-in worked: {e}")
        if on_connected is not None:
            await on_connected()

    user = interaction.user
    handle = getattr(user, "name", "") or ""
    shown = getattr(user, "display_name", "") or handle
    name = f"{shown} (@{handle})" if handle and handle != shown else shown
    try:
        url = await uniloom.start_login(str(user.id), connected, name=name)
    except Exception as e:
        logger.warning(f"Could not start a Uniloom sign-in: {e!r}")
        await interaction.followup.send("couldnt reach Uniloom's sign-in right now, try again in a bit", ephemeral=True)
        return
    minutes = LOGIN_SECONDS // 60
    await interaction.followup.send(
        f"sign in here and allow ChronoChunk. the link is only for u, works once, for {minutes} minutes",
        view=_LinkView(url), ephemeral=True)


class ConnectView(_OwnedView):
    """The Connect Uniloom button, for someone who asked before connecting."""

    def __init__(self, owner_id: str, uniloom: Any, on_connected: Callable[[], Awaitable[None]]):
        super().__init__(owner_id)
        self.uniloom = uniloom
        self.on_connected = on_connected

    @discord.ui.button(label="Connect Uniloom", style=discord.ButtonStyle.primary)
    async def connect(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await send_sign_in_link(interaction, self.uniloom, self.on_connected)


class ConfirmView(_OwnedView):
    """do it / cancel for the changes one reply queued. They run once, as the owner, when pressed."""

    def __init__(self, owner_id: str, writes: List[PendingWrite],
                 run: Callable[[discord.Interaction, List[PendingWrite]], Awaitable[None]]):
        super().__init__(owner_id)
        self.writes = list(writes)
        self.run = run
        self.done = False

    def question(self) -> str:
        """Every queued change, each cut short enough that the whole list fits one message."""
        many = "these changes" if len(self.writes) > 1 else "this change"
        head = f"want me to make {many} in Uniloom?"
        room = (QUESTION_CHARS - len(head) - len(CANCELLED)) // max(1, len(self.writes)) - 3
        lines = [f"- {_cut(write.summary, room)}" for write in self.writes]
        return head + "\n" + "\n".join(lines)

    def _finish(self) -> bool:
        """Switches the buttons off. False when they were already used, so nothing runs twice."""
        if self.done:
            return False
        self.done = True
        self._switch_off()
        self.stop()
        return True

    @discord.ui.button(label="do it", style=discord.ButtonStyle.success)
    async def do_it(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self._finish():
            return
        await interaction.response.edit_message(view=self)
        await self.run(interaction, self.writes)

    @discord.ui.button(label="cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self._finish():
            return
        await interaction.response.edit_message(content=self.question() + CANCELLED, view=self)

    async def on_timeout(self) -> None:
        self.done = True
        await super().on_timeout()
