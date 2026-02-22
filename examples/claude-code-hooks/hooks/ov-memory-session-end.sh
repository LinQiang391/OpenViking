#!/bin/bash
# ov-memory-session-end.sh
# Hook: SessionEnd
# On session end, create an OpenViking session, load all conversation messages,
# then commit — which archives and extracts memories automatically.

LOG=/tmp/ov-hooks.log

INPUT=$(cat)
TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // empty')
REASON=$(echo "$INPUT" | jq -r '.reason // "other"')

if [ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] SessionEnd/memory: no transcript (reason=$REASON)" >> "$LOG"
  exit 0
fi

MESSAGES=$(jq -sc '
  map(select(.type == "user" or .type == "assistant"))
  | map({
      role: .message.role,
      content: (
        .message.content
        | if type == "string" then .
          elif type == "array" then (map(select(.type == "text") | .text) | join("\n"))
          else ""
          end
      )
    })
  | map(select(.content != "" and .content != null))
' "$TRANSCRIPT")

COUNT=$(echo "$MESSAGES" | jq 'length')

if [ "$COUNT" -eq 0 ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] SessionEnd/memory: no messages (reason=$REASON)" >> "$LOG"
  exit 0
fi

# Create ov session and add messages (fast, no LLM needed)
OV_RAW=$(ov session new -o json -c 2>>"$LOG")
OV_SESSION_ID=$(echo "$OV_RAW" | jq -r '.result.session_id // empty')

if [ -z "$OV_SESSION_ID" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] SessionEnd/memory: failed to create ov session" >> "$LOG"
  exit 0
fi

while IFS= read -r msg; do
  ROLE=$(echo "$msg" | jq -r '.role')
  CONTENT=$(echo "$msg" | jq -r '.content')
  ov session add-message --role "$ROLE" --content "$CONTENT" "$OV_SESSION_ID" > /dev/null 2>&1
done < <(echo "$MESSAGES" | jq -c '.[]')

# Commit in background (slow, LLM extraction) — nohup survives parent exit
nohup bash -c "
  ov session commit '$OV_SESSION_ID' >> '$LOG' 2>&1
  echo \"[\$(date '+%Y-%m-%d %H:%M:%S')] SessionEnd/memory: committed $COUNT msgs (ov=$OV_SESSION_ID, reason=$REASON)\" >> '$LOG'
" > /dev/null 2>&1 &

echo "[$(date '+%Y-%m-%d %H:%M:%S')] SessionEnd/memory: queued commit $COUNT msgs (ov=$OV_SESSION_ID, reason=$REASON)" >> "$LOG"
