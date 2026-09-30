#!/usr/bin/env python3
"""
Redact moderation words from today's dump so you can paste here.
Usage:
  python redact_dump.py
  python redact_dump.py --words "word1,word2,word3"
  python redact_dump.py --words-file banned.txt

If no list given, it tries:
  - banned_words.txt
  - moderation_words.txt
  - config.json -> banned_words / blocked_words / moderation_terms
  - else just redacts nothing (you paste your list)
"""
import argparse, json, pathlib, re, sqlite3, time

def load_words(args):
    words = []
    if args.words:
        words += [w.strip() for w in args.words.split(",") if w.strip()]
    if args.words_file:
        p = pathlib.Path(args.words_file)
        if p.exists():
            words += [l.strip() for l in p.read_text().splitlines() if l.strip()]
    # try common files
    for name in ["banned_words.txt", "moderation_words.txt", "blocked.txt", "banned.txt"]:
        p = pathlib.Path(name)
        if p.exists() and not args.words_file:
            words += [l.strip() for l in p.read_text().splitlines() if l.strip()]
    # try config.json
    try:
        cfg = json.loads(pathlib.Path("config.json").read_text())
        for k in ["banned_words", "blocked_words", "moderation_terms", "banned", "filter_words"]:
            if k in cfg and isinstance(cfg[k], list):
                words += cfg[k]
    except Exception:
        pass
    # dedup, keep longest first for regex
    words = sorted(set(w for w in words if w), key=len, reverse=True)
    return words

def redact(text, words):
    if not words:
        return text
    # build regex \bword\b case-insensitive, but also substring for safety if word contains spaces
    # escape each
    pat = re.compile("|".join(re.escape(w) for w in words), re.IGNORECASE)
    return pat.sub("***", text)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--words", help="comma separated list to redact")
    ap.add_argument("--words-file", help="file with one word per line")
    ap.add_argument("--hours", type=float, default=24, help="hours back, default 24")
    ap.add_argument("--limit", type=int, default=50, help="digest limit")
    args = ap.parse_args()

    words = load_words(args)
    print(f"[redact] {len(words)} words loaded to redact: {words[:20]}{'...' if len(words)>20 else ''}")
    if not words:
        print("[redact] No word list found. Use --words \"bad1,bad2\" or create banned_words.txt")

    db_path = pathlib.Path("chat_memory.db")
    if not db_path.exists():
        print(f"No db at {db_path}")
        return
    db = sqlite3.connect(str(db_path))
    since = time.time() - args.hours*3600
    print(f"\n=== Slices last {args.hours}h ===")
    rows = db.execute("SELECT ts_from, ts_to, text FROM summaries WHERE ts_to >= ? ORDER BY ts_to ASC", (since,)).fetchall()
    print(f"{len(rows)} slices")
    for a,b,t in rows:
        t2 = redact(t, words)
        print(f"[{time.strftime('%m/%d %H:%M', time.localtime(a))}-{time.strftime('%H:%M', time.localtime(b))}] {t2[:600]}")

    print(f"\n=== Digest last {args.hours}h (what recap reads, {args.limit} lines) ===")
    try:
        import memory
        m = memory.Memory(str(db_path))
        dig = m.digest(since, limit=args.limit)
        print(f"{len(dig)} lines")
        for ts, who, line in dig:
            line2 = redact(line, words)
            who2 = redact(who, words)
            print(f"[{time.strftime('%H:%M', time.localtime(ts))}] {who2}: {line2[:400]}")
        print(f"\n=== Transcript last {args.hours}h (last 40) ===")
        trans = m.transcript(since, limit=100)
        for ts, who, line in trans[-40:]:
            print(f"[{time.strftime('%H:%M', time.localtime(ts))}] {redact(who, words)}: {redact(line, words)[:400]}")
    except Exception as e:
        print(f"memory error {e}")
        import traceback; traceback.print_exc()

if __name__ == "__main__":
    main()
