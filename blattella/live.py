"""
Live simulation sessions: watch a colony while you interfere with it.

The dashboard's homepage runs one of these. A session holds a colony, steps it
forward, and emits frames; meanwhile the browser can act on it -- put down a
bait, force the lights on, let more animals in, run a trap. The point is that
you see the consequence unfold rather than reading a summary of it.

The heat layers are **real model state**, not a visualisation invented for the
page: `pheromone` is the aggregation mark the animals actually deposit and
follow, and `residue` is the insecticide actually lying on the floor. Both are
already grids inside the model. Density is counted from the animals themselves.

Frames are deliberately small. Grids are downsampled to `GRID` cells a side and
quantised to a byte, which is enough to see structure and cheap enough to send
several times a second.
"""
from __future__ import annotations

import base64
import math
import threading
import time
import uuid
from dataclasses import dataclass, field

import numpy as np

from .arena import default_arena
from .behaviour import FORAGING, RESTING, RETURNING, STATE_NAMES, Colony, make_colony
from .contact import ContactRecorder
from .toxicology import ACTIVES, Toxicology

GRID = 48                     # heat-layer resolution sent to the browser
MAX_SESSIONS = 8
SESSION_TTL_S = 900.0
LAYERS = ("pheromone", "residue", "density")


def _quantise(field_2d: np.ndarray, size: int = GRID) -> tuple[str, float]:
    """
    Downsample to `size` square, scale to bytes, return base64 plus the peak.

    Block maximum by reshaping rather than looping: a nested Python loop over
    2304 cells, three layers, several times a second, was slow enough to stall
    the stream.
    """
    h, w = field_2d.shape
    if h == 0 or w == 0:
        return "", 0.0
    by, bx = math.ceil(h / size), math.ceil(w / size)
    pad_y, pad_x = by * size - h, bx * size - w
    padded = np.pad(field_2d, ((0, pad_y), (0, pad_x))) if (pad_y or pad_x) else field_2d
    out = padded.reshape(size, by, size, bx).max(axis=(1, 3))
    peak = float(out.max())
    if peak <= 0:
        return base64.b64encode(np.zeros((size, size), dtype=np.uint8).tobytes()).decode(), 0.0
    # square root makes the low end visible; a linear ramp hides everything but the peak
    scaled = np.sqrt(out / peak) * 255.0
    return base64.b64encode(scaled.astype(np.uint8).tobytes()).decode(), peak


@dataclass
class LiveSession:
    """One colony being watched. Not thread-safe by itself; the registry locks."""

    id: str
    colony: Colony
    tox: Toxicology
    recorder: ContactRecorder = field(default_factory=ContactRecorder)
    dt: float = 1.0
    speed: int = 30                     # simulated seconds per frame
    paused: bool = False
    force_phase: str | None = None      # "dark", "light" or None to follow the clock
    created: float = field(default_factory=time.monotonic)
    touched: float = field(default_factory=time.monotonic)
    events: list[dict] = field(default_factory=list)
    # (n_resources, n) -- has this individual ever fed at this station? Purely an
    # observation: a bait station nobody walks to kills nobody, and the reader
    # needs to be able to see which stations those are.
    station_users: np.ndarray | None = None

    # ------------------------------------------------------------------ steps
    def advance(self) -> None:
        if self.paused:
            return
        if self.force_phase is not None:
            # hold the clock inside the requested phase rather than faking is_dark,
            # so every other time-dependent term stays consistent
            period = 24 * 3600.0
            hour = (self.colony.t % period) / 3600.0
            if self.force_phase == "dark" and hour < 12.0:
                self.colony.t += (12.0 - hour) * 3600.0
            elif self.force_phase == "light" and hour >= 12.0:
                self.colony.t += (24.0 - hour) * 3600.0
        c = self.colony
        sites = c.arena.resources
        if self.station_users is None or self.station_users.shape[1] != c.n:
            self._grow_users()
        for _ in range(int(self.speed / self.dt)):
            c.step(self.dt)
            self.recorder.observe(c, self.dt)
            self.tox.observe(c, self.dt)
            if self.station_users is not None:
                for k, site in enumerate(sites):
                    self.station_users[k] |= site.contains(c.xy) & c.alive
        self.touched = time.monotonic()

    def _grow_users(self) -> None:
        n, k = self.colony.n, len(self.colony.arena.resources)
        grown = np.zeros((k, n), dtype=bool)
        if self.station_users is not None:
            grown[:, : self.station_users.shape[1]] = self.station_users
        self.station_users = grown

    def station_visits(self) -> list[int]:
        """How many distinct individuals have ever stood at each station."""
        if self.station_users is None:
            return [0] * len(self.colony.arena.resources)
        return self.station_users.sum(axis=1).astype(int).tolist()

    # ----------------------------------------------------------------- frames
    def _density(self) -> np.ndarray:
        a = self.colony.arena
        g = np.zeros((GRID, GRID))
        live = self.colony.alive
        if not live.any():
            return g
        xy = self.colony.xy[live]
        gx = np.clip((xy[:, 0] / a.width * GRID).astype(int), 0, GRID - 1)
        gy = np.clip((xy[:, 1] / a.height * GRID).astype(int), 0, GRID - 1)
        np.add.at(g, (gy, gx), 1.0)
        return g

    def frame(self) -> dict:
        self.frames_sent += 1
        c, tox = self.colony, self.tox
        live = c.alive
        burden = tox.burden.sum(axis=0)
        ld50 = min(a.ld50 for a in tox.actives)
        lethal = burden / ld50

        phero, phero_peak = _quantise(c.pheromone)
        residue, residue_peak = _quantise(tox.residue.sum(axis=0))
        density, density_peak = _quantise(self._density(), GRID)

        net = self.recorder.network()
        hours = c.t / 3600.0
        return {
            "t_hours": round(hours % 24, 3),
            "elapsed_hours": round((c.t - self.start_t) / 3600.0, 3),
            "dark": c.is_dark(),
            "paused": self.paused,
            "speed": self.speed,
            "grid": GRID,
            "layers": {
                "pheromone": {"data": phero, "peak": round(phero_peak, 3)},
                "residue": {"data": residue, "peak": round(residue_peak, 6)},
                "density": {"data": density, "peak": round(density_peak, 1)},
            },
            "positions": np.round(c.xy, 1).tolist(),
            "alive": live.tolist(),
            "state": c.state.tolist(),
            "lethal_fraction": np.round(np.minimum(lethal, 99.0), 3).tolist(),
            "counts": {
                "total": int(c.n),
                "alive": int(live.sum()),
                "dead": int((~live).sum()),
                **{k: int(v) for k, v in c.state_counts().items()},
                "exposed": int((tox.acquired_from.sum(axis=0) > 0).sum()),
                "fed_at_bait": int((tox.acquired_from[0] > 0).sum()),
            },
            "network": net.summary(),
            "treated": {k: sorted(v) for k, v in tox.treated_resources.items()},
            "station_visits": self.station_visits(),
            "events": self.events[-6:],
        }

    start_t: float = 0.0
    frames_sent: int = 0

    # ---------------------------------------------------------------- actions
    def act(self, action: str, params: dict) -> dict:
        """Apply one interaction. Returns a short description for the event log."""
        c = self.colony
        if action == "pause":
            self.paused = bool(params.get("paused", not self.paused))
            note = "paused" if self.paused else "resumed"
        elif action == "speed":
            self.speed = int(np.clip(int(params.get("speed", 30)), 1, 300))
            note = f"speed {self.speed} sim-s per frame"
        elif action == "light":
            phase = params.get("phase")
            if phase not in (None, "dark", "light", "auto"):
                raise ValueError("phase must be dark, light or auto")
            self.force_phase = None if phase in (None, "auto") else phase
            note = f"lights {phase or 'auto'}"
        elif action == "bait":
            active = params.get("active")
            if active not in ACTIVES:
                raise ValueError(f"unknown active {active!r}")
            total = len(c.arena.resources)
            if params.get("indices") is not None:
                idx = sorted({int(i) for i in params["indices"] if 0 <= int(i) < total})
                if not idx:
                    raise ValueError("no valid station index given")
                where = f"station(s) {', '.join(str(i) for i in idx)}"
            else:
                # Animals walk to their *nearest* food, so a station no harborage
                # is nearest to is never visited and baiting it does nothing. The
                # button therefore treats the busiest stations first, which is
                # also what a technician would do; explicit indices override it.
                n = int(np.clip(int(params.get("stations", 1)), 1, total))
                order = sorted(range(total), key=lambda k: -self.station_visits()[k])
                idx = sorted(order[:n])
                where = f"{n} busiest station(s)"
            self.tox.treat_resource(active, idx)
            note = f"baited {where} with {active}"
        elif action == "clear_bait":
            self.tox.clear_treatments()
            note = "bait removed"
        elif action == "toggle_station":
            # Placement is a real control decision, so it is exposed rather than
            # decided for the reader: click a station on the canvas to bait it or
            # take the bait away, leaving every other station as it was.
            k = int(params.get("index", 0))
            if not 0 <= k < len(c.arena.resources):
                raise ValueError(f"no station {k}")
            active = params.get("active")
            if active not in ACTIVES:
                raise ValueError(f"unknown active {active!r}")
            before = {a: set(v) for a, v in self.tox.treated_resources.items()}
            baited_here = [a for a, v in before.items() if k in v]
            self.tox.clear_treatments()
            for a2, idx in before.items():
                rest = sorted(idx - {k})
                if rest:
                    self.tox.treat_resource(a2, rest)
            if baited_here:
                note = f"station {k} cleared"
            else:
                self.tox.treat_resource(active, [k])
                note = f"station {k} baited with {active}"
        elif action == "add":
            n = int(np.clip(int(params.get("n", 20)), 1, 200))
            c.add(n)
            self.tox.resize()
            note = f"{n} immigrated"
        elif action == "remove":
            n = int(np.clip(int(params.get("n", 20)), 1, 500))
            note = f"{len(c.remove(n))} trapped"
        elif action == "aggregation":
            from . import behaviour as bh
            v = float(np.clip(float(params.get("value", 1.2)), 0.0, 4.0))
            bh.P["pheromone_weight"] = v
            note = f"aggregation pull {v:.2f}"
        elif action == "concentration":
            from . import toxicology as tx
            v = float(np.clip(float(params.get("value", 0.0215)), 0.0, 0.2))
            tx.P["bait_concentration"] = v
            note = f"bait strength {v * 100:.2f} %"
        else:
            raise ValueError(f"unknown action {action!r}")

        self.events.append({"t_hours": round(c.t / 3600.0 % 24, 2), "note": note})
        self.touched = time.monotonic()
        return {"ok": True, "note": note}


class Registry:
    """Sessions live here. Old ones are reaped so a browser tab left open cannot
    pin a simulation forever."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, LiveSession] = {}

    def _reap(self) -> None:
        now = time.monotonic()
        for sid in [s for s, v in self._sessions.items() if now - v.touched > SESSION_TTL_S]:
            self._sessions.pop(sid, None)

    def create(self, colony_n: int, harborages: int, resources: int, arena_cm: float,
               seed: int, speed: int) -> LiveSession:
        with self._lock:
            self._reap()
            if len(self._sessions) >= MAX_SESSIONS:
                oldest = min(self._sessions.values(), key=lambda s: s.touched)
                self._sessions.pop(oldest.id, None)
            arena = default_arena(n_harborages=harborages, n_resources=resources,
                                  width=arena_cm, height=arena_cm, seed=seed)
            colony = make_colony(n=colony_n, seed=seed, arena=arena)
            colony.t = 12 * 3600.0                    # start at dusk, when they come out
            tox = Toxicology(colony=colony, actives=tuple(ACTIVES.values()), seed=seed)
            s = LiveSession(id=uuid.uuid4().hex[:12], colony=colony, tox=tox, speed=speed)
            s.start_t = colony.t
            self._sessions[s.id] = s
            return s

    def get(self, sid: str) -> LiveSession | None:
        with self._lock:
            self._reap()
            return self._sessions.get(sid)

    def drop(self, sid: str) -> bool:
        with self._lock:
            return self._sessions.pop(sid, None) is not None

    def count(self) -> int:
        with self._lock:
            self._reap()
            return len(self._sessions)


REGISTRY = Registry()
