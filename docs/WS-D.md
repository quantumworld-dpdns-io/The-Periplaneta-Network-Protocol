# WS-D — Dashboard + deployment

> **Frozen.** This document describes the previous direction's dashboard and
> Kubernetes deployment. The dashboard has since been rebuilt on the model's
> static export (see `deploy/README.md`), and the Kubernetes manifests moved to
> `deploy/archive/k8s/`. Paths below are given as they are now.

Dirs: `dashboard/`, `deploy/archive/k8s/`, `deploy/archive/kind-cluster.yaml`,
the WS-D block of `deploy/docker-compose.yml`, this file.

## 1. Dashboard (`dashboard/`)

Next.js 15 (App Router, TypeScript, Tailwind), dark "war-room" UI. No UI kit.

| Path | What |
|---|---|
| `app/icon.svg` | favicon — stylised cockroach silhouette (single colour, legible at 16 px) |
| `components/CockroachIcon.tsx` | same silhouette as a React SVG; `size`, `state` → colour + glow. Used in header, HIL badges, NerveCordMap ganglion glyphs, heatmap legend/empty state |
| `lib/wire.ts` | WIRE §4.4 `GridFrame` / §4.1 `HilRawFrame` decoders + encoders (DataView, LE); JSON types for hello/features/alert/intervention |
| `lib/ws.ts` | reconnecting sockets: `useGridSocket()`, `useNodeSocket(id)`, `useHilActivity(ids)` (spike detection for the nerve-cord flash) |
| `lib/api.ts` | `POST /interventions`, `GET /nodes/hil`, `GET /health` |
| `lib/mock.ts` | in-browser generator (see §1.2) |
| `components/GridHeatmap.tsx` | canvas `ImageData` w×h from hello, stress LUT deep-blue→teal→amber→red, ≤10 fps, HiDPI overlay, click → node `y*width+x`, region placement |
| `components/NodeDeepDive.tsx` | oscilloscope (rolling 1 s int16 ring buffer, min/max per column), stress + rate sparklines, 8 named features, alert timeline |
| `components/NerveCordMap.tsx` | SVG ventral nerve cord: brain, SOG, T1–T3, A1–A6. HIL 0 → thoracic, HIL 1 → abdominal; ganglia flash on threshold-crossing spikes |
| `components/InterventionPanel.tsx` | kind / target (region x,y,r · node · all) / intensity, spread_ms, onset_ms → `POST /interventions` |
| `components/SystemPanel.tsx` | grid fps (client-side), KiB/s, twins, gateway + health status, HIL online list, last alerts, interventions sent |
| `lib/spread.ts` | client-side spread estimation: 4-connected flood fill over the uint8 stress grid → clusters (cells, stress-weighted centroid, area-equivalent radius, mean/peak stress); `estimateFronts` matches consecutive detections by centroid to give radial growth and drift in cells/s and a suggested countermeasure region; `stressedFraction`. Pure, unit-tested (`test/spread.test.ts`) |
| `components/SpreadPanel.tsx` | "Spread estimate" panel: runs `lib/spread.ts` at 1 Hz on `grid.latest` (no extra backend traffic), lists fronts with growth colouring, "target" button sets the intervention region to the front's centroid + 1.25× radius |

### 1.1 Run

```bash
cd dashboard
npm install
NEXT_PUBLIC_API_URL=http://localhost:18080 npm run dev     # or: just dash-dev  (reads .env.local)
npm run build          # production build (output: standalone)
npx vitest run         # wire codec tests (test/wire.test.ts)
```

Env (`.env.example`): `NEXT_PUBLIC_API_URL` (default `http://localhost:18080`, converted to `ws://` for
`/ws/grid` and `/ws/node/{id}`), `NEXT_PUBLIC_MOCK`, optional `NEXT_PUBLIC_GRID_W/H` (fallback before hello).
`NEXT_PUBLIC_*` is inlined at **build** time — the Docker image bakes the API URL via `--build-arg`.

### 1.2 Mock mode (no backend)

```bash
cd dashboard && NEXT_PUBLIC_MOCK=1 npm run dev     # http://localhost:3000
```

`lib/mock.ts` emulates api-gateway inside the browser using the real encoders: hello + 400×250 GridFrames at
8 Hz, two HIL nodes (`ffff0000`, `ffff0001`) streaming synthetic 10 kHz raw frames with biphasic spikes,
1 Hz feature messages, alerts on state transitions, and a scripted loop (toxin wave at (200,125) r70 at
t+6 s, HIL 1 → toxin at 14 s, drug at 40/48 s, reset at 70 s, period 80 s). `POST /interventions` is
answered locally and affects the mock world (region/node/all). Note: the generator runs on page timers, so
Chrome throttles it to ~1 fps when the tab is in the background — that is the mock, not the pipeline.

### 1.3 Docker

`dashboard/Dockerfile` — multi-stage (deps → build → `node:22-alpine` runtime with Next standalone output),
non-root, port 3000. Build args `NEXT_PUBLIC_API_URL` (compose: `http://localhost:18080`, kind:
`http://localhost:30080`) and `NEXT_PUBLIC_MOCK`.

## 2. docker-compose

`deploy/docker-compose.yml` WS-D block adds `dashboard` (build `../dashboard`, host port 3000, API URL
`http://localhost:18080`). `just up` builds and starts it with the rest of the stack.

## 3. Kubernetes (`deploy/archive/k8s/`, kustomize)

```
base/
  namespace.yaml   Namespace cockroach
  redpanda.yaml    Redpanda v24.2.7 single-node StatefulSet (dev-container mode) + headless Service
                   redpanda:9092, topics-init Job (same rpk commands as compose), Redpanda Console
  mosquitto.yaml   ConfigMap (mosquitto.conf) + Deployment + Service NodePort 31883 (MQTT) / 31901 (WS)
  services.yaml    ConfigMap cockroach-env (WIRE §10), Deployments + Services for mqtt-bridge, swarm-gen,
                   api-gateway (NodePort 30080), feature-worker (+ HPA cpu 70 %, 1..8), dashboard (NodePort 30000)
  ingress.yaml     ingress-nginx: /api(/|$)(.*) → api-gateway:8080 (rewrite), / → dashboard:3000
overlays/dev/      replicas (feature-worker 2), env (RUST_LOG), image tags :dev
```

Images are `cockroach/<service>:dev`, `imagePullPolicy: IfNotPresent`, built locally and `kind load`-ed —
no registry. The dashboard's browser-side WebSocket goes straight to the api-gateway NodePort
(`NEXT_PUBLIC_API_URL=http://localhost:30080`) so WS never traverses the ingress; the Ingress `/api`
route is for plain HTTP clients and only works if ingress-nginx is installed (`INSTALL_INGRESS=1`).

### 3.1 Bring-up

```bash
just k8s-up            # == bash deploy/archive/k8s/up.sh (frozen recipe)
just k8s-down          # == kind delete cluster --name cockroach
```

`up.sh`: installs kind if missing (`winget install Kubernetes.kind`, fallback `go install`, fallback
binary download into `deploy/bin/`), creates cluster `cockroach` from `deploy/archive/kind-cluster.yaml`, builds
every `services/*/Dockerfile` + `dashboard/Dockerfile` that exists (missing ones are skipped with a
warning), `kind load docker-image`, `kubectl apply -k deploy/archive/k8s/overlays/dev`, waits for rollouts.
Knobs: `SKIP_BUILD=1`, `INSTALL_INGRESS=1`, `METRICS_SERVER=1` (required for the HPA to act),
`DASHBOARD_API_URL`, `DASHBOARD_MOCK=1`.

Validate without a cluster: `kubectl kustomize deploy/archive/k8s/overlays/dev` (renders 22 objects).
`kubectl apply -k deploy/archive/k8s/overlays/dev --dry-run=client` additionally needs a reachable kube context
for API discovery (kind provides one).

On Windows, winget puts `kind.exe` in `%LOCALAPPDATA%\Microsoft\WinGet\Links` — restart the shell (or
`export PATH="$LOCALAPPDATA/Microsoft/WinGet/Links:$PATH"`) after the first install.

### 3.2 Port table

| Where | Port | Service | Notes |
|---|---|---|---|
| compose host | 3000 | dashboard | `NEXT_PUBLIC_API_URL=http://localhost:18080` |
| compose host | 18080 | api-gateway | HTTP + WS (8080 in-container) |
| compose host | 18081 | Redpanda Console | |
| compose host | 19092 | Redpanda Kafka (external) | in-cluster `redpanda:9092` |
| compose host | 1883 / 19001 | Mosquitto MQTT / MQTT-WS | |
| kind host | 30000 | dashboard NodePort | `http://localhost:30000` |
| kind host | 30080 | api-gateway NodePort | `ws://localhost:30080/ws/grid` |
| kind host | 31883 | Mosquitto NodePort | ESP32 `MQTT_HOST=<host LAN ip>`, `MQTT_PORT=31883` |
| kind host | 31901 | Mosquitto WS NodePort | |
| kind host | 80 | ingress-nginx | only with `INSTALL_INGRESS=1` |
| in-cluster | redpanda:9092, mosquitto:1883, api-gateway:8080, dashboard:3000, redpanda-console:8080 | | |
| metrics | swarm-gen 9100, feature-worker 9101, mqtt-bridge 9102 | Prometheus text | |

### 3.3 Topology

```
ESP32 ──LAN:31883──▶ mosquitto (NodePort) ──▶ mqtt-bridge ──▶ redpanda (StatefulSet, 1 node)
                                                                 │  swarm.bins ◀── swarm-gen (1 replica, Recreate)
                                                                 ▼
                                                    feature-worker ×N (HPA cpu 70 %, 1..8)
                                                                 │ neuro.features / neuro.alerts
browser ──:30000──▶ dashboard ──(browser WS :30080)──▶ api-gateway ◀──────────────────┘
                                                        │ POST /interventions → interventions topic
```
