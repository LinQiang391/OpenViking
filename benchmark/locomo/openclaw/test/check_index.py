import sqlite3
import sys

db_path = r"C:\Users\johnny\.openclaw\memory\locomo-eval.sqlite"
conn = sqlite3.connect(db_path)
cur = conn.cursor()

tables = cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("Tables:", [t[0] for t in tables])

for t in tables:
    name = t[0]
    count = cur.execute(f"SELECT COUNT(*) FROM [{name}]").fetchone()[0]
    print(f"  {name}: {count} rows")
    cols = cur.execute(f"PRAGMA table_info([{name}])").fetchall()
    print(f"    columns: {[c[1] for c in cols]}")
    if count > 0 and count <= 50:
        rows = cur.execute(f"SELECT * FROM [{name}] LIMIT 3").fetchall()
        for r in rows:
            display = []
            for i, val in enumerate(r):
                if isinstance(val, bytes):
                    display.append(f"<blob {len(val)} bytes>")
                elif isinstance(val, str) and len(val) > 200:
                    display.append(val[:200] + "...")
                else:
                    display.append(val)
            print(f"    row: {display}")

conn.close()
