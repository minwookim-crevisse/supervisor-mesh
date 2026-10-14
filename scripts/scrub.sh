#!/usr/bin/env bash
# Scrub gate: the repo must contain no secrets, tokens, personal paths, or project-specific residue.
# Generic patterns live here; extra literal strings (e.g. a former project's store id) go in an
# untracked .scrub-local file, one per line, so the literals themselves never enter the repo.
# exit 0 = clean · 1 = hits found
set -uo pipefail
cd "$(dirname "$0")/.."
SELF="scripts/scrub.sh"
FILES=$(git ls-files --cached --others --exclude-standard | grep -vxF "$SELF")

declare -A PATTERNS=(
  [slack-token]='xox[abprs]-[A-Za-z0-9-]{8,}'
  [anthropic-key]='sk-ant-[A-Za-z0-9_-]{8,}'
  [openai-key]='sk-[A-Za-z0-9]{32,}'
  [github-token]='gh[pousr]_[A-Za-z0-9]{20,}'
  [aws-key]='AKIA[0-9A-Z]{16}'
  [jwt]='eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.'
  [private-key]='-----BEGIN [A-Z ]*PRIVATE KEY-----'
  [slack-channel-id]='\bC0[A-Z0-9]{8,}\b'
  [supabase-url]='[a-z]{20}\.supabase\.co'
  [env-assignment]='^(SLACK_BOT_TOKEN|SLACK_CHANNEL_ID|[A-Z_]*(SECRET|PASSWORD|API_KEY))=[^[:space:]<]+'
  [short-dec-id]='\bDEC-[0-9]{1,3}\b'
  [home-path]='/home/[a-z][a-z0-9_-]+/|C:\\\\Users\\\\'
  [email]='[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.(com|net|org|io|kr)\b'
  [hangul]='[\x{AC00}-\x{D7A3}]'
)

hits=0
for name in "${!PATTERNS[@]}"; do
  out=$(printf '%s\n' "$FILES" | xargs -r -d '\n' grep -nIP -- "${PATTERNS[$name]}" 2>/dev/null)
  if [ -n "$out" ]; then echo "SCRUB HIT [$name]:"; echo "$out" | sed 's/^/  /'; hits=1; fi
done
if [ -f .scrub-local ]; then
  while IFS= read -r lit; do
    [ -z "$lit" ] && continue
    out=$(printf '%s\n' "$FILES" | xargs -r -d '\n' grep -nIF -- "$lit" 2>/dev/null)
    if [ -n "$out" ]; then echo "SCRUB HIT [local-literal]:"; echo "$out" | sed 's/^/  /'; hits=1; fi
  done < .scrub-local
fi
[ "$hits" -eq 0 ] && echo "scrub: 0 hits"
exit "$hits"
