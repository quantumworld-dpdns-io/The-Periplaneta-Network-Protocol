# services/ — frozen

These four services belong to the project's **previous direction**, a streaming
telemetry and sensor-placement study. They are kept so the repository's history
stays legible, not because anything uses them.

They last passed CI on 2026-09-11, with all three Rust crates building and their
tests green. They have not been rebuilt since, and GitHub Actions is suspended
for this repository, so that is the last date they were known to work rather
than a claim about today.

| service | what it did | language |
|---|---|---|
| `mqtt-bridge` | MQTT ⇄ Redpanda bridge for the hardware-in-the-loop nodes | Rust |
| `swarm-gen` | 100,000 independent digital twins and their ground truth | Rust |
| `api-gateway` | WebSocket fan-out and REST interventions | Rust |
| `feature-worker` | spike features and a stress index | Julia |

**Why they are frozen.** The individuals in that model were statistically
independent of one another — a property a test actively asserted — so "cockroach
network" named a Kafka topic rather than a network of cockroaches. The project
returned to its original intent on 2026-09-11 and the core was rebuilt as
[`blattella/`](../blattella/), where individuals interact, are poisoned, adapt
and evolve.

**What this means in practice.**

- No new work goes here. Bugs are not fixed unless something in `blattella/`
  starts depending on this code, which nothing does.
- `docker compose -f deploy/archive/docker-compose.yml up -d --build` still
  brings the whole stack up if you want to see it run.
- One piece stayed in service: `netsim/renewal.py`, the gamma-renewal fitting the
  neural readout uses. Everything else in `netsim/` is frozen too.

The assessment that prompted the change is in `.claude/PLAN.md`; the rebuild is
logged phase by phase in `docs/blattella/PHASE_LOG.md`.
