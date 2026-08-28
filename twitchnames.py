"""Name pools for the Twitch edition of !smk.

Two pools, both live Twitch data:

* **top streamers** - Get Streams, which Twitch orders by viewer count, so
  page one is the top of the site at this moment.
* **the channel's followers** - Get Channel Followers, moderator-only.

There is deliberately no seeded list of famous streamers here. A hardcoded
roster is a list someone typed two years ago, and it goes stale the day it is
written; the API answer is true right now. If Twitch cannot be reached the game
says so rather than substituting names it made up.

Every name carries a real descriptor: the game a streamer is on, or how long a
follower has been following. Nothing is invented to fill the slot.
"""

import datetime
import os
import random
import threading
import time

import storage

__all__ = ["TwitchNamePool", "TWITCH_NAMES_PATH", "since_label", "REFRESH_SECONDS"]

TWITCH_NAMES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "twitch_names.json")

#: Half an hour. Both endpoints cost 1 rate-limit point (plus 1 per follower
#: page), so this is nowhere near Twitch's allowance - it just keeps a busy
#: channel from asking for the same top-100 list on every !smk.
REFRESH_SECONDS = 1800.0

#: How many follower pages to walk. 100 per page, newest follower first.
FOLLOWER_PAGES = 3

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def since_label(iso: str) -> str:
    """'2024-03-11T07:22:31Z' -> 'followed since Mar 2024'.

    Month and year only: a follower list is refreshed often and the exact day
    would be noise. Returns '' if the timestamp cannot be read, so the caller
    falls back to the honest, vaguer word.
    """
    if not iso:
        return ""
    try:
        stamp = datetime.datetime.strptime(iso[:7], "%Y-%m")
    except (ValueError, TypeError):
        return ""
    if stamp.year < 2011 or stamp.year > 2100:
        return ""                     # Twitch did not exist before 2011
    return f"followed since {MONTHS[stamp.month - 1]} {stamp.year}"


def _streamer_label(game: str, viewers: int) -> str:
    game = " ".join(str(game or "").split())
    if game:
        return f"streaming {game}"
    # A stream with no category set is rare but real; the viewer count is the
    # next true thing to say, and it is in the same response.
    if viewers:
        return f"live with {viewers:,} watching"
    return "live now"


class TwitchNamePool:
    """Cached Twitch names, drawn three at a time."""

    def __init__(self, path: str = TWITCH_NAMES_PATH,
                 refresh_seconds: float = REFRESH_SECONDS):
        self.path = path
        self.refresh_seconds = float(refresh_seconds)
        self._rows: list[list[str]] = []      # [display name, descriptor]
        self._recent: list[str] = []
        self.updated = None
        # RLock: draw() calls _available() while the caller may already hold
        # this, and a plain Lock deadlocked the same way names.py's did.
        self._lock = threading.RLock()
        self._load()

    # ---- persistence ----------------------------------------------------
    def _load(self) -> None:
        data = storage.load_json(self.path, None)
        if not isinstance(data, dict):
            return
        rows = data.get("names")
        if isinstance(rows, list):
            with self._lock:
                self._rows = [[str(r[0]), str(r[1])] for r in rows
                              if isinstance(r, (list, tuple)) and len(r) >= 2
                              and r[0]]
        self.updated = data.get("updated")

    def _save(self) -> bool:
        with self._lock:
            payload = {"names": [list(r) for r in self._rows],
                       "updated": self.updated}
        return storage.save_json(self.path, payload)

    # ---- state ----------------------------------------------------------
    def counts(self) -> dict:
        with self._lock:
            streamers = sum(1 for r in self._rows
                            if r[1].startswith("streaming")
                            or r[1].startswith("live"))
            return {"streamers": streamers,
                    "followers": len(self._rows) - streamers,
                    "total": len(self._rows)}

    def stale(self, now: float | None = None) -> bool:
        """True when the cache should be refilled.

        An empty pool is always stale - that is the only way a first run, or a
        run after the API was down, ever gets populated.
        """
        with self._lock:
            if not self._rows:
                return True
        now = time.time() if now is None else now
        try:
            stamp = float(self.updated or 0)
        except (TypeError, ValueError):
            return True
        return (now - stamp) >= self.refresh_seconds

    # ---- refresh --------------------------------------------------------
    def refresh(self, helix, broadcaster_id: str = "",
                include_followers: bool = False) -> dict:
        """Pull both pools. Returns what happened, for logging and for chat.

        Partial success is kept: if the follower call fails but the streamer
        call worked, the streamers are still cached. Losing one pool should
        not cost the game entirely.
        """
        result = {"streamers": 0, "followers": 0, "failed": []}
        rows: list[list[str]] = []

        streams = helix.top_streams()
        if streams is None:
            result["failed"].append("the top-streams lookup")
        else:
            for name, game, viewers in streams:
                rows.append([name, _streamer_label(game, viewers)])
            result["streamers"] = len(streams)

        if include_followers:
            followers = helix.channel_followers(broadcaster_id,
                                               pages=FOLLOWER_PAGES)
            if followers is None:
                result["failed"].append("the follower list")
            else:
                for name, followed_at in followers:
                    rows.append([name, since_label(followed_at) or "follower"])
                result["followers"] = len(followers)

        if not rows:
            # Keep whatever was cached. A failed refresh must not empty a pool
            # that was working a minute ago.
            return result

        # Twitch display names are unique, but the two pools are pulled at
        # slightly different moments and a streamer can also be a follower.
        seen, deduped = set(), []
        for name, label in rows:
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append([name, label])

        with self._lock:
            self._rows = deduped
            self.updated = time.time()
        self._save()
        return result

    # ---- drawing --------------------------------------------------------
    def _available(self) -> list[list[str]]:
        with self._lock:
            return [list(r) for r in self._rows]

    def draw(self, n: int = 3):
        """`n` [name, descriptor] pairs, avoiding whoever was just used."""
        pool = self._available()
        if len(pool) < n:
            return []
        recent = set(self._recent[-(max(9, n * 3)):])
        fresh = [r for r in pool if r[0] not in recent]
        # Not enough unseen names left: forget the memory rather than repeat a
        # round, or refuse the game. Repeating a whole round reads as broken.
        if len(fresh) < n:
            self.forget_recent()
            fresh = pool
        picked = random.sample(fresh, n)
        self.mark_used([r[0] for r in picked])
        return picked

    def mark_used(self, names) -> None:
        with self._lock:
            for name in names:
                if name not in self._recent:
                    self._recent.append(name)
            # Long enough that a busy chat does not cycle the same six people,
            # short enough that a small channel is not starved.
            self._recent = self._recent[-45:]

    def forget_recent(self) -> None:
        with self._lock:
            self._recent = []

    def names(self) -> list[str]:
        with self._lock:
            return [r[0] for r in self._rows]
