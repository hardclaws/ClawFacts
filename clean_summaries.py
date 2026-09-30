import sqlite3
db = sqlite3.connect("chat_memory.db")
cur = db.cursor()
# Find bad ones
bad_phrases = ["We need to extract from the chat lines", "Write up to", "who was there and who said or did what", "Chat from the last slice"]
rows = cur.execute("SELECT rowid, text FROM summaries").fetchall()
to_delete = []
for rowid, text in rows:
    low = (text or "").lower()
    if any(p.lower() in low for p in bad_phrases):
        to_delete.append(rowid)
print(f"Found {len(to_delete)} bad summaries to delete")
if to_delete:
    cur.execute(f"DELETE FROM summaries WHERE rowid IN ({','.join('?'*len(to_delete))})", to_delete)
    db.commit()
    print(f"Deleted {cur.rowcount}")
else:
    print("No bad summaries found")
