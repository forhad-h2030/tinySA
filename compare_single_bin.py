#!/usr/bin/env python3
"""
Compare what set-single-bin.py generates with what the tinySA measures.

The generated frequency and level are read from set-single-bin.py (target_freq_mhz and
target_power_dbm), or given with --freq / --power. The tinySA is swept over a 500-bin grid
centered on the generated frequency, and the result is drawn on the R axis (R = -6..6 spans
the grid, R = 0 at the center), like the U(R) profile plot.

Usage (two terminals):
    1. python set-single-bin.py        # leave it at the "press Enter" prompt, RF is on
    2. python compare_single_bin.py    # measures and plots
"""

import argparse
import csv
import re
import sys
from pathlib import Path

import numpy as np

from log_tinysa_power import acquire, command, open_tinysa

HERE = Path(__file__).resolve().parent


def read_generated(script):
    """Return (freq_hz, level_dbm) from the target_* assignments in set-single-bin.py."""
    text = Path(script).read_text()
    found = {}
    for key in ("target_freq_mhz", "target_power_dbm"):
        m = re.search(rf"^\s*{key}\s*=\s*([-+0-9.eE]+)", text, re.M)
        if not m:
            sys.exit(f"Could not find {key} in {script}")
        found[key] = float(m.group(1))
    return found["target_freq_mhz"] * 1e6, found["target_power_dbm"]


def measure(port, start_hz, stop_hz, bins, rbw_hz, sweeps):
    """Average several tinySA sweeps in linear power. Returns (freqs_hz, power_mw)."""
    if sweeps < 1:
        raise RuntimeError("--sweeps must be at least 1")
    ser = open_tinysa(port)
    try:
        command(ser, "pause")
        command(ser, f"rbw {rbw_hz / 1e3:g}")
        runs = []
        for k in range(sweeps):
            pts = acquire(ser, start_hz, stop_hz, bins)
            runs.append(10 ** (np.array([p[1] for p in pts]) / 10))
            print(f"  sweep {k + 1}/{sweeps}: peak {10 * np.log10(runs[-1].max()):.1f} dBm")
        freqs = np.array([p[0] for p in pts])
        return freqs, np.mean(runs, axis=0)
    finally:
        try:
            command(ser, "resume")
        except Exception:
            pass
        ser.close()


def analyse(freqs, mw, f_gen, gen_dbm, rbw_hz):
    i = int(np.argmax(mw))
    peak_dbm = 10 * np.log10(mw[i])
    return {
        "peak_freq_hz": freqs[i],
        "peak_dbm": peak_dbm,
        "offset_khz": (freqs[i] - f_gen) / 1e3,
        "loss_db": gen_dbm - peak_dbm,                      # generator level minus measured peak
        "noise_dbm": 10 * np.log10(np.median(mw)),
        "in_window": float(mw[np.abs(freqs - f_gen) <= rbw_hz].sum() / mw.sum()),
    }


def plot(freqs, mw, f_gen, gen_dbm, rbw_hz, stats, out, show):
    import matplotlib
    if not show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fc, per_r = (freqs[0] + freqs[-1]) / 2, (freqs[-1] - freqs[0]) / 12.0
    r = (freqs - fc) / per_r
    gen = np.zeros_like(mw)
    gen[int(np.argmin(np.abs(freqs - f_gen)))] = 1.0       # all generated power in one bin
    meas = mw / mw.max()
    green, orange = "#3a7d5c", "#e08a1e"

    fig, (ax, bx) = plt.subplots(2, 1, figsize=(10, 8), gridspec_kw={"height_ratios": [3, 2]})

    ax.plot(r, gen, color=green, lw=2, label=f"generated ({f_gen / 1e6:.4f} MHz, {gen_dbm:g} dBm)")
    ax.fill_between(r, gen, 0, color=green, alpha=0.25)
    ax.plot(r, meas, color=orange, lw=1.5, ls="--", label="measured by tinySA (normalised to its peak)")
    ax.set_xlim(-6, 6)
    ax.set_xlabel("R")
    ax.set_ylabel("U(R), relative power")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper left")
    ax.text(0.98, 0.95,
            f"peak at {stats['peak_freq_hz'] / 1e6:.4f} MHz  ({stats['offset_khz']:+.2f} kHz from generated)\n"
            f"path loss: {stats['loss_db']:.1f} dB  (peak {stats['peak_dbm']:.1f} dBm)\n"
            f"noise floor: {stats['noise_dbm']:.1f} dBm,  SNR {stats['peak_dbm'] - stats['noise_dbm']:.1f} dB\n"
            f"power within +/-{rbw_hz / 1e3:g} kHz of target: {100 * stats['in_window']:.1f}%",
            transform=ax.transAxes, ha="right", va="top", fontsize=9,
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.85))

    bx.plot(r, 10 * np.log10(mw), color=orange, lw=1.2)
    bx.axvline((f_gen - fc) / per_r, color=green, ls=":", lw=1.5, label="generated frequency")
    bx.axhline(stats["noise_dbm"], color="gray", ls="--", lw=1, label="median (noise floor)")
    bx.set_xlim(-6, 6)
    bx.set_xlabel("R")
    bx.set_ylabel("measured power (dBm)")
    bx.grid(alpha=0.3)
    bx.legend(loc="upper left")

    fig.suptitle(f"Generated vs measured, {per_r / 1e3:.2f} kHz per unit R, RBW {rbw_hz / 1e3:g} kHz", y=0.995)
    fig.tight_layout()
    fig.savefig(out, dpi=200)
    print(f"Saved {out}")
    if show:
        plt.show()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--script", default=str(HERE / "set-single-bin.py"),
                   help="generator script to read target_freq_mhz / target_power_dbm from")
    p.add_argument("--freq", type=float, help="generated frequency in MHz (overrides the script)")
    p.add_argument("--power", type=float, help="generated level in dBm (overrides the script)")
    p.add_argument("--span", type=float, default=400.0, help="span in kHz (default 400)")
    p.add_argument("--bins", type=int, default=500, help="bins per sweep (default 500)")
    p.add_argument("--rbw", type=float, default=3.0, help="RBW in kHz (default 3)")
    p.add_argument("--sweeps", type=int, default=5, help="sweeps to average (default 5)")
    p.add_argument("--port", help="serial port, e.g. COM5 (default: auto-detect)")
    p.add_argument("--out", default=str(HERE / "compare_single_bin.png"), help="output PNG")
    p.add_argument("--no-show", action="store_true", help="save the PNG without opening a window")
    args = p.parse_args()

    f_gen, gen_dbm = read_generated(args.script)
    if args.freq is not None:
        f_gen = args.freq * 1e6
    if args.power is not None:
        gen_dbm = args.power
    print(f"Generated: {f_gen / 1e6:.4f} MHz at {gen_dbm:g} dBm")

    start, stop = f_gen - args.span * 1e3 / 2, f_gen + args.span * 1e3 / 2
    rbw_hz = args.rbw * 1e3
    try:
        freqs, mw = measure(args.port, start, stop, args.bins, rbw_hz, args.sweeps)
    except RuntimeError as e:
        sys.exit(str(e))

    stats = analyse(freqs, mw, f_gen, gen_dbm, rbw_hz)
    print(f"Measured peak: {stats['peak_freq_hz'] / 1e6:.4f} MHz ({stats['offset_khz']:+.2f} kHz), "
          f"{stats['peak_dbm']:.1f} dBm, path loss {stats['loss_db']:.1f} dB, "
          f"noise {stats['noise_dbm']:.1f} dBm, {100 * stats['in_window']:.1f}% of power near the target")

    csv_path = Path(args.out).with_suffix(".csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["freq_hz", "R", "measured_dbm", "measured_mw"])
        fc, per_r = (freqs[0] + freqs[-1]) / 2, (freqs[-1] - freqs[0]) / 12.0
        for fr, m in zip(freqs, mw):
            w.writerow([f"{fr:.1f}", f"{(fr - fc) / per_r:.4f}", f"{10 * np.log10(m):.3f}", f"{m:.6g}"])
    print(f"Saved {csv_path}")

    try:
        plot(freqs, mw, f_gen, gen_dbm, rbw_hz, stats, args.out, show=not args.no_show)
    except ImportError:
        sys.exit("matplotlib is needed for the plot: pip install matplotlib")


if __name__ == "__main__":
    main()
