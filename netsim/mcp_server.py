"""
MCP server exposing the sensor-network simulator to an AI research agent.

Run:   python -m netsim.mcp_server            (stdio transport)
Tools:
  describe_model()                      empirical inputs, defaults, detectors, rules
  run_scenario(...)                     one calibrated run -> alarm time, lead, localisation error
  design_sweep(...)                     small sweep over sensor counts -> per-pipeline summary
  fit_renewal(spike_times)              MLE of (shape, rate, refractory) for a spike train you supply

The agent drives parameter sweeps and reads back structured results; every
number it sees is produced by the same code path as netsim/cli.py, so anything
it reports can be reproduced from the command line.
"""
from __future__ import annotations

from dataclasses import asdict, replace

import numpy as np
from mcp.server.fastmcp import FastMCP

from . import experiment as ex
from .detectors import DETECTORS, RULES
from .renewal import fit_spike_train, load_twin_params
from .scenario import Config, simulate

mcp = FastMCP("periplaneta-netsim")


@mcp.tool()
def describe_model() -> dict:
    """Model summary: empirical inputs (fitted units), default configuration, available detectors and network rules."""
    units, meta = load_twin_params()
    return {
        "empirical_input": {**meta, "n_units": len(units),
                            "units": [asdict(u) for u in units]},
        "default_config": asdict(Config()),
        "detectors": list(DETECTORS),
        "rules": list(RULES),
        "ground_truth": "simulated radial front; symptom onset = first collapse at the origin (rate < 5% baseline for 2 s), independent of sensor placement",
        "metrics": {"lead_s": "t_symptom - t_alarm (positive = alarm before first collapse)",
                    "loc_err": "distance from estimated to true origin / domain diagonal",
                    "far": "fraction of null runs that alarm (sustained rule) at the calibrated threshold"},
    }


@mcp.tool()
def run_scenario(n_sensors: int = 36, topology: str = "grid", noise: float = 0.15, detector: str = "cusum",
                 rule: str = "pool", seed: int = 1, alpha: float = 0.05, n_calib: int = 30,
                 speed: float = 1.5, tau_s: float = 8.0) -> dict:
    """Simulate one perturbed run at a threshold calibrated on `n_calib` null runs (per-run false-alarm probability alpha)."""
    cfg = Config(n_sensors=n_sensors, topology=topology, noise=noise, speed=speed, tau_s=tau_s)
    p = ex.Pipeline(detector, rule)
    nulls = [simulate(cfg, 100_000 + i, perturbed=False) for i in range(n_calib)]
    h = ex.calibrate(nulls, p, alpha)
    run = simulate(cfg, seed)
    r = ex.evaluate_run(run, p, h)
    return {"config": asdict(cfg), "pipeline": p.name, "threshold": h, "seed": seed,
            "origin": list(run.perturbation.origin), "t_symptom": run.t_symptom,
            "t_alarm": r.t_alarm, "detected": r.detected, "lead_s": r.lead_s, "loc_err": r.loc_err,
            "sensor_xy": run.xy.round(2).tolist()}


@mcp.tool()
def design_sweep(n_sensors: list[int] | None = None, topology: str = "grid", noise: float = 0.15,
                 detectors: list[str] | None = None, rules: list[str] | None = None,
                 n_eval: int = 8, alpha: float = 0.05, seed0: int = 1) -> list[dict]:
    """Sweep sensor count for one topology/noise; returns per-pipeline detection probability, lead time, localisation error, held-out FAR."""
    ns = n_sensors or [16, 36, 64]
    pipes = [ex.Pipeline(d, r) for d in (detectors or DETECTORS) for r in (rules or RULES)]
    out: list[dict] = []
    for n in ns:
        cfg = Config(n_sensors=n, topology=topology, noise=noise)
        s, _ = ex.run_config(cfg, pipes, n_calib=max(2 * n_eval, 8), n_heldout=max(n_eval, 4),
                             n_eval=n_eval, alpha=alpha, seed0=seed0)
        out += [asdict(x) for x in s]
    return out


@mcp.tool()
def fit_renewal(spike_times_s: list[float]) -> dict:
    """MLE fit of a gamma-renewal-with-refractory model to spike times (seconds, sorted or not)."""
    p = fit_spike_train(np.asarray(spike_times_s, dtype=float))
    return {**asdict(p), "isi_cv": p.isi_cv, "n_spikes": len(spike_times_s)}


if __name__ == "__main__":
    mcp.run()
