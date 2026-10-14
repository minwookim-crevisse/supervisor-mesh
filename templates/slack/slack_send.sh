#!/usr/bin/env bash
# supervisor-mesh Slack sender — posts the CONTENT OF A FILE to the channel (no shell-escaping of message text).
# Config is read at RUNTIME from the project's local .env (gitignored):
#   SLACK_BOT_TOKEN=<token with chat:write>   SLACK_CHANNEL_ID=<channel id>
# usage: scripts/slack/slack_send.sh <message-file> [path/to/.env]
# Policy: aggregates/counts only — never real customer data, individual amounts, secrets or tokens.
set -euo pipefail
MSG_FILE="${1:?usage: slack_send.sh <message-file> [.env]}"
ENV_FILE="${2:-.env}"
read_env() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d "\"'\r" || true; }

[ -f "$MSG_FILE" ] || { echo "slack_send: $MSG_FILE not found" >&2; exit 1; }
TOKEN="$(read_env SLACK_BOT_TOKEN)"
CHANNEL="$(read_env SLACK_CHANNEL_ID)"
[ -n "$TOKEN" ] && [ -n "$CHANNEL" ] || { echo "slack_send: SLACK_BOT_TOKEN / SLACK_CHANNEL_ID missing in $ENV_FILE" >&2; exit 1; }

PAYLOAD="$(python3 -c 'import json,sys; print(json.dumps({"channel": sys.argv[1], "text": open(sys.argv[2], encoding="utf-8").read()}))' "$CHANNEL" "$MSG_FILE")"
RESP="$(curl -sS -X POST https://slack.com/api/chat.postMessage \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json; charset=utf-8" \
  --data-binary "$PAYLOAD")"
printf '%s' "$RESP" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("sent ts=" + d["ts"]) if d.get("ok") else (print("slack_send error: " + str(d.get("error")), file=sys.stderr), sys.exit(1))'
