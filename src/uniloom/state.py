"""What one reply did with Uniloom, for the buttons that go under it."""
from dataclasses import dataclass, field
from typing import List, Set


@dataclass
class PendingWrite:
    """A change the model asked for, waiting for the person to press do it."""
    tool: str
    arguments: dict
    # One line for the person, such as "save item: title Login page, parent TG-4"
    summary: str


@dataclass
class UniloomState:
    # The Discord user whose account the reply used. Only they can press the buttons
    owner_id: str = ""
    # True when the reply needs the Connect Uniloom button
    connect: bool = False
    # The names of the Uniloom tools already given to the model in this reply
    loaded: Set[str] = field(default_factory=set)
    # True once a Uniloom tool ran, so the reply is never cached
    used: bool = False
    writes: List[PendingWrite] = field(default_factory=list)

    @property
    def touched(self) -> bool:
        return self.connect or self.used or bool(self.loaded) or bool(self.writes)

    @property
    def needs_buttons(self) -> bool:
        return self.connect or bool(self.writes)
