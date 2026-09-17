"""
netsim -- bio-sensor network design study for The Periplaneta Protocol.

Question: given single-node spike statistics fitted to real insect recordings,
how many sensors, in what layout, and with what collective inference rule are
needed to detect a spatially spreading neurotoxic perturbation with a target
lead time and false-alarm rate -- and where did it start?

Modules
  renewal    gamma-renewal spike trains with absolute refractory period; MLE fit
  field      spatial perturbation: radial front from an origin, rate decay behind it
  sensors    sensor layouts (grid / random / clustered) and neighbour structure
  detectors  single-node z-score, CUSUM, BOCPD; network rules max / pool / vote
  scenario   one simulated run: spikes -> 1 s counts -> statistics -> alarms
  experiment matched-false-alarm calibration, sweeps, metrics, CSV + plots
  mcp_server Model Context Protocol server exposing the simulator to an AI agent

Ground truth here is simulated by construction: this is a *network design*
study, not a biological lead-time measurement. Single-node parameters are the
only empirical input (ml/artifacts/twin_params.json, fitted to Zenodo 14281 /
14607). See netsim/README.md.
"""

__version__ = "0.1.0"
