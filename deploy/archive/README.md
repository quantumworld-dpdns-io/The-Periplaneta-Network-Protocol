# deploy/archive/ — the previous direction

Everything here belongs to the project's **previous direction**, a streaming
telemetry and sensor-placement study. The live deployment is one directory up:
`deploy/docker-compose.yml`, which serves the dashboard and is all the project
needs.

| path | what it did |
|---|---|
| `docker-compose.yml` | Redpanda, Mosquitto, the three Rust services and the Julia worker |
| `k8s/` | kustomize base and dev overlay, 22 objects, with a kind bootstrap |
| `kind-cluster.yaml` | local cluster definition |
| `mosquitto/` | broker config for the hardware-in-the-loop nodes |

To bring it up:

```bash
docker compose -f deploy/archive/docker-compose.yml up -d --build
```

The war-room UI this stack used to serve is gone: it was deleted when the
dashboard was rebuilt on the model's static export, so this stack now brings up
backend services with nothing on port 3000.

It last passed CI on 2026-09-11, with all three Rust crates and the Julia worker
green. It has not been rebuilt since, and GitHub Actions is suspended for this
repository, so treat that as the last date it was known to work rather than a
guarantee about today.

**Why they are frozen.** The individuals in that model were statistically
independent of one another — a property a test actively asserted — so "cockroach
network" named a Kafka topic rather than a network of cockroaches. The project
returned to its original intent on 2026-09-11 and the core was rebuilt as
[`blattella/`](../blattella/), where individuals interact, are poisoned, adapt
and evolve.

**What this means in practice.**

- No new work goes here. Bugs are not fixed unless something in `blattella/`
  starts depending on this code, which nothing does.
- `deploy/docker-compose.frozen.yml` still brings the whole stack up if you want
  to see it run.
- One piece stayed in service: `netsim/renewal.py`, the gamma-renewal fitting the
  neural readout uses. Everything else in `netsim/` is frozen too.

The assessment that prompted the change is in `.claude/PLAN.md`; the rebuild is
logged phase by phase in `docs/blattella/PHASE_LOG.md`.
