#!/usr/bin/env bash
# Smoke test for a running api-gateway (default http://localhost:18080).
# Uses curl; WebSocket checks run only if `websocat` is installed.
set -uo pipefail
BASE="${API_URL:-http://localhost:${API_PORT:-18080}}"
WS="${BASE/http/ws}"
fail=0
check() { if [ "$1" = "$2" ]; then echo "ok   $3"; else echo "FAIL $3 (got '$1', want '$2')"; fail=1; fi; }

echo "== $BASE"
H=$(curl -s "$BASE/health"); echo "health: $H"
check "$(echo "$H" | grep -c '"status":"ok"')" 1 "GET /health"
check "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/nodes/hil")" 200 "GET /nodes/hil"
check "$(curl -s "$BASE/metrics" | grep -c '^gateway_ws_clients')" 1 "GET /metrics"
CODE=$(curl -s -o /tmp/iv.json -w '%{http_code}' -H 'Content-Type: application/json' \
  -d '{"kind":"toxin","target":{"type":"region","x":200,"y":125,"r":40},"params":{"intensity":0.8,"spread_ms":20000,"onset_ms":5000},"source":"script"}' \
  "$BASE/interventions")
check "$CODE" 202 "POST /interventions (valid)"; cat /tmp/iv.json; echo
check "$(curl -s -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -d '{"kind":"laser","target":{"type":"all"}}' "$BASE/interventions")" 400 "POST /interventions (bad kind)"
check "$(curl -s -o /dev/null -w '%{http_code}' -H 'Origin: http://localhost:3000' -X OPTIONS -H 'Access-Control-Request-Method: POST' "$BASE/interventions")" 200 "CORS preflight"
if command -v websocat >/dev/null 2>&1; then
  HELLO=$(timeout 3 websocat -n1 -t "$WS/ws/grid" 2>/dev/null | head -1)
  check "$(echo "$HELLO" | grep -c '"type":"hello"')" 1 "WS /ws/grid hello"
  FEAT=$(timeout 3 websocat -n1 -t "$WS/ws/node/80125" 2>/dev/null | head -1)
  check "$(echo "$FEAT" | grep -c '"type":"features"')" 1 "WS /ws/node/80125 features"
else
  echo "skip websocat not installed (WS checks)"
fi
[ $fail -eq 0 ] && echo "SMOKE PASS" || { echo "SMOKE FAIL"; exit 1; }
