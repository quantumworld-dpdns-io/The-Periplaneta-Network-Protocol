# WIRE.md — Shared contract for Cockroach Internet

Every service codes against this file. Change it only through a reviewed PR;
agents must not edit it. All multi-byte integers are **little-endian**.
All `t_ms` fields are Unix epoch milliseconds (UTC) assigned by the producer.

## 1. Node identity and grid

- `node_id: u32`.
- **Virtual twins**: `0 .. SWARM_NODES-1`. Grid position:
  `x = node_id % GRID_W`, `y = node_id / GRID_W` (defaults `GRID_W=400`,
  `GRID_H=250` → 100,000 nodes).
- **HIL (physical ESP32) nodes**: `0xFFFF0000 + hil_index` (`hil_index` 0,1).
  They are *not* in the grid; the dashboard renders them as separate badges.
  Text form (`node_hex`) = 8 lowercase hex chars, e.g. `ffff0000`.
- Sharding: `shard = node_id / 1000` for twins (`SWARM_SHARDS=100`). HIL
  nodes use shard `0xFFFF`.

## 2. Enums

```
mode / state (u8):
  0 = baseline   1 = toxin   2 = drug   3 = radiation
  4 = offline    5 = collapsed   (4,5 only in feature/alert outputs)
detector (string): "zscore" | "gru"
```

## 3. Kafka / Redpanda topics

| Topic | Key | Value | Partitions | Producer → Consumer |
|---|---|---|---|---|
| `hil.raw` | `node_hex` | `HilRawFrame` (binary) | 2 | mqtt-bridge → feature-worker, api-gateway |
| `swarm.bins` | shard as decimal string | `SwarmBinFrame` (binary) | 16 | swarm-gen → feature-worker |
| `swarm.truth` | `intervention_id` | JSON (§6) | 1 | swarm-gen → ml eval, api-gateway |
| `interventions` | `id` | JSON (§5) | 1 | api-gateway → swarm-gen, mqtt-bridge |
| `neuro.features` | shard as decimal string | `FeatureFrame` (binary) | 16 | feature-worker → api-gateway |
| `neuro.alerts` | `node_hex` | JSON (§7) | 4 | feature-worker → api-gateway |

Consumer group names: `feature-worker`, `api-gateway`, `swarm-gen`, `mqtt-bridge`.
Retention: 1 h for binary topics, 24 h for JSON topics (set by `just topics`).

## 4. Binary frames

### 4.1 HilRawFrame  (MQTT `hil/{node_hex}/raw` and Kafka `hil.raw`)

```
offset size  field
0      4     magic          ASCII "HILR"
4      1     version        = 1
5      1     mode           enum §2 (device's current playback mode)
6      2     reserved       = 0
8      4     node_id        u32
12     4     seq            u32, per-device frame counter (wraps)
16     8     ts_us          u64, device micros since boot (NOT unix time)
24     4     sample_rate_hz u32 (10000)
28     2     n_samples      u16 (1000 for a 100 ms frame)
30     2     reserved       = 0
32     2*n   samples        i16[n_samples], units of 0.1 µV (value/10 = µV)
```
Header = 32 bytes. 100 ms @ 10 kHz = 2032 bytes.

### 4.2 SwarmBinFrame  (Kafka `swarm.bins`)

One frame per shard per bin. Counts are spikes per node in the bin.
```
offset size  field
0      4     magic          "SWBN"
4      1     version        = 1
5      1     reserved
6      2     shard          u16
8      4     first_node_id  u32
12     4     n_nodes        u32 (1000)
16     2     bin_ms         u16 (100)
18     2     reserved
20     4     reserved
24     8     t_ms           u64, bin start time
32     2*n   counts         u16[n_nodes]
```
Header = 32 bytes.

### 4.3 FeatureFrame  (Kafka `neuro.features`)

Emitted once per shard per **1 s window** (sliding, step 1 s).
```
offset size  field
0      4     magic          "FEAT"
4      1     version        = 1
5      1     n_feat         = 8
6      2     shard          u16
8      4     first_node_id  u32
12     4     n_nodes        u32
16     8     t_ms           u64, window end time
24     4     window_ms      u32 (1000)
28     4     reserved
32     ...   records        n_nodes × NodeRecord (40 bytes each)

NodeRecord:
0      4     stress         f32 in [0,1]  (Neural Stress Index)
4      1     state          enum §2
5      3     reserved
8      32    features       f32[8], indices:
                 0 rate_hz  1 isi_mean_ms  2 isi_cv  3 burst_index
                 4 fano     5 rate_slope_hz_per_s  6 band_power (raw only, else 0)
                 7 snr_db (raw only, else 0)
```
For HIL nodes `n_nodes = 1`, `first_node_id = node_id`, `shard = 0xFFFF`.

### 4.4 GridFrame  (WebSocket `/ws/grid`, binary message)

```
0      4     magic   "GRID"
4      1     version = 1
5      1     reserved
6      2     width   u16
8      2     height  u16
10     6     reserved
16     8     t_ms    u64
24     w*h   stress  u8[width*height], 0..255 = 0.0..1.0, row-major (y*width+x)
```
Sent at 2–10 Hz (server decides). On connect the server first sends one
**text** message:
`{"type":"hello","width":400,"height":250,"nodes":100000,"hil":[{"node_id":4294901760,"node_hex":"ffff0000","online":true,"mode":0}]}`.

## 5. Intervention (JSON, topic `interventions`)

```json
{
  "id": "uuid-v4",
  "t_ms": 1757400000000,
  "kind": "toxin",
  "target": { "type": "region", "x": 200, "y": 125, "r": 40 },
  "params": { "intensity": 0.8, "spread_ms": 20000, "onset_ms": 5000 },
  "source": "dashboard",
  "subtype": "tetrodotoxin"
}
```
- `kind` ∈ `baseline | toxin | drug | radiation`
- `subtype` (optional, may be `null`): agent label from the dashboard catalog
  (`dashboard/lib/catalog.ts`), 1–40 chars of `[a-z0-9_-]`. **Descriptive
  only**: `kind` is authoritative for twin physics and HIL mode; swarm-gen and
  mqtt-bridge parse `subtype` and ignore it, api-gateway validates it and
  echoes it back in the `202` record so the alert timeline can name the agent.
- `target` ∈ `{"type":"region","x","y","r"}` | `{"type":"node","node_id":u32}` | `{"type":"all"}`
- `source` ∈ `dashboard | button | script`
- `params.*` are JSON numbers. Producers may emit them as integers **or
  floats** (e.g. `20000` or `20000.0`); every consumer must parse them as
  f64 and round where an integer is needed.

`POST /interventions` accepts the same object without `id`/`t_ms`
(server fills them) and returns `202` with the full record.
mqtt-bridge forwards interventions whose target is a HIL node (or `all`) to
`hil/{node_hex}/cmd` as `{"mode":1,"intensity":0.8}`.

## 6. Ground truth (JSON, topic `swarm.truth`)

```json
{ "t_ms": 1757400005000, "event": "symptom_onset",
  "intervention_id": "uuid", "node_id": 80125, "kind": "toxin" }
```
`event` ∈ `intervention_applied | symptom_onset | recovered`.
`symptom_onset` = node firing rate fell below 5 % of its baseline for ≥ 2 s.
This timestamp is the reference for the **lead-time** metric.

## 7. Alert (JSON, topic `neuro.alerts`)

```json
{ "t_ms": 1757400003000, "node_id": 80125, "node_hex": "000138fd",
  "prev_state": 0, "state": 1, "stress": 0.83, "detector": "gru" }
```
Emitted only on state transitions.

## 8. MQTT (Mosquitto, anonymous, port 1883 / ws 9001)

| Topic | Dir | Payload |
|---|---|---|
| `hil/{node_hex}/raw` | device → | `HilRawFrame` |
| `hil/{node_hex}/status` | device → (every 2 s, retained) | `{"node_id":u32,"mode":u8,"uptime_s":u32,"rssi":i8,"fw":"0.1.0","ip":"..."}` |
| `hil/{node_hex}/cmd` | → device | `{"mode":u8,"intensity":0..1}` |

Device modes alter playback: `toxin` progressively drops spikes and shrinks
amplitude; `drug` restores toward baseline; `radiation` bursts then adds noise.
`status` may carry additional diagnostic keys (e.g. `intensity`, `severity`,
`frames`, `overruns`); consumers must ignore unknown keys.

## 9. api-gateway HTTP/WS (port 8080)

- `GET  /health` → `{"status":"ok","kafka":true}`
- `GET  /nodes/hil` → array of last `status` payloads (§8) + `online` bool
- `POST /interventions` → §5
- `GET  /metrics` → Prometheus text
- `WS   /ws/grid` → §4.4
- `WS   /ws/node/{node_id}` → text `{"type":"features","t_ms":..,"stress":..,"state":..,"features":[8]}` at 1 Hz,
  text `{"type":"alert", ...§7}` on transitions, and for HIL nodes **binary**
  `HilRawFrame` passthrough at 10 Hz.

## 10. Environment variables and ports

| Var | Default (compose) | Used by |
|---|---|---|
| `KAFKA_BROKERS` | `redpanda:9092` (host: `localhost:19092`) | all services |
| `MQTT_URL` | `mqtt://mosquitto:1883` | mqtt-bridge; ESP32 uses `MQTT_HOST`/`MQTT_PORT` build flags |
| `API_PORT` | `8080` inside containers; **`18080` when running natively on the dev host** (8080/8081/9001 are taken by other containers on this machine) | api-gateway |
| `GRID_W` / `GRID_H` | `400` / `250` | swarm-gen, api-gateway, dashboard |
| `SWARM_NODES` / `SWARM_SHARDS` / `BIN_MS` / `SEED` | `100000` / `100` / `100` / `42` | swarm-gen |
| `NEXT_PUBLIC_API_URL` | `http://localhost:18080` | dashboard |
| `METRICS_PORT` | swarm-gen 9100, feature-worker 9101, mqtt-bridge 9102 | |

Host ports (dev laptop): Redpanda 19092, Redpanda Console **18081**,
Mosquitto 1883 (TCP) / **19001** (WebSocket), api-gateway **18080**
(container-internal 8080), dashboard 3000. Container-to-container names/ports
are unchanged: `redpanda:9092`, `mosquitto:1883`, `api-gateway:8080`.
