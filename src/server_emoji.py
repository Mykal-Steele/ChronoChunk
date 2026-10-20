"""
Custom server emoji in the bot's replies.

The model writes an emoji as :name:. Discord only shows one when the message carries
its id, <:name:id>, and the bot may use that emoji where it is posting. Anything else
reaches the channel as broken text. So every reply goes through render() right before
it is sent: a name the bot can use here becomes the emoji, and every other :name: or
<:name:id> is taken out. No emoji is better than a broken one.
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List

from src import emoji_notes

logger = logging.getLogger(__name__)

# One reply shows at most this many emoji, custom and standard together, and this many custom ones
MAX_EMOJI_PER_REPLY = 2
MAX_CUSTOM_PER_REPLY = 1
# After a reply with a custom emoji, this many of the bot's next replies in the channel get none
QUIET_REPLIES = 3
# The bot puts a reaction on a message at most this often in one channel, in seconds
REACTION_GAP_SECONDS = 600
# How many emoji the model is told about, this server's first
MAX_NAMES_IN_PROMPT = 30
# When someone asks the bot about its emoji, a reply that names this many different ones is a list
# of them. It may show up to MAX_LISTED, each once. Any other reply keeps the limits above.
LIST_NAMES = 3
MAX_LISTED = 8

# Standard emoji the model sometimes writes by name. Discord does not turn these into emoji for a bot.
STANDARD = {"skull": "💀", "fire": "🔥", "sob": "😭", "pray": "🙏", "joy": "😂"}

_NAME = r"[A-Za-z0-9_]{2,32}"
_MARKUP = re.compile(rf"<(a?):({_NAME}):(\d{{15,22}})>")
# Markup the model cut short or got wrong, which Discord would print as it stands
_BROKEN_MARKUP = re.compile(rf"<a?:{_NAME}(?::\d*)?>?")
_SHORTCODE = re.compile(rf":({_NAME}):")
# One or more names standing on their own, like ":kekw:" or ":kekw::pog:"
_ALONE = re.compile(rf"(?<![\w:])(?::{_NAME}:)+(?![\w:])")
# A name stuck to the end of a word, like "bruh:kekw:" or "lol2:kekw:". "note:todo:fix" and "10:30:45" do not match.
_ATTACHED = re.compile(rf"(?<=[A-Za-z0-9_]):({_NAME}):(?![\w:])")
# A name with its closing colon left off, like "lol :kekw". Only a name the bot knows counts, so "Re :hello" stays.
_DANGLING = re.compile(r"(?<![\w:<]):([A-Za-z][A-Za-z0-9_]{1,31})(?![\w:])")
# Text that is left exactly as written: code blocks, inline code and links
_PROTECTED = re.compile(r"(```.*?```|`[^`\n]+`|https?://\S+)", re.DOTALL)
# One standard emoji, in the ranges the reply formatter counts. A skin tone, a variation mark
# and the parts of a joined emoji (a family, a flag with a colour) stay with it, so the limit
# never cuts one emoji in two.
_STANDARD_CHAR = ("[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
                  "\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U00002702-\U000027B0]")
_STANDARD = re.compile(rf"{_STANDARD_CHAR}(?:[\U0001F3FB-\U0001F3FF\uFE0F]|\u200D{_STANDARD_CHAR})*")
# A message that is about emoji themselves, in English or Thai
_ABOUT_EMOJI = re.compile(r"\b(?:emoji|emote|emoticon)s?\b|อีโมจิ|อิโมจิ", re.IGNORECASE)
# How the model asks for a reaction on the message it is answering: [react :name:] or [react 💀]
_REACT = re.compile(r"\[\s*react(?:ion)?\b\s*:?\s*([^\]\n]{0,80})\]", re.IGNORECASE)

# Added to the system prompt when the server has custom emoji. {names} is filled in per server.
PROMPT = """

SERVER EMOJI:
- this server has its own emoji and people here use them the way they use lol or bruh. u write one as its name between colons, like "nah thats wild :{example}:"
- these are not counted by the EMOJI RULE above, that rule is about normal emoji. for these: when u react to something funny, dumb, sad or hype and one fits, end the message with it. roughly one reply in four. one per message, never two.
- serious answers, explanations and anything they are upset about get none.
- what each one shows is in the brackets after its name. pick by that, not by the name alone.
- if they ask which emoji u have, show a handful of the best ones, each written :name:. eight at most, never the whole list.
- only these names work. never make one up and never write the <:name:id> form:
{names}
"""

# Added as well when the bot may put a reaction on the message it is answering
REACTION_PROMPT = """
REACTING:
- u can also put an emoji reaction on their message, the way people tap a reaction in discord. to do that, end ur reply with [react 😂] or [react :name:]. they never see the brackets, only the reaction on their message.
- example: they say "i called my teacher mom in front of everyone" and u answer: nahh thats a core memory now, u cant come back from that [react 😂]
- do it for the one message in ten or so that is actually funny or embarrassing, or where one emoji says it perfectly. not for normal questions, greetings or serious stuff.
- a reaction never replaces the reply. u still write ur normal message first.
"""


@dataclass(frozen=True)
class EmojiSet:
    """The custom emoji one reply may use."""
    # lowercase name -> the text Discord shows as the emoji
    by_name: Dict[str, str] = field(default_factory=dict)
    # names as the servers spell them, this server's first
    names: List[str] = field(default_factory=list)
    # lowercase name -> a few words on what the emoji shows, for the ones that have been looked at
    notes: Dict[str, str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.by_name)

    def prompt(self, reactions: bool = False) -> str:
        """
        The lines that tell the model which emoji it has, and with reactions that it may
        react to the message it is answering. Empty when there is nothing to tell.
        """
        text = ""
        if self.names:
            offered = self.names[:MAX_NAMES_IN_PROMPT]
            listed = [f":{name}: ({self.notes[name.lower()]})" if self.notes.get(name.lower()) else f":{name}:"
                      for name in offered]
            text = PROMPT.format(example=offered[0], names="\n".join(listed))
        if reactions:
            text += ("" if text else "\n") + REACTION_PROMPT
        return text


NO_EMOJI = EmojiSet()


def _external_allowed(channel, guild, via_interaction: bool) -> bool:
    """Whether a message here may show emoji from other servers."""
    me = guild.me
    if me is None:
        return False
    if channel.permissions_for(me).use_external_emojis is not True:
        return False
    # An answer to a slash command is posted through a webhook, which Discord has checked
    # against @everyone's permissions. Both have to allow it.
    if via_interaction and channel.permissions_for(guild.default_role).use_external_emojis is not True:
        return False
    return True


def usable_emoji(channel, guilds: Iterable, via_interaction: bool = False) -> EmojiSet:
    """
    The custom emoji the bot may use in this channel: the server's own, and other
    servers' when the bot has the Use External Emoji permission here. None in DMs.
    guilds is every server the bot is in. Any doubt leaves an emoji out.
    """
    try:
        guild = getattr(channel, "guild", None)
        if guild is None:
            return NO_EMOJI
        by_name: Dict[str, str] = {}
        names: List[str] = []
        notes: Dict[str, str] = {}

        def add(emoji) -> None:
            name, markup = emoji.name, str(emoji)
            found = _MARKUP.fullmatch(markup)
            # The text has to be exactly what Discord renders, for the very name the model will write
            if not isinstance(name, str) or not found or found.group(2) != name or name.lower() in by_name:
                return
            # is_usable covers an emoji Discord has switched off and one limited to roles the bot lacks
            if emoji.is_usable() is not True:
                return
            by_name[name.lower()] = markup
            names.append(name)
            note = emoji_notes.note_for(found.group(3))
            if note:
                notes[name.lower()] = note

        for emoji in guild.emojis:
            add(emoji)
        if _external_allowed(channel, guild, via_interaction):
            for other in guilds:
                if other.id == guild.id or other.unavailable is not False:
                    continue
                for emoji in other.emojis:
                    add(emoji)
        return EmojiSet(by_name, names, notes)
    except Exception as e:
        # A reply without custom emoji is fine. A reply that fails over them is not.
        logger.warning(f"Could not work out the usable emoji, leaving them out: {e!r}")
        return NO_EMOJI


def demote(text: str) -> str:
    """Custom emoji written the way the model writes them, :name:, for the chat history."""
    return _MARKUP.sub(lambda found: f":{found.group(2)}:", text)


def used_custom_emoji(text: str) -> bool:
    """Whether a past reply, as kept in the history, had a custom emoji in it."""
    names = [found.group(1) for run in _ALONE.finditer(demote(text)) for found in _SHORTCODE.finditer(run.group(0))]
    names += [found.group(1) for found in _ATTACHED.finditer(demote(text))]
    return any(name.lower() not in STANDARD and not name.isdigit() for name in names)


def asked_about_emoji(message: str) -> bool:
    """Whether what someone wrote to the bot is about emoji, so that an answer may show several of them."""
    return bool(_ABOUT_EMOJI.search(message or ""))


@dataclass(frozen=True)
class Rendered:
    """A reply ready to send, and the same reply as the chat history should remember it."""
    text: str
    plain: str
    # The emoji the model asked to put on the message it is answering, in the form Discord takes. Empty for none.
    reaction: str = ""


def render(text: str, emoji: EmojiSet = NO_EMOJI, custom_allowed: bool = True,
           limit: int = MAX_EMOJI_PER_REPLY, listing: bool = False) -> Rendered:
    """
    Make a reply safe to send. Emoji names the bot can use become emoji, at most limit
    per reply with standard emoji counted in, and no custom ones when custom_allowed is
    off. Every other emoji name or id is removed. Code and links are not touched.

    listing is for an answer to someone who asked about the emoji. A reply that then
    names several different ones shows up to MAX_LISTED of them, each once.

    A [react ...] note from the model is always taken out of the text. When it names
    an emoji the bot can use, that emoji comes back as Rendered.reaction.
    """
    # Each emoji found is swapped for a numbered slot first. The limit is applied afterwards, in reading order.
    slots: List[tuple] = []  # (what is sent, what the history keeps, is it custom)
    reactions: List[str] = []

    def slot(shown: str = "", plain: str = "", custom: bool = False) -> str:
        """Leave a slot in the text. An empty one marks something that is always taken out."""
        slots.append((shown, plain, custom))
        return f"\x00{len(slots) - 1}\x00"

    def react(found: re.Match) -> str:
        wanted = found.group(1).strip()
        full = _MARKUP.fullmatch(wanted)
        name = (full.group(2) if full else wanted.strip(":")).lower()
        if name in emoji.by_name:
            reactions.append(emoji.by_name[name])
        elif name in STANDARD:
            reactions.append(STANDARD[name])
        elif _STANDARD.fullmatch(wanted):
            reactions.append(wanted)
        return slot()

    def named(name: str, raw: str = "") -> str:
        """The slot for a name the model wrote. raw is what stays when the name is only a number, as in a time."""
        key = name.lower()
        if key in emoji.by_name:
            return slot(emoji.by_name[key], f":{name}:", True)
        if key in STANDARD:
            return slot(STANDARD[key], STANDARD[key])
        return raw if raw and name.isdigit() else slot()

    def markup(found: re.Match) -> str:
        # An id is only trusted when it is the one the usable list gives for that name
        if emoji.by_name.get(found.group(2).lower()) == found.group(0):
            return slot(found.group(0), f":{found.group(2)}:", True)
        return named(found.group(2))

    pieces = _PROTECTED.split(text.replace("\x00", ""))
    for index in range(0, len(pieces), 2):
        piece = _REACT.sub(react, pieces[index])
        piece = _MARKUP.sub(markup, piece)
        piece = _BROKEN_MARKUP.sub(lambda found: slot(), piece)
        piece = _ALONE.sub(lambda run: _SHORTCODE.sub(lambda found: named(found.group(1)), run.group(0)), piece)
        piece = _ATTACHED.sub(lambda found: named(found.group(1), found.group(0)), piece)
        piece = _DANGLING.sub(lambda found: named(found.group(1)) if found.group(1).lower() in emoji.by_name
                              or found.group(1).lower() in STANDARD else found.group(0), piece)
        pieces[index] = _STANDARD.sub(lambda found: slot(found.group(0), found.group(0)), piece)

    # They asked about the emoji and the reply names several: that is a list, not a reaction
    listing = listing and len({plain.lower() for _, plain, custom in slots if custom}) >= LIST_NAMES
    listed: set = set()

    keep = [False] * len(slots)
    kept = kept_custom = 0
    for number in (int(n) for piece in pieces[::2] for n in re.findall(r"\x00(\d+)\x00", piece)):
        shown, plain, custom = slots[number]
        if listing and custom:
            if plain.lower() not in listed and len(listed) < MAX_LISTED:
                listed.add(plain.lower())
                keep[number] = True
            continue
        if not shown or kept >= limit:
            continue
        if custom and (not custom_allowed or kept_custom >= MAX_CUSTOM_PER_REPLY):
            continue
        keep[number] = True
        kept += 1
        kept_custom += custom

    def fill(piece: str, which: int) -> str:
        def run(found: re.Match) -> str:
            """Fill a stretch of slots and the spaces around them, without leaving a gap where one was emptied."""
            whole = found.group(0)
            spaced = " " if re.search(r"[ \t]", whole) else ""
            # Kept emoji stay apart only where the reply had them apart
            shown, gap = "", ""
            for part in re.split(r"\x00(\d+)\x00", whole)[1:]:
                if not part.isdigit():
                    gap = gap or (" " if part else "")
                elif keep[int(part)]:
                    shown += (gap if shown else "") + slots[int(part)][which]
                    gap = ""
            if shown:
                lead = " " if whole[0] in " \t" else ""
                tail = " " if whole[-1] in " \t" else ""
                return lead + shown + tail
            at_line_start = found.start() == 0 or piece[found.start() - 1] == "\n"
            rest = piece[found.end():found.end() + 1]
            if at_line_start or rest in ("", "\n", ".", ",", "!", "?"):
                return ""
            return spaced
        return re.sub(r"[ \t]*(?:\x00\d+\x00[ \t]*)+", run, piece)

    def scrub(piece: str, which: int) -> str:
        """
        The last check before sending: no emoji id but the ones just put in, and no
        emoji name left standing. Taking one piece out can leave two others touching
        that read as an emoji together, so this repeats until nothing changes.
        """
        usable = set(emoji.by_name.values()) if which == 0 else set()
        while True:
            cleaned = _BROKEN_MARKUP.sub(lambda found: found.group(0) if found.group(0) in usable else "", piece)
            if which == 0:
                cleaned = _ALONE.sub("", cleaned)
                cleaned = _ATTACHED.sub(lambda found: found.group(0) if found.group(1).isdigit() else "", cleaned)
            if cleaned == piece:
                return piece
            piece = cleaned

    def build(which: int) -> str:
        return "".join(piece if index % 2 else scrub(fill(piece, which), which)
                       for index, piece in enumerate(pieces)).strip()

    shown, plain = build(0), build(1)
    if not shown and text.strip():
        # The whole reply was an emoji that could not be shown. Its name as a word beats silence.
        words = " ".join(found.group(1) for found in _SHORTCODE.finditer(demote(text))) or "lol"
        return Rendered(words, words, reactions[0] if reactions else "")
    return Rendered(shown, plain, reactions[0] if reactions else "")
