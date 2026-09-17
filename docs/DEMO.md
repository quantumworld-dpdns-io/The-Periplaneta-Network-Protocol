# Demo runbook (≈ 6 minutes)

Pre-flight: `just up`, wait until http://localhost:18080/health returns ok,
both ESP32s powered and on the same Wi-Fi as the laptop (or run the emulator:
`python services/mqtt-bridge/tools/hil_emulator.py`). Open
http://localhost:3000.

| Min | Action | What the audience sees |
|---|---|---|
| 0:00 | Open dashboard | 100k-node heatmap in calm baseline colours; two cockroach badges green; oscilloscope traces live |
| 0:45 | Click HIL badge 0 | Node deep-dive: raw spikes at 10 kHz, feature table, stress index near 0 |
| 1:15 | Press BOOT button on ESP32 #0 (→ toxin) | Spikes thin out on the scope; stress index climbs; alert lands in the timeline within ~3 s; ganglion on the nerve-cord map dims |
| 2:00 | InterventionPanel → toxin, region (200,125) r=40, intensity 0.8 | A wave spreads across the heatmap; alert front runs ahead of the collapse front |
| 3:00 | InterventionPanel → drug, same region | Colours recover from the centre outward |
| 3:45 | Open `docs/EVAL.md` | Lead-time table, GRU vs z-score, mean ± std across seeds |
| 4:30 | Redpanda Console (18081) / SystemPanel | Frames/s and consumer lag under 100k-node load |
| 5:15 | `kubectl get pods -n cockroach` | Same stack on Kubernetes, feature-worker replicas scaled |

Scripted version: `just demo` (runs `docs/demo.sh`).
