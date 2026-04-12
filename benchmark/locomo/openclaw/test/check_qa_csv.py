import csv

csv_path = r"G:\gitcode\OpenViking\benchmark\locomo\openclaw\result\qa_results.csv"
with open(csv_path, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    rows = list(reader)

print(f"Headers: {list(rows[0].keys())}")
print(f"Total rows: {len(rows)}")

qi_set = set()
for r in rows:
    qi = r.get("question_index", "")
    qi_set.add(qi)

print(f"Unique question_index values: {sorted(qi_set, key=lambda x: int(x) if x.isdigit() else 0)}")
print(f"Count: {len(qi_set)}")

jsonl_files = [r.get("jsonl_file", "") for r in rows]
missing_jsonl = [i for i, j in enumerate(jsonl_files) if not j or j == ""]
print(f"\nRows without jsonl_file: {len(missing_jsonl)}")
for idx in missing_jsonl[:10]:
    r = rows[idx]
    print(f"  Row {idx}: qi={r.get('question_index')}, question={r.get('question', '')[:80]}")
