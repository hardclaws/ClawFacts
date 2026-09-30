import sqlite3, time, pathlib
db_path = pathlib.Path("chat_memory.db")
if not db_path.exists():
    print(f"No db at {db_path}")
    exit()
db = sqlite3.connect(str(db_path))
# Today = last 24h
since = time.time() - 86400
print(f"\n=== TODAY since {time.strftime('%Y-%m-%d %H:%M', time.localtime(since))} ===")
rows = db.execute("SELECT ts_from, ts_to, text FROM summaries WHERE ts_to >= ? ORDER BY ts_to ASC", (since,)).fetchall()
print(f"{len(rows)} slices for today")
for a,b,t in rows:
    print(f"[{time.strftime('%H:%M', time.localtime(a))}-{time.strftime('%H:%M', time.localtime(b))}] {t[:400]}")

print("\n--- Verbatim digest today (what recap actually reads) ---")
try:
    import memory
    m = memory.Memory(str(db_path))
    dig = m.digest(since, limit=40)
    print(f"{len(dig)} digest lines")
    for ts, who, line in dig:
        print(f"[{time.strftime('%H:%M', time.localtime(ts))}] {who}: {line[:300]}")
    print("\n--- Transcript today (raw, last 100) ---")
    trans = m.transcript(since, limit=100)
    print(f"{len(trans)} transcript lines in last 24h")
    for ts, who, line in trans[-30:]:
        print(f"[{time.strftime('%H:%M', time.localtime(ts))}] {who}: {line[:300]}")
except Exception as e:
    print(f"memory error: {e}")
    import traceback; traceback.print_exc()

print("\n--- Counts currently kept ---")
try:
    import ongoing
    # ongoing state is in ongoing.json or similar
    import json, pathlib
    for p in ["ongoing.json", "D:\\funfact-bot\\ongoing.json"]:
        if pathlib.Path(p).exists():
            data = json.loads(pathlib.Path(p).read_text())
            print(f"From {p}: {data.get('tallies')}")
except Exception as e:
    print(f"ongoing error: {e}")
