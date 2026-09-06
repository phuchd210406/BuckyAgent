#!/usr/bin/env bash
#
# The recording-day shape, end to end: a frontend on a PUBLIC origin talking to
# the backend on THIS laptop through a public tunnel. Everything that only
# breaks once the two are on different hosts -- the CORS preflight, cross-origin
# SSE (EventSource cannot send headers, so the server has to), the record fetch
# -- is checked here.
#
# Usage:  bash scripts/verify_tunnel.sh [FRONTEND_ORIGIN]
#
# Uses localhost.run, which needs no account. Swap the ssh line for
# `ngrok http 8000` if you would rather use ngrok; the checks are the same.
set -uo pipefail
cd "$(dirname "$0")/.."
OUT="$(mktemp -d)"
VERCEL="${1:-https://example.vercel.app}"
PY=./.venv/bin/python
[ -x "$PY" ] || PY=python3
FAIL=0
ok(){ echo "  ok   $*"; }; bad(){ echo "  FAIL $*"; FAIL=$((FAIL+1)); }

MOCK=1 REPRO_MOCK_SPEED=0.05 PYTHONPATH=src $PY -m uvicorn repro.api.main:app --port 8000 --log-level warning >$OUT/api_t.log 2>&1 &
API=$!
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ServerAliveInterval=15 \
    -R 80:localhost:8000 nokey@localhost.run >$OUT/tun.log 2>&1 &
TUN=$!
trap 'kill $API $TUN 2>/dev/null' EXIT

for _ in $(seq 1 40); do curl -fsS localhost:8000/healthz >/dev/null 2>&1 && break; sleep 0.5; done
for _ in $(seq 1 60); do grep -qE "https://[a-z0-9]+\.lhr\.life" $OUT/tun.log && break; sleep 1; done
URL=$(grep -oE "https://[a-z0-9]+\.lhr\.life" $OUT/tun.log | head -1)
echo "tunnel:  $URL  ->  this laptop:8000"
echo "origin:  $VERCEL (the real deployed frontend)"
echo

curl -fsS -m 20 "$URL/healthz" >/dev/null 2>&1 && ok "the backend answers through the public tunnel" || { bad "tunnel is not reaching the API"; exit 1; }

PRE=$(curl -s -i -m 20 -X OPTIONS "$URL/runs" -H "Origin: $VERCEL" \
      -H 'Access-Control-Request-Method: POST' -H 'Access-Control-Request-Headers: content-type')
echo "$PRE" | grep -qi "access-control-allow-origin: $VERCEL" \
  && ok "preflight allows the vercel.app origin (matched by regex, so preview URLs work too)" \
  || bad "preflight rejected the vercel origin"

BODY=$(curl -s -m 20 -X POST "$URL/runs" -H "Origin: $VERCEL" -H 'content-type: application/json' \
       -d '{"raw_text":"charged me postage over $50","repo_path":"fixtures/demo_repos/shopcart"}')
RUN=$(echo "$BODY" | $PY -c 'import json,sys; print(json.load(sys.stdin)["run_id"])' 2>/dev/null)
[ -n "$RUN" ] && ok "POST /runs across the tunnel -> $RUN" || { bad "POST failed: $BODY"; exit 1; }

timeout 90 curl -s -N -D $OUT/h.txt -H "Origin: $VERCEL" "$URL/runs/$RUN/events" > $OUT/sse.txt
grep -qi "access-control-allow-origin" $OUT/h.txt \
  && ok "the SSE stream carries CORS (EventSource cannot add headers, so it must)" \
  || bad "SSE has no CORS header"
N=$(grep -c '^data:' $OUT/sse.txt)
[ "$N" -ge 17 ] && ok "$N events streamed over the tunnel" || bad "only $N events"
grep -q '"type":"verdict"' $OUT/sse.txt && ok "reached the verdict" || bad "no verdict"
COST=$(grep -o '"usd":[0-9.]*' $OUT/sse.txt | tail -1)
[ -n "$COST" ] && ok "cost counter data present ($COST)" || bad "no usage on events"

REC=$(curl -s -m 20 -H "Origin: $VERCEL" "$URL/runs/$RUN")
echo "$REC" | grep -q '"verdict"' && ok "GET /runs/{id} returns the record" || bad "no record"

echo
[ "$FAIL" -eq 0 ] && echo "PASS - deployed origin -> public tunnel -> local backend works" || echo "$FAIL FAILED"
exit $FAIL
