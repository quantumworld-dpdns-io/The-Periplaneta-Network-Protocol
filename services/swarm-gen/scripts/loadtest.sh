#!/usr/bin/env bash
# Load test: run swarm-gen for DURATION_S (60) at SWARM_NODES (100k) against
# localhost:19092, then verify via rpk that >= 95 % of the expected frames
# landed in swarm.bins and print consumer-group lag for feature-worker.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRATE="$(cd "$HERE/.." && pwd)"
BROKERS="${KAFKA_BROKERS:-localhost:19092}"
DURATION_S="${DURATION_S:-60}"
NODES="${SWARM_NODES:-100000}"
SHARDS="${SWARM_SHARDS:-100}"
BIN_MS="${BIN_MS:-100}"
METRICS_PORT="${METRICS_PORT:-9100}"
RPK="docker run --rm --network host docker.redpanda.com/redpandadata/redpanda:v24.2.7"
export NO_COLOR=1

hwm_sum() {
  # sum of HIGH-WATERMARK across partitions of a topic (0 if topic missing)
  $RPK topic describe swarm.bins -p --brokers "$BROKERS" 2>/dev/null \
    | awk 'NR>1 && $1 ~ /^[0-9]+$/ { s += $NF } END { print s+0 }'
}

echo "== loadtest: $NODES nodes / $SHARDS shards / ${BIN_MS} ms bins for ${DURATION_S}s -> $BROKERS"
BEFORE=$(hwm_sum)
echo "swarm.bins high-watermark before: $BEFORE"

echo "== building swarm-gen (release)"
(cd "$CRATE" && cargo build --release -q) || { echo "build failed"; exit 2; }
BIN="$CRATE/target/release/swarm-gen"; [ -x "$BIN" ] || BIN="$BIN.exe"

START=$(date +%s.%N)
SWARM_NODES="$NODES" SWARM_SHARDS="$SHARDS" BIN_MS="$BIN_MS" KAFKA_BROKERS="$BROKERS" METRICS_PORT="$METRICS_PORT" \
  "$BIN" --duration-s "$DURATION_S" 2>&1 | grep -E "sim |kafka |behind|error|done" &
GEN=$!
# scrape metrics shortly before the run ends (latency histogram lives in-process)
sleep $(( DURATION_S > 5 ? DURATION_S - 3 : 1 ))
METRICS=$(curl -s "http://localhost:$METRICS_PORT/metrics" 2>/dev/null || true)
wait $GEN
END=$(date +%s.%N)
ELAPSED=$(awk -v a="$START" -v b="$END" 'BEGIN { printf "%.1f", b - a }')

AFTER=$(hwm_sum)
PRODUCED=$(( AFTER - BEFORE ))
EXPECTED=$(( DURATION_S * 1000 / BIN_MS * SHARDS ))
MIN=$(( EXPECTED * 95 / 100 ))
RATE=$(awk -v p="$PRODUCED" -v e="$ELAPSED" 'BEGIN { if (e > 0) printf "%.0f", p / e; else print 0 }')

echo "== results"
echo "frames in swarm.bins: $PRODUCED (expected $EXPECTED, min $MIN) in ${ELAPSED}s => ~${RATE} frames/s"
if [ -n "$METRICS" ]; then
  echo "$METRICS" | awk '
    /^swarm_produce_latency_seconds_sum/  { s=$2 } /^swarm_produce_latency_seconds_count/ { c=$2 }
    /^swarm_sim_step_seconds_sum/ { ss=$2 }  /^swarm_sim_step_seconds_count/ { sc=$2 }
    /^swarm_produce_errors_total/ { e=$2 }  /^swarm_sim_lag_ms/ { lag=$2 }
    END { if (c>0) printf "mean produce latency (bin -> all shards acked): %.1f ms\n", 1000*s/c;
          if (sc>0) printf "mean sim step: %.2f ms\n", 1000*ss/sc;
          printf "produce errors: %d, sim lag at end: %d ms\n", e+0, lag+0 }'
  echo "$METRICS" | grep -E '^swarm_produce_latency_seconds_bucket\{le="(0.01|0.05|0.1|0.5)"\}' | sed 's/^/  /'
fi

echo "== consumer group feature-worker (lag)"
$RPK group describe feature-worker --brokers "$BROKERS" 2>&1 | grep -v -E "^\s*$" | head -25 || true
echo "== topic swarm.bins"
$RPK topic describe swarm.bins --brokers "$BROKERS" 2>&1 | head -12 || true

if [ "$PRODUCED" -lt "$MIN" ]; then
  echo "FAIL: produced $PRODUCED < $MIN"
  exit 1
fi
echo "PASS"
