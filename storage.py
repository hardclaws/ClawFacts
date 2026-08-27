"""Atomic JSON persistence for the bot's own small state files.

Shared by reminders.py and transporting.py. Written to a temp file and renamed
into place, because a half-written JSON file is not a minor annoyance here: a
corrupt config.json once put the bot into an infinite restart loop. If this
cannot be written, the feature degrades to in-memory and says so.
"""

import json
import os
import tempfile


def load_json(path: str, default=None):
    """Read a JSON file, or return `default` if it is absent or unreadable.

    An unreadable file is reported and ignored rather than raised: the bot must
    still start, and losing a reminder list is better than not coming up.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        print(f"[storage] ignoring unreadable {os.path.basename(path)}: {exc}",
              flush=True)
        return default


def save_json(path: str, obj) -> bool:
    """Write `obj` to `path` atomically. Returns True on success."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    except OSError as exc:
        print(f"[storage] cannot write {os.path.basename(path)}: {exc}",
              flush=True)
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)   # atomic on POSIX and on Windows
    except OSError as exc:
        print(f"[storage] could not write {os.path.basename(path)}: {exc}",
              flush=True)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False
    return True
