"""Moderator-defined chat commands, persisted across restarts.

    !cmd add discord Join us on Discord - discord.gg/example
    !cmd edit discord New link: discord.gg/example
    !cmd delete discord
    !cmd list

Then anyone who passes the normal access gate can run !discord.

Two rules matter more than the rest:

* A custom command can never shadow a built-in. If it could, "!help" typed by
  a moderator would silently stop meaning help.
* The message is capped well under the IRC limit, so a long one is refused
  rather than truncated mid-sentence when it is posted.

The message is mod-authored, so its content is their call. What is not their
call is the name it answers to, which is validated here.
"""

import os
import re

import storage

__all__ = ["CommandSet", "COMMANDS_PATH", "NAME_RE", "MAX_MESSAGE"]

COMMANDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "custom_commands.json")

#: Twitch-style: lowercase letters, digits and underscores, 2 to 25
#: characters. Requiring a letter first keeps "!123" from looking like a number
#: someone typed by accident. Two is the floor because short ones are real
#: (!gs, !ff); shorter than that is a typo waiting to be fired by accident.
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,24}$")

#: Under the bot's 450-character IRC limit, leaving room for the mention and
#: the label that get added when it is posted.
MAX_MESSAGE = 380

MAX_COMMANDS = 100


class CommandSet:
    """The mod-defined commands, loaded from and saved to disk."""

    def __init__(self, path: str = COMMANDS_PATH, reserved=()):
        self.path = path
        self.reserved = {str(r).lower() for r in reserved}
        self._items: dict[str, str] = {}
        self._load()

    # ---- persistence ----------------------------------------------------
    def _load(self) -> None:
        data = storage.load_json(self.path, default={})
        if not isinstance(data, dict):
            return
        items = data.get("commands")
        if not isinstance(items, dict):
            return
        for name, message in items.items():
            name = str(name).lower()
            if NAME_RE.match(name) and name not in self.reserved \
                    and isinstance(message, str) and message.strip():
                self._items[name] = message.strip()[:MAX_MESSAGE]

    def save(self) -> bool:
        return storage.save_json(self.path, {"commands": self._items})

    # ---- access ---------------------------------------------------------
    def __contains__(self, name: object) -> bool:
        return str(name).lower() in self._items

    def get(self, name: str) -> str | None:
        return self._items.get(str(name).lower())

    def names(self) -> list[str]:
        return sorted(self._items)

    def __len__(self) -> int:
        return len(self._items)

    # ---- mutations ------------------------------------------------------
    def add(self, name: str, message: str):
        """Returns (ok, why). `why` is either the refusal or a confirmation."""
        name = (name or "").strip().lower()
        message = " ".join((message or "").split())
        if not NAME_RE.match(name):
            return False, ("that is not a usable command name - 2 to 25 "
                           "characters, letters, digits and underscores, "
                           "starting with a letter")
        if name in self.reserved:
            return False, (f"!{name} is already a built-in command, so it "
                           f"cannot be redefined")
        if name in self._items:
            return False, (f"!{name} already exists - use edit to change it")
        if not message:
            return False, "give it something to say"
        if len(message) > MAX_MESSAGE:
            return False, (f"that is {len(message)} characters, over the "
                           f"{MAX_MESSAGE} limit - shorten it")
        if len(self._items) >= MAX_COMMANDS:
            return False, f"there are already {MAX_COMMANDS} custom commands"
        self._items[name] = message
        return True, f"!{name} created"

    def edit(self, name: str, message: str):
        name = (name or "").strip().lower()
        message = " ".join((message or "").split())
        if name not in self._items:
            return False, f"there is no !{name} to edit"
        if not message:
            return False, "give it something to say"
        if len(message) > MAX_MESSAGE:
            return False, (f"that is {len(message)} characters, over the "
                           f"{MAX_MESSAGE} limit - shorten it")
        self._items[name] = message
        return True, f"!{name} updated"

    def delete(self, name: str):
        name = (name or "").strip().lower()
        if name not in self._items:
            return False, f"there is no !{name} to delete"
        del self._items[name]
        return True, f"!{name} deleted"

    # ---- output ---------------------------------------------------------
    @staticmethod
    def render(message: str, user: str = "") -> str:
        """Fill in the placeholders a moderator may have used.

        Only {user} for now. Unknown placeholders are left alone rather than
        blanked, so a typo is visible instead of silently eating text.
        """
        if user:
            message = message.replace("{user}", user)
        return message
