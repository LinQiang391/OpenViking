import json

with open(r"C:\Users\johnny\.openclaw\agents\locomo-eval\sessions\sessions.json", "r", encoding="utf-8") as f:
    data = json.load(f)

for key, value in sorted(data.items()):
    if isinstance(value, dict):
        sf = value.get("sessionFile", "?")
        print(f"  {key} -> {sf}")
