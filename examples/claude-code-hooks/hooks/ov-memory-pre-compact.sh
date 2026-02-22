#!/bin/bash
# ov-memory-pre-compact.sh
# Hook: PreCompact
#
# WHAT: Snapshot the current conversation into OpenViking before context is compacted.
#
# PSEUDOCODE:
#   read stdin → transcript_path, trigger (manual|auto)
#   if no transcript → exit
#   parse transcript → keep user/assistant text messages only
#   if no messages → exit
#   write messages to tmpfile
#   log: ov add-memory (content truncated)
#   background: ov add-memory <tmpfile> → log result → rm tmpfile
#
# SPECIAL CASES:
#   trigger=auto   — context limit reached, Claude triggered compaction itself
#   trigger=manual — user ran /compact explicitly
#   nohup background — compaction may proceed before ov finishes; that's fine

LOG=/tmp/ov.log

_log()    { [ "$OV_HOOK_DEBUG" = "1" ] && echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }
_logcmd() { [ "$OV_HOOK_DEBUG" = "1" ] && printf "\033[90m%s\033[0m \033[35m%s\033[0m\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }
_trunc()  { printf '%s' "$1" | python3 -c "import sys; s=sys.stdin.read(); print(s[:120]+('...' if len(s)>120 else ''), end='')"; }

INPUT=$(cat)
TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // empty')
TRIGGER=$(echo "$INPUT" | jq -r '.trigger // "auto"')

if [ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ]; then
  _log "PreCompact: no transcript (trigger=$TRIGGER)"
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
  _log "PreCompact: nothing to snapshot (trigger=$TRIGGER)"
  exit 0
fi

TMPFILE=$(mktemp /tmp/ov-hook-XXXXXX.json)
echo "$MESSAGES" > "$TMPFILE"

_logcmd "ov add-memory '$(jq -c 'map(.content = (.content | if length > 120 then .[0:120] + "..." else . end))' "$TMPFILE")'"
nohup bash -c "
  ov add-memory \"\$(cat $TMPFILE)\" >> '$LOG' 2>&1
  [ \"\$OV_HOOK_DEBUG\" = '1' ] && echo \"[\$(date '+%Y-%m-%d %H:%M:%S')] PreCompact: snapshotted $COUNT msgs (trigger=$TRIGGER)\" >> '$LOG'
  rm -f '$TMPFILE'
" > /dev/null 2>&1 &

_log "PreCompact: queued $COUNT msgs (trigger=$TRIGGER)"
