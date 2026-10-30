"""Example: cavity-length scan (60 Hz) of a confocal cavity, probe transmission heterodyned at
20 MHz and demodulated with iq_gate_gui.py / record_windows.py.

  python plot_scan.py            -> scan.png
Each resonance crossing gives a peak in |A| and a phase step; the scan is faster than the cavity
linewidth and the cavity is multimode (confocal), so the IQ trajectory is a ringing spiral, not a
clean circle.
"""
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
d = np.load(os.path.join(HERE, "scan_20261027.npz"))
t = d["long_t"] * 1e3                          # ms, 67 ms window, dt 4.1 us
z = (d["long_I"][0] + 1j * d["long_Q"][0]) * 1e3   # mV (iq units)
A = np.abs(z)

C1, INK, INK2, GRID, BG = "#2a78d6", "#0b0b0b", "#52514e", "#e6e5e1", "#fcfcfb"
plt.rcParams.update({"font.size": 9, "axes.grid": True, "grid.color": GRID, "axes.spines.top": False,
                     "axes.spines.right": False, "figure.facecolor": BG, "axes.facecolor": BG})
fig = plt.figure(figsize=(11, 6))
gs = fig.add_gridspec(2, 3, hspace=0.4, wspace=0.3)
a = fig.add_subplot(gs[0, :2])
a.plot(t, A, color=C1, lw=0.8)
a.set_ylabel("|A| (mV)")
a.set_title(f"cavity scan: amplitude (f_demod = {float(d['f_demod']):.4f} Hz, "
            f"quadrature_factor {float(d['quadrature_factor']):g})", loc="left", color=INK)
b = fig.add_subplot(gs[1, :2], sharex=a)
m = A > 0.1 * A.max()                          # phase only where there is light
b.plot(t[m], np.angle(z[m]), ".", ms=1.5, color=INK2)
b.set_ylim(-np.pi, np.pi)
b.set_xlabel("time (ms)")
b.set_ylabel("phase (rad)")
b.set_title("phase (where |A| > 10 % of max)", loc="left", color=INK)
c = fig.add_subplot(gs[:, 2])
inner = (t > 5) & (t < t[-1] - 5)
i = np.flatnonzero(inner)[np.argmax(A[inner])]
w = (t > t[i] - 0.12) & (t < t[i] + 0.12)
c.plot(z[w].real, z[w].imag, ".-", color=C1, lw=0.8, ms=3)
c.plot([0], [0], "+", color=INK, ms=10)
c.set_aspect("equal")
c.set_xlabel("I (mV)")
c.set_ylabel("Q (mV)")
c.set_title(f"IQ plane, ±0.12 ms around\nthe strongest crossing ({t[i]:.2f} ms)", loc="left", color=INK)
fig.savefig(os.path.join(HERE, "scan.png"), dpi=130, bbox_inches="tight")
print("saved scan.png")
