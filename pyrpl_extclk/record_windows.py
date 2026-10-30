"""Record back-to-back scope traces of I (iq2) and Q (iq2_2) with the same settings as
iq_gate_gui.py (continuous mode). Saves iq_gate_data/rec_<time>.npz.

  python record_windows.py [host] [n_long] [n_short] [quadrature_factor] [long_window_s]
long  traces: default 1.074 s window, dt = 65.5 us (boxcar-averaged -> ~7.6 kHz low-pass)
short traces: 33.6 ms window, dt = 2.05 us (full 78 kHz IQ bandwidth)
"""
import importlib.util
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
GUI = os.path.join(HERE, "iq_gate_gui.py")
HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.186"
N_LONG = int(sys.argv[2]) if len(sys.argv) > 2 else 5
N_SHORT = int(sys.argv[3]) if len(sys.argv) > 3 else 5
QF = float(sys.argv[4]) if len(sys.argv) > 4 else 1.5
LONG_S = float(sys.argv[5]) if len(sys.argv) > 5 else 1.07

spec = importlib.util.spec_from_file_location("iq_gate_gui", GUI)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

be = g.Backend(HOST)
bws = be.bandwidths()
bw = min(bws, key=lambda b: abs(b - 1e5))
be.configure(g.F_EXACT, bw, QF, 0.0, True)
durs = be.durations()
out = {"f_demod": be.iq.frequency, "bandwidth": be.iq.bandwidth[0],
       "quadrature_factor": be.iq.quadrature_factor}

for tag, target, n in (("long", LONG_S, N_LONG), ("short", 0.0336, N_SHORT)):
    d = be.set_window(min(durs, key=lambda x: abs(x - target)))
    I_all, Q_all, t_wall = [], [], []
    for k in range(n):
        be.arm(False)
        t0 = time.time()
        while not be.ready():
            if time.time() - t0 > d + 5:
                raise RuntimeError("scope timeout")
            time.sleep(0.01)
        t, I, Q, _ = be.read(False)
        I_all.append(I)
        Q_all.append(Q)
        t_wall.append(t0)
        print(f"{tag} #{k}: window {d*1e3:.3f} ms, |A| mean {np.abs(I+1j*Q).mean():.3f} V, "
              f"max|I|,|Q| {max(abs(I).max(), abs(Q).max()):.3f} V", flush=True)
    out[f"{tag}_t"] = t
    out[f"{tag}_I"] = np.array(I_all)
    out[f"{tag}_Q"] = np.array(Q_all)
    out[f"{tag}_wall"] = np.array(t_wall)

os.makedirs(g.SAVE_DIR, exist_ok=True)
fn = os.path.join(g.SAVE_DIR, time.strftime("rec_%Y%m%d_%H%M%S.npz"))
np.savez(fn, **out)
print("saved", fn)
