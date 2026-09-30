#!/usr/bin/env python3
# Windows one-file redactor - paste your banned words below or create banned_words.txt
import re, sqlite3, time, pathlib, sys
# EDIT THIS LIST with your moderation words, or leave empty and create banned_words.txt
BANNED = [
    # "dirty lepage",
    # "example",
]
# Also load from files if they exist
for fname in ["banned_words.txt", "moderation_words.txt", "blocked.txt", "banned.txt"]:
    p = pathlib.Path(fname)
    if p.exists():
        BANNED += [l.strip() for l in p.read_text(encoding="utf-8", errors="ignore").splitlines() if l.strip()]
BANNED = sorted(set(w for w in BANNED if w), key=len, reverse=True)
pat = re.compile("|".join(re.escape(w) for w in BANNED), re.IGNORECASE) if BANNED else None
def redact(t):
    if not pat:
        return t
    return pat.sub("***", t)

db_path = pathlib.Path("chat_memory.db")
if not db_path.exists():
    print(f"No db at {db_path}")
    sys.exit(1)
db = sqlite3.connect(str(db_path))
since = time.time()-86400
print(f"[redact] {len(BANNED)} words: {BANNED[:20]}")
print(f"\n=== {len(db.execute('SELECT 1 FROM summaries WHERE ts_to>=?',(since,)).fetchall())} SLICES TODAY ===")
for a,b,txt in db.execute("SELECT ts_from,ts_to,text FROM summaries WHERE ts_to>=? ORDER BY ts_to ASC",(since,)).fetchall():
    print(f"[{time.strftime('%H:%M', time.localtime(a))}-{time.strftime('%H:%M', time.localtime(b))}] {redact(txt[:800])}")

print(f"\n=== DIGEST TODAY (40 lines, what recap actually reads) ===")
import memory
m = memory.Memory(str(db_path))
for ts,who,line in m.digest(since, limit=40):
    print(f"[{time.strftime('%H:%M', time.localtime(ts))}] {redact(who)}: {redact(line[:500])}")
