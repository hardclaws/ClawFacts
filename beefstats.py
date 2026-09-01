"""Persistent state for the beef game: points, titles, the leaderboard, the
!revenge window, and who is actually in chat right now.

This is real state, not more text. A leaderboard nobody can cite is a toy, and
a rematch window that dies with the worker thread that ran the original beef
is not a window - so the window is a timestamp in a file and the scoreboard is
a small JSON document, both written atomically through storage.py because a
half-written state file once put this bot into an restart loop.

Everything here is deliberately local, like beef.py itself: no network, no
model, no key. The game must always run on what the bot already has - a fun
command that can be taken down by a dead Ollama, an empty OpenRouter account,
or a 402 that disabled the LLM for the fun facts is not a game, it is a
dependency wearing one.

The numbers a stream will want to tune are all at the top, and points can go
negative on purpose: a scoreboard where losing costs nothing is a suggestion,
not a scoreboard.
"""

from __future__ import annotations

import time

import storage

__all__ = ["BeefState", "RecentChatters", "REVENGE_WINDOW", "WIN_POINTS",
           "LOSS_POINTS", "REVENGE_WIN_POINTS", "LEADER_ROWS",
           "TAG_RECENT_SECONDS", "title_for"]

STATE_PATH = "beef_state.json"

#: How many seconds a beaten player has to demand the rematch. Long enough to
#: read the loss, short enough that the rematch is still about that loss.
REVENGE_WINDOW = 60.0

#: Scoring. A win is worth 2, a loss costs 1, and avenging a loss is the hard
#: win: you beat the person who just beat you, inside a minute.
WIN_POINTS = 2
LOSS_POINTS = -1
REVENGE_WIN_POINTS = 3

#: Rows the leaderboard posts in chat. Five reads in one message; more is a
#: wall, and a wall gets scrolled past.
LEADER_ROWS = 5

#: How recently somebody must have been seen in chat for an @-tag to be aimed
#: at them. Half an hour: present enough to actually see the ping, without
#: keeping a lurker's whole evening on file.
TAG_RECENT_SECONDS = 1800.0

#: Titles, by wins. Earned, never bought: the only way up the ladder is to
#: start feuds and win them.
TITLES = (
    (15, "Legendary Grudge"),
    (10, "Chat Menace"),
    (6, "Feud Professional"),
    (3, "Certified Beefstarter"),
    (1, "Garden-Variety Grudge-Holder"),
)

#: Cap on recent-chatter tracking. Enough to cover a busy chat's active core
#: without the dict growing forever on a long stream.
SEEN_CAP = 400


def title_for(row: dict) -> str:
    """The title a record has earned. Zero wins is Fresh Meat, honestly."""
    wins = int(row.get("wins") or 0)
    for floor, title in TITLES:
        if wins >= floor:
            return title
    return "Fresh Meat"


def _new_row(name: str) -> dict:
    return {"name": name, "beefs": 0, "wins": 0, "losses": 0, "points": 0,
            "streak": 0, "best_streak": 0, "revenges": 0, "revenge_wins": 0,
            "last": 0.0}


def _clean_row(row: dict, fallback_name: str) -> dict:
    """Coerce whatever the file had into a well-formed row."""
    base = _new_row(str(row.get("name") or fallback_name))
    for key in base:
        if key == "name":
            continue
        try:
            base[key] = type(base[key])(row.get(key) or base[key])
        except (TypeError, ValueError):
            pass              # a corrupt field keeps its default, not the bot
    return base


class BeefState:
    """The scoreboard and the revenge windows, one per bot, one file."""

    def __init__(self, path: str = STATE_PATH, clock=time.time):
        self.path = path
        self.clock = clock
        self.players: dict[str, dict] = {}    # login -> row
        self.windows: dict[str, dict] = {}    # login -> {rival, genre, ts}
        self._load()

    # ---- persistence ---------------------------------------------------

    def _load(self) -> None:
        data = storage.load_json(self.path, None)
        if not isinstance(data, dict):
            return
        players = data.get("players")
        if isinstance(players, dict):
            self.players = {
                str(login): _clean_row(row, str(login))
                for login, row in players.items() if isinstance(row, dict)
            }
        windows = data.get("windows")
        if isinstance(windows, dict):
            now = self.clock()
            self.windows = {
                str(login): w for login, w in windows.items()
                if isinstance(w, dict)
                # A window older than the window is not a window; dropping it
                # here keeps the file from accreting one entry per player.
                and now - float(w.get("ts") or 0.0) < REVENGE_WINDOW
            }

    def save(self) -> bool:
        return storage.save_json(self.path, {
            "version": 1,
            "players": self.players,
            "windows": self.windows,
        })

    # ---- the game --------------------------------------------------------

    def record(self, issuer: str, rival: str, genre: str, issuer_won: bool,
               revenge: bool = False, theme: str = "") -> dict | None:
        """Score one feud for whoever started it, and arm or clear their
        revenge window. Returns the updated row, or None for a bad name.

        Only the issuer is scored. A named rival who never typed a beef
        command has not opted into having a record, and a leaderboard anyone
        could add anyone to by losing on purpose would not be worth reading.
        """
        name = (issuer or "").strip()
        if not name:
            return None
        login = name.lower()
        row = self.players.setdefault(login, _new_row(name))
        # Keep the freshest spelling of the name; Twitch display names drift.
        row["name"] = name
        row["beefs"] += 1
        if issuer_won:
            row["wins"] += 1
            row["streak"] += 1
            row["best_streak"] = max(row["best_streak"], row["streak"])
            row["points"] += (REVENGE_WIN_POINTS if revenge else WIN_POINTS)
        else:
            row["losses"] += 1
            row["streak"] = 0
            row["points"] += LOSS_POINTS
        if revenge:
            row["revenges"] += 1
            if issuer_won:
                row["revenge_wins"] += 1
        row["last"] = self.clock()

        if issuer_won:
            # A winner has nothing to avenge, and a stale window from an older
            # loss must not survive the win that settled it.
            self.windows.pop(login, None)
        else:
            self.windows[login] = {"rival": (rival or "").strip(),
                                   "genre": genre or "",
                                   "theme": " ".join((theme or "").split()),
                                   "ts": self.clock()}
        self.save()
        return row

    def window_for(self, name: str) -> dict | None:
        """The live revenge window for `name`, or None.

        Returns {"rival", "genre", "seconds_left"}; seconds_left is > 0 or the
        window is already gone. Checked against the clock at call time, never
        against a timer object, so it works no matter which thread asks and
        survives a restart inside the window.
        """
        w = self.windows.get((name or "").lower())
        if not w:
            return None
        left = REVENGE_WINDOW - (self.clock() - float(w.get("ts") or 0.0))
        if left <= 0:
            return None
        return {"rival": w.get("rival") or "", "genre": w.get("genre") or "",
                "theme": w.get("theme") or "", "seconds_left": int(left)}

    def is_player(self, name: str) -> bool:
        """True if this name has played - i.e. typed a beef command itself.

        That act is the opt-in the tagging gate leans on: naming somebody in
        a feud is the issuer's choice, but pinging them with @ is earned by
        the rival having actually joined the game.
        """
        return (name or "").strip().lower() in self.players

    # ---- what chat sees --------------------------------------------------

    def leaderboard(self, rows: int = LEADER_ROWS) -> list:
        """Top rows as (display name, row), points first, then wins."""
        ranked = sorted(self.players.values(),
                        key=lambda r: (-r.get("points", 0), -r.get("wins", 0),
                                       r.get("name", "").lower()))
        return [(r["name"], r) for r in ranked[:rows]]

    def leader_line(self, rows: int = LEADER_ROWS) -> str:
        """The leaderboard as one chat line, or "" when nobody has played."""
        top = self.leaderboard(rows)
        if not top:
            return ""
        parts = [f"{i}. {name} {r['points']}pts "
                 f"({r['wins']}W-{r['losses']}L"
                 + (f", streak {r['streak']}" if r.get("streak") else "")
                 + ")"
                 for i, (name, r) in enumerate(top, 1)]
        return " | ".join(parts)

    def card(self, name: str) -> str | None:
        """One player's line, or None if they have never played."""
        row = self.players.get((name or "").strip().lower())
        if not row:
            return None
        revenge = (f", {row['revenge_wins']} avenged"
                   if row.get("revenge_wins") else "")
        return (f"{row['name']}: {row['beefs']} "
                f"beef{'s' if row['beefs'] != 1 else ''}, {row['wins']}W-"
                f"{row['losses']}L, {row['points']} pts, streak "
                f"{row['streak']} (best {row['best_streak']}){revenge} - "
                f"{title_for(row)}")


class RecentChatters:
    """Who has been seen in chat lately, for the tagging gate.

    In-memory on purpose: presence is only interesting while the bot is
    running, and this must never turn the state file into a log of everyone
    who ever watched. A capped dict of the last few hundred names is enough
    to know whether an @ would reach a person.
    """

    def __init__(self, cap: int = SEEN_CAP, clock=time.time):
        self.cap = max(10, cap)
        self.clock = clock
        self._seen: dict[str, tuple[str, float]] = {}   # login -> (name, ts)

    def note(self, display: str, ts: float | None = None) -> None:
        name = (display or "").strip()
        if not name:
            return
        when = self.clock() if ts is None else ts
        self._seen[name.lower()] = (name, when)
        if len(self._seen) > self.cap:
            # Drop the stalest half rather than one entry per message - the
            # eviction would otherwise run on every line of a busy chat.
            for login, _ in sorted(self._seen.items(),
                                   key=lambda kv: kv[1][1])[:len(self._seen)
                                                             - self.cap]:
                del self._seen[login]

    def seen(self, name: str, within: float = TAG_RECENT_SECONDS) -> bool:
        """True if `name` was seen in chat within `within` seconds."""
        entry = self._seen.get((name or "").strip().lower())
        if not entry:
            return False
        return self.clock() - entry[1] <= within
