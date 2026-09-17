#!/usr/bin/env python3
"""
make_template.py -- generate firmware/hil-node/include/template.h

The HIL firmware replays a 10 kHz extracellular recording. To keep the C header
small (<= 250 KB) and the playback engine flexible (spike dropping, amplitude
scaling, extra noise), the header stores the *ingredients* of the waveform
rather than the rendered int16 array:

  * TPL_SPIKE_T[]   spike times (sample index, sorted) within a TPL_LEN loop
  * TPL_SPIKE_AMP[] per-spike amplitude scale (u8, 128 == 1.0)
  * TPL_WAVE[]      biphasic spike waveform (~1.5 ms, int16, 0.1 uV units)
  * TPL_NOISE_RMS   pink-noise RMS (0.1 uV units); the device synthesises
                    pink noise at run time (Voss-McCartney)

The firmware renders  y[n] = sum_k amp_k * wave[n - t_k] + pink_noise[n]
in the 10 kHz timer ISR, which is bit-for-bit the same model this script
uses for `--render` (except for the noise realisation).

Placeholder mode (default): spike times are a gamma-renewal process
(shape 2, mean rate 20 Hz, 2 ms absolute refractory period) -- a common
stand-in for cortical / insect neuron ISI statistics.

Real-data mode (for WS-C): pass `--spikes times.npy` (float seconds, or int
sample indices with `--spikes-in-samples`) extracted from a CRCNS / Zenodo
recording, optionally `--wave wave.npy` (float uV or int16 0.1 uV, resampled
to 10 kHz) and `--amps amps.npy`. Everything else is unchanged, so WS-C can
swap in a real segment without touching the firmware.

Examples:
  python tools/make_template.py                       # placeholder -> include/template.h
  python tools/make_template.py --render out.npy      # also dump the rendered 10 s int16 wave
  python tools/make_template.py --spikes crcns_seg.npy --wave crcns_wave.npy --source "CRCNS hc-3 ..."

Only depends on numpy.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
import textwrap

import numpy as np

RATE_HZ = 10_000
DEFAULT_DURATION_S = 10.0
WAVE_MS = 1.6                 # waveform support (16 samples @ 10 kHz)
DEFAULT_SPIKE_UV = 120.0      # negative peak amplitude, uV
DEFAULT_NOISE_UV = 4.5        # pink noise RMS, uV
UNIT_SCALE = 10.0             # 0.1 uV per LSB -> value = uV * 10


# --------------------------------------------------------------------------- #
# Ingredients
# --------------------------------------------------------------------------- #
def gamma_renewal_spike_times(duration_s: float, rate_hz: float, shape: float,
                              refractory_ms: float, rng: np.random.Generator) -> np.ndarray:
    """Spike times (seconds) of a gamma renewal process with absolute refractory period."""
    mean_isi = 1.0 / rate_hz
    refr = refractory_ms * 1e-3
    if mean_isi <= refr:
        raise ValueError("rate too high for the refractory period")
    scale = (mean_isi - refr) / shape
    n_guess = int(duration_s * rate_hz * 2) + 50
    isi = refr + rng.gamma(shape, scale, size=n_guess)
    t = np.cumsum(isi) + rng.uniform(0, mean_isi)   # random phase at the start
    return t[t < duration_s]


def biphasic_wave(n: int = int(WAVE_MS * 1e-3 * RATE_HZ), peak_uv: float = DEFAULT_SPIKE_UV) -> np.ndarray:
    """Biphasic extracellular spike: sharp negative peak, slower positive rebound. Units: uV."""
    t = np.arange(n) / RATE_HZ * 1e3  # ms
    neg = np.exp(-0.5 * ((t - 0.40) / 0.12) ** 2)
    pos = 0.38 * np.exp(-0.5 * ((t - 0.95) / 0.28) ** 2)
    w = -neg + pos
    w -= w[0]                     # start at zero
    w *= peak_uv / np.abs(w).max()
    return w


def pink_noise(n: int, rms: float, rng: np.random.Generator) -> np.ndarray:
    """1/f (pink) noise by spectral shaping of white noise, normalised to `rms`."""
    white = rng.standard_normal(n)
    spec = np.fft.rfft(white)
    f = np.fft.rfftfreq(n)
    f[0] = f[1]                   # avoid divide-by-zero at DC
    spec /= np.sqrt(f)
    spec[0] = 0.0
    x = np.fft.irfft(spec, n)
    return x * (rms / x.std())


# --------------------------------------------------------------------------- #
# Rendering (reference model; the firmware does the same at run time)
# --------------------------------------------------------------------------- #
def render(spike_idx: np.ndarray, amps: np.ndarray, wave_i16: np.ndarray, length: int,
           noise_rms_lsb: float, rng: np.random.Generator) -> np.ndarray:
    y = pink_noise(length, noise_rms_lsb, rng)
    wn = len(wave_i16)
    for t, a in zip(spike_idx, amps):
        end = min(t + wn, length)
        y[t:end] += (a / 128.0) * wave_i16[: end - t]
    return np.clip(np.round(y), -32768, 32767).astype(np.int16)


# --------------------------------------------------------------------------- #
# Header emission
# --------------------------------------------------------------------------- #
def _c_array(name: str, ctype: str, values: np.ndarray, per_line: int = 16) -> str:
    body = ",\n".join(
        "    " + ", ".join(str(int(v)) for v in values[i:i + per_line])
        for i in range(0, len(values), per_line)
    )
    return f"static const {ctype} {name}[{len(values)}] = {{\n{body}\n}};\n"


def emit_header(path: str, spike_idx: np.ndarray, amps: np.ndarray, wave_i16: np.ndarray,
                length: int, noise_rms_lsb: int, source: str, seed: int | None) -> None:
    hdr = textwrap.dedent(f"""\
        // GENERATED FILE -- do not edit. Regenerate with:
        //   python firmware/hil-node/tools/make_template.py
        // Generated {_dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%d %H:%MZ')}
        // Source: {source}
        //
        // Spike-train template for the HIL playback engine (see playback.h).
        // Units: samples @ TPL_RATE_HZ; amplitudes in 0.1 uV (value/10 = uV).
        #pragma once
        #include <stdint.h>

        #define TPL_RATE_HZ    {RATE_HZ}
        #define TPL_LEN        {length}      // loop length in samples ({length / RATE_HZ:.1f} s)
        #define TPL_WAVE_N     {len(wave_i16)}
        #define TPL_N_SPIKES   {len(spike_idx)}
        #define TPL_NOISE_RMS  {noise_rms_lsb}          // pink-noise RMS, 0.1 uV units
        #define TPL_MEAN_RATE_HZ {len(spike_idx) * RATE_HZ / length:.2f}f
        #define TPL_SEED       {seed if seed is not None else -1}

        // Biphasic spike waveform, 0.1 uV units, {len(wave_i16) / RATE_HZ * 1e3:.1f} ms.
        """)
    out = [hdr, _c_array("TPL_WAVE", "int16_t", wave_i16)]
    out.append("\n// Spike onset sample indices, sorted, all < TPL_LEN - TPL_WAVE_N.\n")
    out.append(_c_array("TPL_SPIKE_T", "uint32_t", spike_idx, per_line=12))
    out.append("\n// Per-spike amplitude scale, 128 == 1.0.\n")
    out.append(_c_array("TPL_SPIKE_AMP", "uint8_t", amps, per_line=24))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="\n") as f:
        f.write("".join(out))


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    default_out = os.path.join(here, "..", "include", "template.h")

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", default=default_out, help="output header path")
    ap.add_argument("--duration", type=float, default=DEFAULT_DURATION_S, help="loop length, seconds")
    ap.add_argument("--rate", type=float, default=20.0, help="placeholder mean firing rate, Hz")
    ap.add_argument("--shape", type=float, default=2.0, help="gamma shape for placeholder ISIs")
    ap.add_argument("--refractory-ms", type=float, default=2.0)
    ap.add_argument("--spike-uv", type=float, default=DEFAULT_SPIKE_UV, help="spike negative peak, uV")
    ap.add_argument("--noise-uv", type=float, default=DEFAULT_NOISE_UV, help="pink noise RMS, uV")
    ap.add_argument("--amp-jitter", type=float, default=0.15, help="per-spike amplitude sd (fraction)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--spikes", help=".npy of real spike times (seconds) -- WS-C real-data path")
    ap.add_argument("--spikes-in-samples", action="store_true", help="--spikes holds sample indices @10 kHz")
    ap.add_argument("--wave", help=".npy real spike waveform @10 kHz (float uV or int16 0.1 uV)")
    ap.add_argument("--amps", help=".npy per-spike amplitude scale (float, 1.0 = template amplitude)")
    ap.add_argument("--source", default=None, help="provenance string written into the header")
    ap.add_argument("--render", help="also write the rendered int16 waveform to this .npy")
    args = ap.parse_args(argv)

    rng = np.random.default_rng(args.seed)
    length = int(round(args.duration * RATE_HZ))

    # -- spike times --------------------------------------------------------
    if args.spikes:
        raw = np.load(args.spikes).astype(np.float64).ravel()
        idx = raw if args.spikes_in_samples else raw * RATE_HZ
        spike_idx = np.round(idx).astype(np.int64)
        spike_idx = spike_idx[(spike_idx >= 0)]
        source = args.source or f"real spike times from {os.path.basename(args.spikes)}"
    else:
        t = gamma_renewal_spike_times(args.duration, args.rate, args.shape, args.refractory_ms, rng)
        spike_idx = np.round(t * RATE_HZ).astype(np.int64)
        source = args.source or (f"PLACEHOLDER synthetic gamma-renewal (shape {args.shape}, "
                                 f"{args.rate:g} Hz, {args.refractory_ms:g} ms refractory, seed {args.seed}). "
                                 f"Run `just template` (ml/scripts/make_template.jl) to replace this with a Zenodo-derived segment.")

    # -- waveform -----------------------------------------------------------
    if args.wave:
        w = np.load(args.wave).astype(np.float64).ravel()
        wave_uv = w / UNIT_SCALE if np.issubdtype(np.load(args.wave).dtype, np.integer) else w
    else:
        wave_uv = biphasic_wave(peak_uv=args.spike_uv)
    wave_i16 = np.clip(np.round(wave_uv * UNIT_SCALE), -32768, 32767).astype(np.int16)

    # keep every spike fully inside the loop and sorted/unique
    spike_idx = np.unique(spike_idx[spike_idx < length - len(wave_i16)])

    # -- amplitudes ---------------------------------------------------------
    if args.amps:
        a = np.load(args.amps).astype(np.float64).ravel()
        if len(a) != len(spike_idx):
            print(f"warning: {len(a)} amps for {len(spike_idx)} spikes; resampling", file=sys.stderr)
            a = np.interp(np.linspace(0, 1, len(spike_idx)), np.linspace(0, 1, len(a)), a)
    else:
        a = 1.0 + args.amp_jitter * rng.standard_normal(len(spike_idx))
    amps = np.clip(np.round(a * 128), 32, 255).astype(np.uint8)

    noise_rms_lsb = int(round(args.noise_uv * UNIT_SCALE))

    emit_header(args.out, spike_idx, amps, wave_i16, length, noise_rms_lsb, source, args.seed)
    size_kb = os.path.getsize(args.out) / 1024
    print(f"wrote {args.out} ({size_kb:.1f} KB): {len(spike_idx)} spikes over {length / RATE_HZ:.1f} s "
          f"({len(spike_idx) * RATE_HZ / length:.1f} Hz), wave {len(wave_i16)} samples, "
          f"noise {noise_rms_lsb} LSB rms")
    if size_kb > 250:
        print("ERROR: header exceeds 250 KB budget", file=sys.stderr)
        return 1

    if args.render:
        y = render(spike_idx, amps, wave_i16, length, noise_rms_lsb, rng)
        np.save(args.render, y)
        print(f"rendered {len(y)} int16 samples -> {args.render}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
