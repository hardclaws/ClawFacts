"""Moderator reminders that post into chat later.

    !reminder 60mins Check the lights are working     a delay from now
    !reminder 01:30PDT Check your lights              a wall-clock time
    !reminder list                                    what is pending
    !reminder cancel 2                                drop one
    !reminder cancel all                              drop them all

Durations accept `60mins`, `60 mins`, `90s`, `1h30m`, `2d`, and a bare number,
which means minutes ("remind me in 5"). The confirmation always restates both
the delay and the absolute time it resolved to, so a misread is visible at a
glance and can be cancelled.

Clock times take a timezone abbreviation (PDT, EST, AEST), an IANA name
(America/Los_Angeles), or nothing at all, which means the machine's local
time. Abbreviations are a *fixed* offset - "PDT" means UTC-7 whatever the
season, which is what someone typing it means; an IANA name tracks DST.

Reminders survive a restart. They are written to reminders.json as they are
created and reloaded when the bot comes back up, so one set for tomorrow
morning is not silently lost by a crash overnight.
"""

import datetime
import os
import re
import time

try:
    import zoneinfo
except ImportError:          # pragma: no cover - Python 3.8 and older
    zoneinfo = None

import storage

REMINDERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "reminders.json")

MIN_DELAY = 10.0            # anything shorter is just a chat message
MAX_DELAY = 14 * 86400.0    # two weeks out is not a reminder
MAX_PENDING = 20
MAX_MESSAGE = 400
LATE_GRACE = 3600.0         # fire on startup only if under an hour overdue

# Fixed UTC offsets for the abbreviations people actually type. zoneinfo
# rejects these: ZoneInfo("PDT") raises, it wants "America/Los_Angeles".
ZONE_OFFSETS = {
    "UTC": 0.0, "GMT": 0.0, "Z": 0.0,
    "EST": -5.0, "EDT": -4.0,
    "CST": -6.0, "CDT": -5.0,
    "MST": -7.0, "MDT": -6.0,
    "PST": -8.0, "PDT": -7.0,
    "AKST": -9.0, "AKDT": -8.0, "HST": -10.0,
    "AWST": 8.0, "ACST": 9.5, "AEST": 10.0, "ACDT": 10.5, "AEDT": 11.0,
    "NZST": 12.0, "NZDT": 13.0,
}

_UNITS = {
    "": 60.0, "s": 1.0, "sec": 1.0, "secs": 1.0, "second": 1.0, "seconds": 1.0,
    "m": 60.0, "min": 60.0, "mins": 60.0, "minute": 60.0, "minutes": 60.0,
    "h": 3600.0, "hr": 3600.0, "hrs": 3600.0, "hour": 3600.0, "hours": 3600.0,
    "d": 86400.0, "day": 86400.0, "days": 86400.0,
    "w": 604800.0, "week": 604800.0, "weeks": 604800.0,
}

# A duration is one or more number+unit pairs with nothing else in it.
_DURATION = re.compile(r"^(?:\d+(?:\.\d+)?[a-z]*)+$", re.I)
_PIECE = re.compile(r"(\d+(?:\.\d+)?)([a-z]*)", re.I)

# Split into "the clock" and "everything after it", then read the tail as a
# meridiem and/or a timezone. One regex cannot do this: it cannot tell the
# "pm" of "1:30pm" from the "A" that starts "America/Los_Angeles".
_CLOCK = re.compile(r"^\s*(\d{1,2}):([0-5]\d)(?::([0-5]\d))?\s*(.*)$", re.I)
_MERIDIEM = re.compile(r"^(a\.?m\.?|p\.?m\.?)(?=$|[\s])", re.I)


def human_seconds(seconds: float) -> str:
    """120 -> '2 minutes'. Used in every confirmation, so it stays plain."""
    seconds = int(round(seconds))
    parts = []
    for name, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            count, seconds = divmod(seconds, size)
            parts.append(f"{count} {name}" + ("" if count == 1 else "s"))
    if seconds or not parts:
        parts.append(f"{seconds} second" + ("" if seconds == 1 else "s"))
    return " ".join(parts[:3])


def _local_tz():
    return datetime.datetime.now().astimezone().tzinfo


def _zone(name: str):
    """A tzinfo for an abbreviation or an IANA name, or None if unknown."""
    if not name:
        return None
    key = name.strip()
    if key.upper() in ZONE_OFFSETS:
        return datetime.timezone(
            datetime.timedelta(hours=ZONE_OFFSETS[key.upper()]), key.upper())
    # A bare numeric offset: UTC-7, UTC+10, +0930.
    m = re.match(r"^(?:UTC|GMT)?([+-])(\d{1,2})(?::?([0-5]\d))?$", key, re.I)
    if m:
        hours = int(m.group(2))
        minutes = int(m.group(3) or 0)
        if hours > 14 or minutes > 59:
            return None
        delta = datetime.timedelta(hours=hours, minutes=minutes)
        if m.group(1) == "-":
            delta = -delta
        return datetime.timezone(delta, key.upper())
    if zoneinfo is None:
        return None
    try:
        return zoneinfo.ZoneInfo(key)
    except Exception:
        return None


def parse_delay(spec: str):
    """'60mins' -> (3600.0, '1 hour'), or (None, reason)."""
    text = re.sub(r"\s+", "", (spec or "").strip().lower())
    if not text:
        return None, "no delay given"
    if not _DURATION.match(text):
        return None, "not a duration"
    total = 0.0
    for number, unit in _PIECE.findall(text):
        if unit not in _UNITS:
            return None, f"'{unit}' is not a unit I know - use s, m, h, d or w"
        total += float(number) * _UNITS[unit]
    if total <= 0:
        return None, "the delay is zero"
    if total < MIN_DELAY:
        return None, f"the shortest reminder is {int(MIN_DELAY)} seconds"
    if total > MAX_DELAY:
        return None, f"the longest reminder is {int(MAX_DELAY // 86400)} days"
    return total, human_seconds(total)


def parse_clock(spec: str, now: float | None = None):
    """'01:30PDT' -> (epoch, 'PDT', rolled_to_tomorrow), or (None, reason, _).

    A time that has already passed today rolls to tomorrow, which is what
    "remind me at 1:30" means when it is already 4pm.
    """
    match = _CLOCK.match(spec or "")
    if not match:
        return None, "not a time of day", False
    hour, minute = int(match.group(1)), int(match.group(2))
    second = int(match.group(3) or 0)

    tail = (match.group(4) or "").strip()
    meridiem = ""
    found = _MERIDIEM.match(tail)
    if found:
        meridiem = found.group(1).replace(".", "").lower()
        tail = tail[found.end():].strip()
    zone_name = tail

    if meridiem:
        if not 1 <= hour <= 12:
            return None, f"{hour}:{minute:02d} is not a 12-hour time", False
        if meridiem[0] == "p" and hour != 12:
            hour += 12
        elif meridiem[0] == "a" and hour == 12:
            hour = 0
    elif hour > 23:
        return None, f"{hour} is not an hour", False

    if zone_name:
        tz = _zone(zone_name)
        if tz is None:
            return None, (f"'{zone_name}' is not a timezone I know - try PDT, "
                          f"EST, AEST, or a name like America/Los_Angeles"), False
        label = zone_name.upper() if zone_name.upper() in ZONE_OFFSETS \
            else zone_name
    else:
        tz = _local_tz()
        label = "local time"

    base = datetime.datetime.now(tz)
    target = base.replace(hour=hour, minute=minute, second=second,
                          microsecond=0)
    rolled = target <= base
    if rolled:
        target += datetime.timedelta(days=1)
    stamp = target.timestamp()
    if now is not None and stamp - now > MAX_DELAY:
        return None, f"that is more than {int(MAX_DELAY // 86400)} days away", rolled
    return stamp, label, rolled


class Reminder:
    """One pending reminder."""

    __slots__ = ("id", "due", "message", "creator", "label")

    def __init__(self, rid: int, due: float, message: str, creator: str,
                 label: str = ""):
        self.id = int(rid)
        self.due = float(due)
        self.message = message
        self.creator = creator
        self.label = label

    def as_dict(self) -> dict:
        return {"id": self.id, "due": self.due, "message": self.message,
                "creator": self.creator, "label": self.label}

    @classmethod
    def from_dict(cls, data: dict):
        try:
            return cls(int(data["id"]), float(data["due"]),
                       str(data["message"]), str(data.get("creator", "")),
                       str(data.get("label", "")))
        except (KeyError, TypeError, ValueError):
            return None

    def when(self, now: float | None = None) -> str:
        """'in 1 hour' / '3 minutes ago', for the list output."""
        now = time.time() if now is None else now
        return human_seconds(abs(self.due - now)) + (
            " from now" if self.due >= now else " ago")


class ReminderSet:
    """The pending reminders, persisted on every change."""

    def __init__(self, path: str = REMINDERS_PATH, late_grace: float = LATE_GRACE):
        self.path = path
        self.late_grace = late_grace
        self._items: list[Reminder] = []
        self._next_id = 1
        self.dropped_on_load = 0
        self._load()

    # ---- persistence --------------------------------------------------
    def _load(self) -> None:
        rows = storage.load_json(self.path, default=[])
        if not isinstance(rows, list):
            return
        now = time.time()
        for row in rows:
            item = Reminder.from_dict(row) if isinstance(row, dict) else None
            if item is None:
                continue
            # A reminder that was due while the bot was down still fires, but
            # one from three days ago is stale news and would be confusing.
            if now - item.due > self.late_grace:
                self.dropped_on_load += 1
                continue
            self._items.append(item)
            self._next_id = max(self._next_id, item.id + 1)
        self._items.sort(key=lambda r: r.due)
        if self.dropped_on_load:
            print(f"[reminders] dropped {self.dropped_on_load} reminder(s) "
                  f"that were over an hour overdue while the bot was down.",
                  flush=True)

    def save(self) -> bool:
        return storage.save_json(self.path,
                                 [r.as_dict() for r in self._items])

    # ---- queries ------------------------------------------------------
    def pending(self) -> list:
        return sorted(self._items, key=lambda r: r.due)

    def __len__(self) -> int:
        return len(self._items)

    def pop_due(self, now: float | None = None) -> list:
        """Remove and return the reminders whose time has come."""
        now = time.time() if now is None else now
        ready = [r for r in self._items if r.due <= now]
        if not ready:
            return []
        keep = [r for r in self._items if r.due > now]
        changed = len(keep) != len(self._items)
        self._items = keep
        if changed:
            self.save()
        return sorted(ready, key=lambda r: r.due)

    # ---- mutations ----------------------------------------------------
    def add(self, spec: str, message: str, creator: str,
            now: float | None = None):
        """Create one. Returns (Reminder, None) or (None, reason)."""
        now = time.time() if now is None else now
        message = " ".join((message or "").split())
        if not message:
            return None, "there is no message after the time"
        if len(message) > MAX_MESSAGE:
            return None, f"the message must be {MAX_MESSAGE} characters or fewer"
        if len(self._items) >= MAX_PENDING:
            return None, (f"there are already {MAX_PENDING} reminders pending - "
                          f"cancel one first")

        spec = (spec or "").strip()
        seconds, why_delay = parse_delay(spec)
        if seconds is not None:
            due = now + seconds
            label = f"in {why_delay}"
        else:
            due, zone_label, rolled = parse_clock(spec, now)
            if due is None:
                return None, (f"'{spec}' is neither a delay (60mins, 1h30m) nor "
                              f"a time of day (01:30PDT) - {why_delay}")
            clock = datetime.datetime.fromtimestamp(due).strftime("%H:%M")
            label = (f"at {clock} {zone_label}"
                     + (" tomorrow" if rolled else " today"))

        item = Reminder(self._next_id, due, message, creator, label)
        self._next_id += 1
        self._items.append(item)
        self.save()
        return item, None

    def cancel(self, which: str):
        """'3' or 'all'. Returns (removed_count, reason_or_None)."""
        text = (which or "").strip().lower()
        if text in ("all", "*", "everything"):
            count = len(self._items)
            self._items = []
            self.save()
            return count, (None if count else "there is nothing to cancel")
        if not text:
            return 0, "cancel which one? !reminder list shows the numbers"
        number = text.lstrip("#")
        if not number.isdigit():
            return 0, f"'{text}' is not a reminder number"
        wanted = int(number)
        before = len(self._items)
        self._items = [r for r in self._items if r.id != wanted]
        if len(self._items) == before:
            return 0, f"there is no reminder #{wanted}"
        self.save()
        return 1, None
