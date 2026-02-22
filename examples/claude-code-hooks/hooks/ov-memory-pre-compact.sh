#!/bin/bash
# ov-memory-pre-compact.sh
# Hook: PreCompact
# Before context compaction, snapshot the conversation into OpenViking memory.

LOG=/tmp/ov-hooks.log

INPUT=$(cat)
TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // empty')
TRIGGER=$(echo "$INPUT" | jq -r '.trigger // "auto"')

if [ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] PreCompact/memory: no transcript (trigger=$TRIGGER)" >> "$LOG"
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
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] PreCompact/memory: nothing to snapshot (trigger=$TRIGGER)" >> "$LOG"
  exit 0
fi

TMPFILE=$(mktemp /tmp/ov-hook-XXXXXX.json)
echo "$MESSAGES" > "$TMPFILE"

nohup bash -c "
  ov add-memory \"\$(cat $TMPFILE)\" >> '$LOG' 2>&1
  echo \"[\$(date '+%Y-%m-%d %H:%M:%S')] PreCompact/memory: snapshotted $COUNT msgs before $TRIGGER compaction\" >> '$LOG'
  rm -f '$TMPFILE'
" > /dev/null 2>&1 &

echo "[$(date '+%Y-%m-%d %H:%M:%S')] PreCompact/memory: queued $COUNT msgs (trigger=$TRIGGER)" >> "$LOG"
