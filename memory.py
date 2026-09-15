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
