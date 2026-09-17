#!/usr/bin/env bash
# Scripted demo: toxin wave on the twin grid, HIL node 0 to toxin, then drug recovery.
set -euo pipefail
API="${NEXT_PUBLIC_API_URL:-http://localhost:18080}"

post() {
  curl -sS -X POST "$API/interventions" -H 'content-type: application/json' -d "$1" && echo
}

echo "health:"; curl -sS "$API/health"; echo
echo "[1/4] HIL node 0 -> toxin"
post '{"kind":"toxin","target":{"type":"node","node_id":4294901760},"params":{"intensity":0.8,"spread_ms":0,"onset_ms":0},"source":"script"}'
sleep 5
echo "[2/4] toxin wave at grid centre"
post '{"kind":"toxin","target":{"type":"region","x":200,"y":125,"r":40},"params":{"intensity":0.8,"spread_ms":20000,"onset_ms":3000},"source":"script"}'
sleep 40
echo "[3/4] drug on same region"
post '{"kind":"drug","target":{"type":"region","x":200,"y":125,"r":40},"params":{"intensity":1.0,"spread_ms":15000,"onset_ms":0},"source":"script"}'
sleep 5
echo "[4/4] HIL node 0 -> baseline"
post '{"kind":"baseline","target":{"type":"node","node_id":4294901760},"params":{"intensity":1.0,"spread_ms":0,"onset_ms":0},"source":"script"}'
echo "done"
