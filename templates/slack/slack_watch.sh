#!/usr/bin/env bash
# supervisor-mesh Slack watcher — emits each NEW human (non-bot) message in one channel as one stdout line.
# Arm it with the host's background Monitor; each line re-invokes the session.
#
# Config is read at RUNTIME from the project's local .env (gitignored). Nothing secret lives in this file.
#   SLACK_BOT_TOKEN=<token with channels:history>      (never commit; never echo)
#   SLACK_CHANNEL_ID=<channel id>
#   SLACK_POLL_SECONDS=20                              (optional)
# usage: scripts/slack/slack_watch.sh [path/to/.env]
set -uo pipefail
ENV_FILE="${1:-.env}"
STATE_DIR="${SLACK_STATE_DIR:-${TMPDIR:-/tmp}/supervisor-mesh-slack}"

read_env() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d "\"'\r" || true; }

[ -f "$ENV_FILE" ] || { echo "slack_watch: $ENV_FILE not found" >&2; exit 1; }
TOKEN="$(read_env SLACK_BOT_TOKEN)"
CHANNEL="$(read_env SLACK_CHANNEL_ID)"
POLL="$(read_env SLACK_POLL_SECONDS)"; POLL="${POLL:-20}"
[ -n "$TOKEN" ] && [ -n "$CHANNEL" ] || { echo "slack_watch: SLACK_BOT_TOKEN / SLACK_CHANNEL_ID missing in $ENV_FILE" >&2; exit 1; }

mkdir -p "$STATE_DIR"; chmod 700 "$STATE_DIR"
TS_FILE="$STATE_DIR/last_ts"
python3 -c "import time,sys; open(sys.argv[1],'w').write(f'{time.time():.6f}')" "$TS_FILE"

while true; do
  RESP="$(curl -sS -G -H "Authorization: Bearer $TOKEN" \
    --data-urlencode "channel=$CHANNEL" --data-urlencode "oldest=$(cat "$TS_FILE")" --data-urlencode "limit=20" \
    https://slack.com/api/conversations.history 2>/dev/null || true)"
  printf '%s' "$RESP" | TS_FILE="$TS_FILE" python3 -c '
import json, os, sys
tsf = os.environ["TS_FILE"]
try:
    last = float(open(tsf).read() or 0)
except Exception:
    last = 0.0
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit()
if not data.get("ok", False):
    print("[slack_watch error] " + str(data.get("error", "unknown")), file=sys.stderr)
    sys.exit()
newest = last
for m in sorted(data.get("messages", []), key=lambda x: float(x.get("ts", "0") or 0)):
    ts = float(m.get("ts", "0") or 0)
    if ts <= last:
        continue
    newest = max(newest, ts)
    if m.get("bot_id") or m.get("subtype"):
        continue  # skip bot/system messages (including our own posts)
    print("[Slack] " + (m.get("text", "")[:1800]).replace("\n", " "), flush=True)
open(tsf, "w").write(f"{newest:.6f}")
' || true
  sleep "$POLL"
done
