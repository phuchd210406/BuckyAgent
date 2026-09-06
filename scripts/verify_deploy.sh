#!/usr/bin/env bash
#
# The deployed shape, verified locally: a frontend served from ONE origin
# talking to the API on ANOTHER. That is exactly what Vercel + a tunnel is, so
# everything that can break about it -- the baked-in API base, the CORS
# preflight, cross-origin SSE -- breaks here first, for free, before recording
# day.
#
# Usage:  bash scripts/verify_deploy.sh [API_BASE]
#         API_BASE defaults to http://127.0.0.1:8000; pass an ngrok URL to run
#         the same checks against the real tunnel.
set -uo pipefail

cd "$(dirname "$0")/.."
PY=./.venv/bin/python
[ -x "$PY" ] || PY=python3

API_BASE="${1:-http://127.0.0.1:8000}"
WEB_ORIGIN="${WEB_ORIGIN:-http://localhost:4173}"
WEB_PORT="${WEB_PORT:-4173}"
LOCAL_API="${API_BASE#http://127.0.0.1:}"
API_PID=""; WEB_PID=""
FAILURES=0

log()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m   %s\n' "$*"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAILURES=$((FAILURES+1)); }

cleanup() {
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null
  [ -n "$WEB_PID" ] && kill "$WEB_PID" 2>/dev/null
  wait 2>/dev/null
  return 0
}
trap cleanup EXIT

# 1. The API, on its own origin, allowing the web origin.
if [ "$LOCAL_API" != "$API_BASE" ]; then
  log "starting the API on $API_BASE (MOCK=1)"
  MOCK=1 REPRO_MOCK_SPEED=0.02 PYTHONPATH=src \
    REPRO_CORS_ORIGINS="$WEB_ORIGIN" \
    $PY -m uvicorn repro.api.main:app --port "${LOCAL_API%%/*}" --log-level warning &
  API_PID=$!
  for _ in $(seq 1 60); do curl -fsS "$API_BASE/healthz" >/dev/null 2>&1 && break; sleep 0.25; done
fi
curl -fsS "$API_BASE/healthz" >/dev/null 2>&1 && ok "API answers at $API_BASE" || { bad "API is not up at $API_BASE"; exit 1; }

# 2. The frontend, built to talk to that origin rather than to a dev proxy.
log "building the frontend with VITE_API_BASE=$API_BASE"
(cd web && VITE_API_BASE="$API_BASE" npm run build >/dev/null 2>&1) \
  && ok "vite build" || { bad "vite build"; exit 1; }
if grep -rq "$API_BASE" web/dist/assets/*.js; then
  ok "the API base is baked into the bundle"
else
  bad "VITE_API_BASE did not reach the bundle -- the deployed app would call its own origin"
fi

log "serving the built frontend on $WEB_ORIGIN"
(cd web && npx vite preview --port "$WEB_PORT" --strictPort >/dev/null 2>&1) &
WEB_PID=$!
for _ in $(seq 1 60); do curl -fsS "$WEB_ORIGIN/" >/dev/null 2>&1 && break; sleep 0.25; done
curl -fsS "$WEB_ORIGIN/" >/dev/null 2>&1 && ok "frontend served" || bad "frontend not served"

# 3. The three cross-origin requests a browser actually makes.
log "cross-origin checks, as the browser makes them (Origin: $WEB_ORIGIN)"

PREFLIGHT=$(curl -s -i -X OPTIONS "$API_BASE/runs" \
  -H "Origin: $WEB_ORIGIN" \
  -H 'Access-Control-Request-Method: POST' \
  -H 'Access-Control-Request-Headers: content-type')
echo "$PREFLIGHT" | grep -qi "access-control-allow-origin: $WEB_ORIGIN" \
  && ok "preflight OPTIONS /runs is allowed" \
  || bad "preflight has no matching access-control-allow-origin (the POST would be blocked)"

POST=$(curl -s -i -X POST "$API_BASE/runs" \
  -H "Origin: $WEB_ORIGIN" -H 'content-type: application/json' \
  -d '{"raw_text":"deploy check","repo_path":"fixtures/demo_repos/shopcart"}')
echo "$POST" | grep -qi "access-control-allow-origin" \
  && ok "POST /runs carries the CORS header" \
  || bad "POST /runs has no CORS header"
RUN_ID=$(echo "$POST" | tail -1 | $PY -c 'import json,sys; print(json.load(sys.stdin)["run_id"])' 2>/dev/null)
[ -n "$RUN_ID" ] && ok "run started: $RUN_ID" || bad "no run_id came back"

if [ -n "$RUN_ID" ]; then
  SSE=$(curl -s -N --max-time 30 -D /tmp/sse_headers.$$ \
        -H "Origin: $WEB_ORIGIN" "$API_BASE/runs/$RUN_ID/events")
  grep -qi "access-control-allow-origin" /tmp/sse_headers.$$ \
    && ok "SSE stream carries the CORS header" \
    || bad "SSE has no CORS header -- EventSource cannot set one, so this must come from the server"
  COUNT=$(echo "$SSE" | grep -c '^data:')
  [ "$COUNT" -gt 0 ] && ok "$COUNT events received cross-origin" || bad "no events received"
  echo "$SSE" | grep -q '"type":"verdict"' && ok "the stream reached its verdict" || bad "no verdict in the stream"
  echo "$SSE" | grep -q '"usage"' && ok "events carry the running cost" || bad "no usage on the events"
  rm -f /tmp/sse_headers.$$

  RECORD=$(curl -s -H "Origin: $WEB_ORIGIN" "$API_BASE/runs/$RUN_ID")
  echo "$RECORD" | grep -q '"verdict"' && ok "GET /runs/{id} returns the record" || bad "no record served"
fi

log "$([ "$FAILURES" -eq 0 ] && echo 'PASS — the deployed shape works cross-origin' || echo "$FAILURES CHECK(S) FAILED")"
exit "$FAILURES"
