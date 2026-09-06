#!/usr/bin/env bash
#
# Local proof of the AgentCore contract: start the server, POST one report, get
# a verdict back, POST rubbish and get a 400 shape back, GET /ping, stop.
#
# Needs no AWS credentials and touches no repository: LLM_PROVIDER=stub wires a
# scripted model and a sandbox that runs nothing (see agent.py). It proves the
# wiring, not the agent. Non-zero exit on any failure.
set -euo pipefail

cd "$(dirname "$0")/.."

# Same interpreter rule as the Makefile: prefer the venv, never a bare `python`.
PY=./.venv/bin/python
[ -x "$PY" ] || PY=python3
export PYTHONPATH=src
export LLM_PROVIDER=stub

PORT="${PORT:-8080}"
BASE="http://127.0.0.1:${PORT}"
SERVER_LOG="$(mktemp -t smoke_agentcore.XXXXXX.log)"
SERVER_PID=""

cleanup() {
  local status=$?
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  if [ "$status" -ne 0 ]; then
    echo "--- server log ---" >&2
    cat "$SERVER_LOG" >&2 || true
  fi
  rm -f "$SERVER_LOG"
  exit "$status"
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

echo "starting agentcore server on :${PORT} (LLM_PROVIDER=stub)"
"$PY" src/repro/agentcore/agent.py >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 100); do
  if curl -fsS "${BASE}/ping" >/dev/null 2>&1; then
    break
  fi
  kill -0 "$SERVER_PID" 2>/dev/null || fail "server exited before it was ready"
  sleep 0.2
done
curl -fsS "${BASE}/ping" >/dev/null 2>&1 || fail "server never became ready on ${BASE}/ping"

# 1. A real report comes back with a verdict.
echo "POST /invocations"
RESPONSE="$(curl -fsS -X POST "${BASE}/invocations" \
  -H 'Content-Type: application/json' \
  -d '{"report": {"run_id": "smoke-1", "raw_text": "Checkout charged me twice.",
       "channel": "email", "repo_path": "/tmp"}}')" || fail "POST /invocations failed"
echo "$RESPONSE"

echo "$RESPONSE" | "$PY" -c '
import json, sys
body = json.load(sys.stdin)
verdict = body.get("verdict")
assert verdict, f"no verdict in response: {body}"
assert "handover" in body, f"no handover key in response: {body}"
assert body.get("run_id") == "smoke-1", f"wrong run_id: {body}"
print(f"  verdict={verdict}")
' || fail "response did not contain a verdict"

# 2. Rubbish comes back as a 400 shape, not a stack trace.
echo "POST /invocations (bad payload)"
BAD="$(curl -fsS -X POST "${BASE}/invocations" \
  -H 'Content-Type: application/json' \
  -d '{"report": {"raw_text": "no run_id, no repo_path"}}')" || fail "bad-payload POST failed"

echo "$BAD" | "$PY" -c '
import json, sys
body = json.load(sys.stdin)
assert body.get("status") == 400, f"expected a 400-shaped error, got: {body}"
assert "verdict" not in body, f"a rejected payload must not carry a verdict: {body}"
assert body.get("detail"), f"a 400 must say what was wrong: {body}"
count = len(body["detail"])
print(f"  rejected {count} field(s)")
' || fail "bad payload was not rejected with a 400 shape"

# 3. /ping answers.
echo "GET /ping"
PING="$(curl -fsS "${BASE}/ping")" || fail "GET /ping failed"
echo "$PING" | "$PY" -c '
import json, sys
body = json.load(sys.stdin)
assert body.get("status"), f"no status in ping: {body}"
status = body["status"]
print(f"  status={status}")
' || fail "/ping did not report a status"

echo "OK: smoke passed, stopping server"
