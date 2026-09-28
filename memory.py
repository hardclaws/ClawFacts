"""Long-term memory for the chat AI: the log and the distilled facts.

Two tables, one small SQLite file, stdlib only:

  * ``messages`` - what chat said (nick, login, time, text), pruned to the
    last 90 days. This is the raw material; it is never fed to the model
    wholesale - years of chat do not fit in a prompt and would read like
    court documents anyway.
  * ``memories`` - durable FACTS per viewer ("training for a 5k", "sleeps
    on the floor by choice"), distilled by the model after the bot talks.
    Recall is a handful of relevant facts injected into the persona prompt,
    which is what makes a bot feel like it remembers everything.

Privacy, learned the hard way: only public chat is recorded, and only
while ``chat_ai_enabled`` is on - the feature owns its data. ``!forget
<viewer>`` (mods) erases every memory and every logged message for that
viewer, and memory surfaces as familiarity, never as surveillance.
"""

import re
import sqlite3
import threading
import time

from funfacts import _norm, _overlap

DB_PATH = "chat_memory.db"

#: How long the raw log is kept. Memories are facts, not transcripts, and
#: stay until a mod forgets them.
LOG_DAYS = 90
#: Per viewer. Oldest facts fall off the end first.
MAX_PER_NICK = 25
#: A rolling summary covers a slice of the stream. Capped so a recap's
#: spine stays readable: thirty-six slices of a twelve-hour stream is
#: about fourteen thousand characters, which fits a hosted model with
#: room to spare and is still a fraction of the transcript it replaces.
MAX_SUMMARY_CHARS = 400


class Memory:
    """Thread-safe wrapper around the one SQLite file."""

    def __init__(self, path: str = DB_PATH, clock=time.time):
        self.path = path
        self.clock = clock
        self._lock = threading.Lock()
        self._db = None
        try:
            db = sqlite3.connect(path, check_same_thread=False)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS messages("
                "id INTEGER PRIMARY KEY, ts REAL, nick TEXT, login TEXT,"
                " text TEXT)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS memories("
                "id INTEGER PRIMARY KEY, ts REAL, nick TEXT, fact TEXT)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_mem_nick"
                       " ON memories(nick)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS summaries("
                "id INTEGER PRIMARY KEY, ts_from REAL, ts_to REAL,"
                " text TEXT)")
            db.commit()
            self._db = db
        except sqlite3.Error as exc:
            print(f"[memory] unavailable ({exc!r}) - running without "
                  f"memory", flush=True)

    @property
    def ok(self) -> bool:
        return self._db is not None

    # ---- the log -----------------------------------------------------
    def note(self, nick: str, login: str, text: str) -> None:
        """One line of chat, kept for distilling and pruning."""
        if not self.ok:
            return
        try:
            with self._lock:
                self._db.execute(
                    "INSERT INTO messages(ts, nick, login, text)"
                    " VALUES(?,?,?,?)",
                    (self.clock(), nick or "?", (login or "").lower(),
                     " ".join((text or "").split())[:500]))
                self._db.commit()
        except sqlite3.Error:
            pass                        # a lost line of log costs nothing

    def prune(self) -> None:
        """Drop logged messages older than the window."""
        if not self.ok:
            return
        try:
            with self._lock:
                self._db.execute(
                    "DELETE FROM messages WHERE ts < ?",
                    (self.clock() - LOG_DAYS * 86400,))
                self._db.commit()
        except sqlite3.Error:
            pass

    # ---- the facts ---------------------------------------------------
    def remember(self, nick: str, facts: list) -> int:
        """Store new facts for a viewer, dropping near-duplicates.

        Returns how many were actually kept."""
        if not self.ok or not facts:
            return 0
        nick = (nick or "?").strip()
        kept = 0
        try:
            with self._lock:
                have = [r[0] for r in self._db.execute(
                    "SELECT fact FROM memories WHERE nick = ?"
                    " ORDER BY ts DESC", (nick,))]
                for fact in facts:
                    f = " ".join((fact or "").split())
                    if not (8 <= len(f) <= 200):
                        continue
                    fn = _norm(f)
                    if any(_overlap(fn, _norm(h)) > 0.6 for h in have):
                        continue
                    self._db.execute(
                        "INSERT INTO memories(ts, nick, fact) VALUES(?,?,?)",
                        (self.clock(), nick, f))
                    have.append(f)
                    kept += 1
                # Cap per viewer: oldest facts fall off first.
                ids = [r[0] for r in self._db.execute(
                    "SELECT id FROM memories WHERE nick = ?"
                    " ORDER BY ts DESC", (nick,))]
                for old in ids[MAX_PER_NICK:]:
                    self._db.execute("DELETE FROM memories WHERE id = ?",
                                     (old,))
                self._db.commit()
        except sqlite3.Error:
            pass
        return kept

    def recall(self, nicks: list, limit: int = 8) -> list:
        """The most recent facts about the given viewers: [(nick, fact)]."""
        if not self.ok or not nicks:
            return []
        names = [(n or "").strip() for n in set(nicks) if n and n.strip()]
        if not names:
            return []
        marks = ",".join("?" * len(names))
        try:
            with self._lock:
                rows = self._db.execute(
                    f"SELECT nick, fact FROM memories WHERE nick IN ({marks})"
                    " ORDER BY ts DESC LIMIT ?", (*names, limit)).fetchall()
            return [(r[0], r[1]) for r in rows]
        except sqlite3.Error:
            return []

    # ---- the log, read back ---------------------------------------
    def transcript(self, since: float, limit: int = 1500,
                   nicks: list = None) -> list:
        """Logged chat newer than ``since``, oldest first: [(ts, nick, text)].

        The messages table has always been written - every line of chat
        goes through note() - and was never read: the only queries
        against it were the two DELETEs. Recaps drew eight distilled
        facts instead, which is why a five-day recap reported the same
        four events as a one-day one.
        """
        if not self.ok:
            return []
        sql = "SELECT ts, nick, text FROM messages WHERE ts >= ?"
        args = [since]
        names = sorted({(n or "").strip()
                        for n in (nicks or []) if n and n.strip()})
        if names:
            sql += " AND nick IN (%s)" % ",".join("?" * len(names))
            args.extend(names)
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(max(1, int(limit)))
        try:
            with self._lock:
                rows = self._db.execute(sql, tuple(args)).fetchall()
            return [(r[0], r[1], r[2]) for r in reversed(rows)]
        except sqlite3.Error:
            return []

    def digest(self, since: float, limit: int = 40, skip=()) -> list:
        """A spread of the period's real chat, not its last `limit` lines.

        Taking the newest N lines of a five-day window reports only the
        last hour, which is the complaint this exists to answer. The
        window is bucketed and the most substantive line taken from each,
        so a long period is represented end to end. Commands, the bot's
        own lines and one-word noise are dropped: none of it is a story.
        """
        rows = self.transcript(since)
        if not rows:
            return []
        drop = {(n or "").strip().lower() for n in skip if n and n.strip()}
        kept = []
        for ts, nick, text in rows:
            t = " ".join((text or "").split())
            if len(t) < 12 or t.startswith(("!", "/")):
                continue
            if (nick or "").strip().lower() in drop:
                continue
            kept.append((ts, nick, t))
        if len(kept) <= max(1, limit):
            return kept
        buckets = [[] for _ in range(max(1, limit))]
        for i, row in enumerate(kept):
            buckets[i * len(buckets) // len(kept)].append(row)
        return [max(b, key=lambda r: len(r[2])) for b in buckets if b]

    # ---- the rolling summary of the stream -------------------------
    def add_summary(self, ts_from: float, ts_to: float, text: str) -> bool:
        """Store one distilled slice of the stream."""
        if not self.ok:
            return False
        text = " ".join((text or "").split())[:MAX_SUMMARY_CHARS]
        if len(text) < 20:
            return False
        try:
            with self._lock:
                self._db.execute(
                    "INSERT INTO summaries(ts_from, ts_to, text)"
                    " VALUES(?,?,?)", (ts_from, ts_to, text))
                self._db.execute(
                    "DELETE FROM summaries WHERE ts_to < ?",
                    (ts_from - LOG_DAYS * 86400,))
                self._db.commit()
            return True
        except sqlite3.Error:
            return False

    def summaries(self, since: float, limit: int = 96) -> list:
        """The slices covering a period, oldest first: [(from, to, text)].

        This is what makes a recap scale. A twelve-hour stream logs
        thousands of lines and no prompt holds them, so the transcript is
        distilled as the stream runs and the recap reads the spine -
        complete coverage in a fraction of the tokens.
        """
        if not self.ok:
            return []
        try:
            with self._lock:
                rows = self._db.execute(
                    "SELECT ts_from, ts_to, text FROM summaries"
                    " WHERE ts_to >= ? ORDER BY ts_to DESC LIMIT ?",
                    (since, max(1, int(limit)))).fetchall()
            return [(r[0], r[1], r[2]) for r in reversed(rows)]
        except sqlite3.Error:
            return []

    def last_summary_end(self) -> float:
        """Where the next slice picks up; 0.0 when nothing is summarised."""
        if not self.ok:
            return 0.0
        try:
            with self._lock:
                row = self._db.execute(
                    "SELECT MAX(ts_to) FROM summaries").fetchone()
            return float(row[0]) if row and row[0] is not None else 0.0
        except sqlite3.Error:
            return 0.0

    def summary_count(self) -> int:
        """How much of the stream is on the spine."""
        if not self.ok:
            return 0
        try:
            with self._lock:
                return int(self._db.execute(
                    "SELECT COUNT(*) FROM summaries").fetchone()[0])
        except sqlite3.Error:
            return 0

    # ---- the exit ----------------------------------------------------
    def purge(self, nick: str) -> int:
        """Erase everything held about one viewer: their memories AND their
        logged messages. Returns how many memories were dropped."""
        if not self.ok:
            return 0
        nick = (nick or "").strip()
        if not nick:
            return 0
        try:
            with self._lock:
                cur = self._db.execute(
                    "SELECT COUNT(*) FROM memories WHERE nick = ?", (nick,))
                dropped = cur.fetchone()[0]
                self._db.execute("DELETE FROM memories WHERE nick = ?",
                                 (nick,))
                self._db.execute("DELETE FROM messages WHERE nick = ?"
                                 " COLLATE NOCASE", (nick,))
                self._db.commit()
            return dropped
        except sqlite3.Error:
            return 0

    def close(self) -> None:
        if self._db is not None:
            try:
                self._db.close()
            except sqlite3.Error:
                pass
            self._db = None


#: What counts as a durable fact worth keeping: the viewer's own world,
#: stated in public chat. Health details, politics, finances, religion and
#: anything intimate are deliberately NOT remembered - familiarity must
#: never turn into a file on someone.
DISTILL_RULES = (
    "You read one viewer's recent chat lines and extract durable facts "
    "about that viewer.\n"
    "Keep: work and vehicles, pets, family roles, hobbies and sports, "
    "plans and goals, strong preferences, where they are from.\n"
    "Never keep: health or medical details, politics, religion, finances, "
    "relationships, anything intimate, or anything about anyone else.\n"
    "Rules:\n"
    "- Only facts the viewer stated about themselves, in these lines.\n"
    "- One short line per fact, third person, no @mentions.\n"
    "- At most 3 facts. If nothing is durable, reply with exactly: "
    "NOTHING WORTH KEEPING\n"
)

_FACT_PREFIX = re.compile(r"^\s*(?:[-*\u2022]\s*|\d+[.)]\s*)")


def distill_prompt(nick: str, lines: list) -> str:
    """The extraction ask: this viewer's recent lines."""
    out = [f"Viewer: {nick}", "", "Their recent lines:"]
    out.extend(f"- {t}" for _, t in lines[-20:])
    out.append("")
    out.append("Durable facts:")
    return "\n".join(out)


def parse_facts(raw: str) -> list:
    """The model's extraction reply, as a clean list of facts."""
    facts = []
    for ln in (raw or "").splitlines():
        ln = _FACT_PREFIX.sub("", ln).strip().strip('"\u201c\u201d')
        if not ln or "@" in ln:
            continue
        if "NOTHING WORTH KEEPING" in ln.upper():
            return []
        if 8 <= len(ln) <= 200:
            facts.append(ln)
    return facts[:3]
