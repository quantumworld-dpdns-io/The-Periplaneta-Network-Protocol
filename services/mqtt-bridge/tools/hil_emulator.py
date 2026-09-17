#!/usr/bin/env python3
"""
hil_emulator.py -- host-side stand-in for the two ESP32-S3 HIL boards.

Publishes valid HilRawFrames (proto/WIRE.md section 4.1) to hil/{node_hex}/raw at
10 Hz (1000 samples @ 10 kHz), a retained JSON status to hil/{node_hex}/status
every 2 s, and reacts to {"mode":u8,"intensity":f} on hil/{node_hex}/cmd with the
same baseline/toxin/drug/radiation semantics as firmware/hil-node/src/playback.cpp.

    pip install paho-mqtt numpy
    python hil_emulator.py                       # nodes ffff0000 + ffff0001 -> localhost:1883
    python hil_emulator.py --host 192.168.1.10 --nodes 0
    python hil_emulator.py --mode toxin --intensity 0.8   # start in a mode

Try it:  mosquitto_pub -t hil/ffff0000/cmd -m '{"mode":1,"intensity":0.8}'
"""
from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
import threading
import time

import numpy as np

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover
    sys.exit("pip install paho-mqtt numpy")

RATE_HZ = 10_000
FRAME_N = 1000
HEADER = struct.Struct("<4sBBHIIQIHH")  # magic ver mode res node_id seq ts_us rate n res
assert HEADER.size == 32
MODES = {"baseline": 0, "toxin": 1, "drug": 2, "radiation": 3}

TOXIN_RAMP_S, DRUG_RECOVERY_S, BASELINE_FADE_S = 20.0, 15.0, 60.0
RADIATION_BURST_S, RADIATION_RAMP_S = 2.0, 5.0


def biphasic_wave(n: int = 16, peak_lsb: float = 1200.0) -> np.ndarray:
    t = np.arange(n) / RATE_HZ * 1e3
    w = -np.exp(-0.5 * ((t - 0.40) / 0.12) ** 2) + 0.38 * np.exp(-0.5 * ((t - 0.95) / 0.28) ** 2)
    w -= w[0]
    return w * (peak_lsb / np.abs(w).max())


def pink_noise(n: int, rms: float, rng: np.random.Generator) -> np.ndarray:
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n)
    f[0] = f[1]
    spec /= np.sqrt(f)
    spec[0] = 0
    x = np.fft.irfft(spec, n)
    return x * (rms / (x.std() or 1.0))


class Node:
    def __init__(self, index: int, rng_seed: int, mode: int = 0, intensity: float = 0.0):
        self.node_id = 0xFFFF0000 + index
        self.hex = f"{self.node_id:08x}"
        self.rng = np.random.default_rng(rng_seed)
        self.mode, self.intensity = mode, intensity
        self.severity, self.t_in_mode = 0.0, 0.0
        self.seq, self.frames = 0, 0
        self.t0 = time.monotonic()
        self.wave = biphasic_wave()
        self.noise_rms = 45.0
        self.mean_rate = 20.0
        self.refr = 0.002
        self.next_spike_s = self._draw_isi()
        self.lock = threading.Lock()

    # -- pathology (mirrors playback.cpp) --------------------------------------
    def _draw_isi(self) -> float:
        return self.refr + self.rng.gamma(2.0, (1 / self.mean_rate - self.refr) / 2.0)

    def set_mode(self, mode: int, intensity: float) -> None:
        with self.lock:
            self.mode, self.intensity = mode, min(max(intensity, 0.0), 1.0)
            self.t_in_mode = 0.0

    def step(self, dt: float) -> dict:
        self.t_in_mode += dt
        i, m = self.intensity, self.mode
        if m == 1:
            if self.severity < i:
                self.severity = min(i, self.severity + dt * i / TOXIN_RAMP_S)
        elif m == 2:
            self.severity -= dt * i / DRUG_RECOVERY_S
        elif m == 3:
            if self.t_in_mode > RADIATION_BURST_S:
                tgt = 0.6 * i
                if self.severity < tgt:
                    self.severity = min(tgt, self.severity + dt * tgt / RADIATION_RAMP_S)
        else:
            self.severity -= dt / BASELINE_FADE_S
        self.severity = min(max(self.severity, 0.0), 1.0)
        eff = dict(drop=self.severity, amp=1 - 0.7 * self.severity, noise=1.0, extra_hz=0.0)
        if m == 3:
            if self.t_in_mode <= RADIATION_BURST_S:
                eff.update(drop=0.0, amp=1 + 0.3 * i, extra_hz=150 * i, noise=1 + i)
            else:
                eff["noise"] = 1 + 3 * i
        return eff

    # -- frame -----------------------------------------------------------------
    def frame(self) -> bytes:
        with self.lock:
            eff = self.step(FRAME_N / RATE_HZ)
            y = pink_noise(FRAME_N, self.noise_rms * eff["noise"], self.rng)
            # template spikes: renewal process continued across frames
            t = self.next_spike_s
            while t < FRAME_N / RATE_HZ:
                if self.rng.random() >= eff["drop"]:
                    self._add_spike(y, int(t * RATE_HZ), eff["amp"] * (1 + 0.15 * self.rng.standard_normal()))
                t += self._draw_isi()
            self.next_spike_s = t - FRAME_N / RATE_HZ
            # extra burst spikes
            n_extra = self.rng.poisson(eff["extra_hz"] * FRAME_N / RATE_HZ)
            for k in self.rng.integers(0, FRAME_N, n_extra):
                self._add_spike(y, int(k), eff["amp"])
            samples = np.clip(np.round(y), -32768, 32767).astype("<i2")
            ts_us = int((time.monotonic() - self.t0) * 1e6)
            hdr = HEADER.pack(b"HILR", 1, self.mode, 0, self.node_id, self.seq, ts_us, RATE_HZ, FRAME_N, 0)
            self.seq = (self.seq + 1) & 0xFFFFFFFF
            self.frames += 1
            return hdr + samples.tobytes()

    def _add_spike(self, y: np.ndarray, at: int, gain: float) -> None:
        end = min(at + len(self.wave), len(y))
        if end > at:
            y[at:end] += gain * self.wave[: end - at]

    def status(self, ip: str) -> str:
        return json.dumps({
            "node_id": self.node_id, "mode": self.mode,
            "uptime_s": int(time.monotonic() - self.t0), "rssi": -50,
            "fw": "0.1.0-emu", "ip": ip,
            "intensity": round(self.intensity, 2), "severity": round(self.severity, 3),
            "frames": self.frames, "overruns": 0,
        }, separators=(",", ":"))


def local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--nodes", type=int, nargs="+", default=[0, 1], help="HIL indices to emulate")
    ap.add_argument("--mode", choices=MODES, default="baseline")
    ap.add_argument("--intensity", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--fps", type=float, default=10.0, help="frames per second (10 = real time)")
    args = ap.parse_args()

    nodes = {f"{0xFFFF0000 + i:08x}": Node(i, args.seed + i, MODES[args.mode], args.intensity) for i in args.nodes}
    ip = local_ip()

    def on_connect(client, userdata, flags, reason, properties=None):
        print(f"[emu] connected to {args.host}:{args.port} (rc={reason}); nodes {', '.join(nodes)}")
        for hx in nodes:
            client.subscribe(f"hil/{hx}/cmd", qos=1)

    def on_message(client, userdata, msg):
        hx = msg.topic.split("/")[1]
        node = nodes.get(hx)
        if node is None:
            return
        try:
            d = json.loads(msg.payload)
            mode, inten = int(d["mode"]), float(d.get("intensity", 1.0))
        except (ValueError, KeyError, TypeError) as e:
            print(f"[emu] {hx}: bad cmd {msg.payload!r}: {e}")
            return
        if 0 <= mode <= 3:
            node.set_mode(mode, inten)
            print(f"[emu] {hx}: mode={mode} intensity={inten:.2f}")
            client.publish(f"hil/{hx}/status", node.status(ip), qos=0, retain=True)

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"hil-emulator-{args.seed}")
    except AttributeError:  # paho 1.x
        client = mqtt.Client(client_id=f"hil-emulator-{args.seed}")
        client.on_connect = lambda c, u, f, rc: on_connect(c, u, f, rc)
    else:
        client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.host, args.port, keepalive=15)
    client.loop_start()

    period = 1.0 / args.fps
    next_t = time.monotonic()
    last_status = 0.0
    try:
        while True:
            for hx, node in nodes.items():
                client.publish(f"hil/{hx}/raw", node.frame(), qos=0)
            now = time.monotonic()
            if now - last_status >= 2.0:
                last_status = now
                for hx, node in nodes.items():
                    client.publish(f"hil/{hx}/status", node.status(ip), qos=0, retain=True)
                stats = " ".join(f"{hx}:m{n.mode}/sev{n.severity:.2f}" for hx, n in nodes.items())
                print(f"[emu] {stats}  frames={sum(n.frames for n in nodes.values())}", flush=True)
            next_t += period
            time.sleep(max(0.0, next_t - time.monotonic()))
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
