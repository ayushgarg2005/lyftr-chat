#!/usr/bin/env bash
# Quick contract check against a running backend.
# Usage: ./scripts/verify.sh [base_url]     (default http://localhost:8000)
set -u
BASE="${1:-http://localhost:8000}"
S1="s-verify-$RANDOM$RANDOM"
S2="s-verify-$RANDOM$RANDOM"
MSG="In one sentence, what is a hash map? ($RANDOM)"

post() {  # session, message, [simulate header]
  if [ -n "${3:-}" ]; then
    curl -s -w '  [HTTP %{http_code}]\n' -X POST "$BASE/chat" -H 'Content-Type: application/json' \
      -H "X-Simulate-Failure: $3" -d "{\"session_id\":\"$1\",\"message\":\"$2\"}"
  else
    curl -s -w '  [HTTP %{http_code}]\n' -X POST "$BASE/chat" -H 'Content-Type: application/json' \
      -d "{\"session_id\":\"$1\",\"message\":\"$2\"}"
  fi
}

echo "== health";                         curl -s -w '  [HTTP %{http_code}]\n' "$BASE/health"
echo "== live call (expect LIVE_INFERENCE)"; post "$S1" "$MSG"
echo "== same first message, new session (expect LLM_CACHE)"; post "$S2" "$MSG"
echo "== follow-up in session 1";          post "$S1" "Now say it in five words."
echo "== key_1=429 (expect key_2)";        post "s-fo-$RANDOM" "Name a prime number. ($RANDOM)" "key_1=429"
echo "== key_1=500 (expect key_2)";        post "s-fo-$RANDOM" "Name a planet. ($RANDOM)" "key_1=500"
echo "== *=429 (expect 503)";              post "s-down-$RANDOM" "hello" "*=429"
echo "== empty message (expect 422)";      post "s-bad" "   "
echo "== history of session 1";            curl -s -w '  [HTTP %{http_code}]\n' "$BASE/sessions/$S1/messages"
echo "== unknown session (expect 404)";    curl -s -w '  [HTTP %{http_code}]\n' "$BASE/sessions/nope-$RANDOM/messages"
