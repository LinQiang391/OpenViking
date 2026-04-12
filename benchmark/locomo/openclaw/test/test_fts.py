import sqlite3

db_path = r"C:\Users\johnny\.openclaw\memory\locomo-eval.sqlite"
conn = sqlite3.connect(db_path)
cur = conn.cursor()

print("=== FTS search for 'Caroline' ===")
results = cur.execute(
    "SELECT id, path, snippet(chunks_fts, 0, '>>>', '<<<', '...', 20) "
    "FROM chunks_fts WHERE chunks_fts MATCH 'Caroline' LIMIT 5"
).fetchall()
print(f"Found {len(results)} FTS results")
for r in results:
    print(f"  id={r[0][:20]}... path={r[1]} snippet={r[2][:100]}...")

print("\n=== FTS search for 'writing career' ===")
results = cur.execute(
    "SELECT id, path, snippet(chunks_fts, 0, '>>>', '<<<', '...', 20) "
    "FROM chunks_fts WHERE chunks_fts MATCH 'career' LIMIT 5"
).fetchall()
print(f"Found {len(results)} FTS results")
for r in results:
    print(f"  id={r[0][:20]}... path={r[1]} snippet={r[2][:100]}...")

print("\n=== Check chunks_vec_info ===")
try:
    info = cur.execute("SELECT * FROM chunks_vec_info").fetchall()
    print(f"chunks_vec_info: {info}")
except Exception as e:
    print(f"Error: {e}")

print("\n=== Check chunks_vec_rowids ===")
rowids = cur.execute("SELECT count(*) FROM chunks_vec_rowids").fetchone()
print(f"chunks_vec_rowids count: {rowids[0]}")
rows = cur.execute("SELECT * FROM chunks_vec_rowids LIMIT 5").fetchall()
for r in rows:
    print(f"  rowid={r}")

print("\n=== Check meta ===")
meta = cur.execute("SELECT * FROM meta").fetchone()
print(f"Meta key: {meta[0]}")
print(f"Meta value: {meta[1]}")

conn.close()
