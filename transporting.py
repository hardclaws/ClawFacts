"""The !transporting status board.

    !transporting update Produce     what the truck is hauling (mods)
    !transporting                    anyone in chat can ask
    !transporting delete             clear it (mods)

One line of state, written to transporting.json so it survives a restart -
what the truck is carrying does not change just because the bot was updated.
"""

import os
import time

import storage

TRANSPORTING_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "transporting.json")

MAX_CARGO = 200


class Cargo:
    """The current load, and who set it."""

    def __init__(self, path: str = TRANSPORTING_PATH):
        self.path = path
        self.text = ""
        self.set_by = ""
        self.set_at = 0.0
        self._load()

    def _load(self) -> None:
        data = storage.load_json(self.path, default={})
        if isinstance(data, dict):
            self.text = str(data.get("text", ""))[:MAX_CARGO]
            self.set_by = str(data.get("set_by", ""))
            try:
                self.set_at = float(data.get("set_at", 0.0) or 0.0)
            except (TypeError, ValueError):
                self.set_at = 0.0

    def save(self) -> bool:
        return storage.save_json(self.path, {
            "text": self.text, "set_by": self.set_by, "set_at": self.set_at,
        })

    @property
    def is_set(self) -> bool:
        return bool(self.text)

    def update(self, text: str):
        """Set the load. Returns (True, None) or (False, reason)."""
        text = " ".join((text or "").split())
        if not text:
            return False, "update to what? !transporting update Produce"
        if len(text) > MAX_CARGO:
            return False, f"keep it under {MAX_CARGO} characters"
        self.text = text
        self.set_at = time.time()
        self.save()
        return True, None

    def delete(self):
        """Clear the load. Returns (True, None) or (False, reason)."""
        if not self.text:
            return False, "nothing is logged as transporting right now"
        self.text = ""
        self.set_by = ""
        self.set_at = 0.0
        self.save()
        return True, None

    def age(self, now: float | None = None) -> str:
        """'4 minutes ago' / 'just now', for the provenance line."""
        if not self.set_at:
            return ""
        now = time.time() if now is None else now
        seconds = max(0, int(now - self.set_at))
        if seconds < 60:
            return "just now"
        for name, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
            if seconds >= size:
                count = seconds // size
                return (f"{count} {name}" + ("" if count == 1 else "s")
                        + " ago")
        return f"{seconds} seconds ago"

    def phrase(self) -> str:
        """What a viewer sees. Empty string when nothing is logged."""
        return self.text
