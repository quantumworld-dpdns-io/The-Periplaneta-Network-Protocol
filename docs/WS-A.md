# WS-A — HIL firmware + MQTT bridge

Dirs: `firmware/hil-node/`, `services/mqtt-bridge/`.
Contract: `proto/WIRE.md` §1, §2, §4.1, §5, §8, §10.

```
ESP32-S3 (hil0/hil1) ──hil/{hex}/raw (2032 B, 10 Hz)──▶ Mosquitto ──▶ mqtt-bridge ──▶ Redpanda hil.raw (key node_hex)
                     ──hil/{hex}/status (JSON, 2 s, retained)──▶       │  keeps last status → GET :9102/hil
                     ◀──hil/{hex}/cmd {"mode","intensity"}─────────────┘◀── Redpanda interventions (group mqtt-bridge)
```

## Components

| Path | What | Status |
|---|---|---|
| `firmware/hil-node/` | PlatformIO / Arduino, envs `hil0`, `hil1` | compiles (`pio run -e hil0`); not flashed (no board) |
| `firmware/hil-node/src/playback.{h,cpp}` | 10 kHz ISR renderer + mode semantics (pure functions) | host tests `pio test -e native` |
| `firmware/hil-node/include/template.h` | spike template (generated) | **placeholder** synthetic gamma-renewal train until `just template` is run with the Zenodo data on disk (needs Julia) |
| `firmware/hil-node/tools/make_template.py` | placeholder generator (numpy); `ml/scripts/make_template.jl` emits the same `TPL_*` header from real recordings | numpy only |
| `services/mqtt-bridge/` | Rust: rumqttc + rdkafka (cmake-build) + axum + prometheus | builds, 5 unit tests, verified end to end |
| `services/mqtt-bridge/tools/hil_emulator.py` | host stand-in for both boards (paho-mqtt + numpy) | works against local Mosquitto |
| `deploy/docker-compose.yml` (WS-A block) | `mqtt-bridge` service, host port 9102 | added |

## Run without boards

```bash
just infra                                            # Redpanda + Mosquitto
cd services/mqtt-bridge && MQTT_URL=mqtt://localhost:1883 KAFKA_BROKERS=localhost:19092 cargo run --release
pip install paho-mqtt numpy && python services/mqtt-bridge/tools/hil_emulator.py     # nodes ffff0000, ffff0001
curl localhost:9102/hil          # last status per node (+ online, age_s)
curl localhost:9102/metrics      # mqtt_bridge_frames_in / frames_out / bad_frames / cmds_out / hil_online
just consume hil.raw             # keyed binary records
# send an intervention (or POST /interventions on the gateway once WS-B is up)
echo '{"id":"x","kind":"toxin","target":{"type":"node","node_id":4294901760},"params":{"intensity":0.8}}' \
  | docker exec -i cockroach-internet-redpanda-1 rpk topic produce interventions -k x --brokers localhost:9092
```

Or in Docker: `docker compose -f deploy/docker-compose.yml up -d --build mqtt-bridge`.

## Firmware notes

* Node id `0xFFFF0000 + HIL_INDEX`; `HIL_INDEX` is a build flag set by env
  (`hil0` → 0, `hil1` → 1). Wi-Fi/MQTT credentials come from
  `firmware/hil-node/secrets.ini` (gitignored; template `secrets.example.ini`).
* Sampling: GPTimer at 10 kHz (`timerBegin/timerAttachInterrupt`; both
  Arduino-core 2.x and 3.x APIs supported) fills a 2×1000-sample double
  buffer; `loop()` publishes the finished buffer with PubSubClient
  (`setBufferSize(2304)`). Overruns are counted and reported in `status`.
* ISR safety: the template is copied to RAM in `Playback::begin()`, and all
  ISR code is `IRAM_ATTR` + integer math — no flash access from the ISR.
* Mode semantics (WIRE §8), all driven by a `severity` state that persists
  across mode changes:
  * toxin: severity → intensity over 20 s; drop p = severity, amp × (1 − 0.7·severity)
  * drug: severity → 0 at rate intensity/15 s
  * radiation: 2 s burst (+150·I Hz Poisson spikes, amp × (1 + 0.3·I), noise × (1 + I)),
    then noise × (1 + 3·I) and severity → 0.6·I over 5 s
  * baseline: severity fades over 60 s
* BOOT button (GPIO0) cycles modes at intensity 0.8; `cmd` sets any mode.
* `status` JSON has the WIRE §8 fields plus `intensity`, `severity`, `frames`,
  `overruns` (extra fields; consumers should ignore unknown keys).

## Template / WS-C hand-off

`template.h` stores **ingredients** — spike sample indices, per-spike amplitude
(u8, 128 = 1.0), a 16-sample biphasic waveform (0.1 µV) and the pink-noise RMS —
not the rendered 10 s int16 array. A rendered array does not fit the 250 KB
header budget as C text (100k samples ≈ 600 KB), and ingredients make the mode
effects exact (drop/scale individual spikes). The device renders the same model
the script's `--render` does. Header size is ~3.4 KB.

WS-C: extract spike times (and optionally a mean waveform + amplitudes) from a
CRCNS/Zenodo recording, then

```bash
python firmware/hil-node/tools/make_template.py --spikes seg_times.npy --wave seg_wave.npy \
    --amps seg_amps.npy --duration 10 --source "CRCNS hc-3 ec013.527 ch3 0-10 s"
```

and rebuild the firmware. Nothing else changes.

## Bridge notes

* `hil/+/raw` payloads are validated (magic, version, `len == 32 + 2·n`);
  invalid ones increment `bad_frames` and are dropped. The Kafka key is the
  `node_id` inside the frame (8 lowercase hex chars).
* `interventions` → `cmd`: `target.type == "node"` with `node_id ≥ 0xFFFF0000`
  → that node; `"all"` → `ffff0000`, `ffff0001` plus any other HIL node seen on
  `status`; `region` never targets HIL nodes. `kind` → `mode` per WIRE §2.
  Consumer starts at `latest` (a restarted bridge does not replay old commands).
* Intervention `params` (`intensity`, `spread_ms`, `onset_ms`) are parsed as f64
  (WIRE §5: JSON numbers may be int or float); `intensity` is clamped to [0,1] and
  sent to the device as f32.
* Windows build: `.cargo/config.toml` sets `CMAKE_GENERATOR=Ninja` because
  rdkafka-sys with the Visual Studio generator overflows MAX_PATH under
  `target/`. Needs cmake + ninja on PATH (WinLibs provides both); the
  Dockerfile installs `cmake ninja-build`.
* No TLS/SASL (features off) — plaintext Redpanda/Mosquitto only, as in compose.

## Open items

* Flash + on-board validation (timer jitter, Wi-Fi throughput at 20 KB/s per board).
* Real template from WS-C.
* `justfile firmware-build` runs `pio run` (default env hil0 only); use
  `pio run -e hil0 -e hil1` to build both.
