import json, sys

session_file = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\johnny\.openclaw\agents\locomo-eval\sessions\4ac53d37-1843-49cd-8db6-01d40ea8a37c.jsonl"

with open(session_file, "r", encoding="utf-8") as f:
    lines = f.readlines()

print(f"Total lines: {len(lines)}")

for i, line in enumerate(lines):
    if not line.strip():
        continue
    data = json.loads(line.strip())
    msg_type = data.get("type", "")

    if msg_type == "message":
        msg = data.get("message", {})
        role = msg.get("role", "unknown")
        content = msg.get("content", [])

        if isinstance(content, list):
            for c in content:
                if not isinstance(c, dict):
                    continue
                ct = c.get("type", "")
                if ct == "toolCall":
                    name = c.get("name", "")
                    args = json.dumps(c.get("arguments", ""), ensure_ascii=False)[:200]
                    print(f"  Line {i} [{role}] TOOL_CALL: {name} args={args}")
                elif ct == "text":
                    text = c.get("text", "")[:200]
                    print(f"  Line {i} [{role}] TEXT: {text}")

        if role == "toolResult":
            tool_name = msg.get("toolName", "")
            tr_text = ""
            for c in msg.get("content", []):
                if isinstance(c, dict) and c.get("type") == "text":
                    tr_text = c.get("text", "")[:200]
            has_results = '"results": []' not in tr_text
            print(f"  Line {i} [TOOL_RESULT] {tool_name}: results={'YES' if has_results else 'EMPTY'} | {tr_text[:150]}")
    elif msg_type == "session":
        ts = data.get("timestamp", "")
        print(f"  Line {i} SESSION: id={data.get('id','')} ts={ts}")
    elif msg_type in ("model_change", "thinking_level_change", "custom"):
        pass
    else:
        print(f"  Line {i} type={msg_type}")
