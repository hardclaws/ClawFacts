import sqlite3, time, pathlib
db_path = pathlib.Path("chat_memory.db")
if not db_path.exists():
    print(f"No db at {db_path} - check bot folder")
    exit()
db = sqlite3.connect(str(db_path))
since = time.time() - 7*86400
rows = db.execute("SELECT ts_from, ts_to, text FROM summaries WHERE ts_to >= ? ORDER BY ts_to DESC LIMIT 25", (since,)).fetchall()
print(f"\n=== Last 7 days: {len(rows)} slices ===")
for a,b,t in reversed(rows):
    print(f"[{time.strftime('%a %m/%d %H:%M', time.localtime(a))}-{time.strftime('%H:%M', time.localtime(b))}] {t[:300]}")
print("\n--- Verbatim sample last 7 days (digest) ---")
# Use memory.py digest logic if available
try:
    import memory
    m = memory.Memory(str(db_path))
    for ts, who, line in m.digest(since, limit=30):
        print(f"[{time.strftime('%a %m/%d %H:%M', time.localtime(ts))}] {who}: {line[:200]}")
except Exception as e:
    print(f"digest error: {e}")
    # fallback transcript
    try:
        for ts, who, line in m.transcript(since, limit=50):
            print(f"[{time.strftime('%m/%d %H:%M', time.localtime(ts))}] {who}: {line[:200]}")
    except Exception:
        pass
