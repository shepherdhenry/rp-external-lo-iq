"""
Find the external LO frequency: sweep the NCO and record |Z| (narrow filters, so |Z| peaks only when the NCO is
within the reference-channel bandwidth of the LO).  Run on the board:
    sudo bash -lc "source /usr/local/share/pynq-venv/bin/activate; python3 sweep.py [f_start_MHz f_stop_MHz]"
"""
import sys
import time
from iq_demod_driver import IQDemod

f_start = float(sys.argv[1]) * 1e6 if len(sys.argv) > 1 else 19.5e6
f_stop = float(sys.argv[2]) * 1e6 if len(sys.argv) > 2 else 22.0e6

iq = IQDemod("iq_demod.bit", download=False)
iq.set_bandwidth(100e3, ref_fc_hz=100e3)
iq.set_gain(0.03)
iq.select("iq")
iq.set_monitor_window(16)


def measure(f, n=5):
    iq.set_frequency(f)
    time.sleep(0.05)
    mags = []
    for _ in range(n):
        mags.append(iq.monitor()["mag"])
        time.sleep(0.02)
    return sum(mags) / len(mags)


print("%11s %8s" % ("f_nco[MHz]", "|Z|"))
best = (0.0, f_start)
f = f_start
while f <= f_stop + 1:
    mag = measure(f)
    if mag > best[0]:
        best = (mag, f)
    print("%11.3f %8.0f" % (f / 1e6, mag))
    f += 100e3
print("coarse peak at %.3f MHz (|Z| = %.0f)" % (best[1] / 1e6, best[0]))

f0 = best[1]
best = (0.0, f0)
f = f0 - 150e3
while f <= f0 + 150e3 + 1:
    mag = measure(f)
    if mag > best[0]:
        best = (mag, f)
    f += 10e3
print("fine peak at %.4f MHz (|Z| = %.0f)" % (best[1] / 1e6, best[0]))
f0 = best[1]
best = (0.0, f0)
f = f0 - 15e3
while f <= f0 + 15e3 + 1:
    mag = measure(f, n=8)
    if mag > best[0]:
        best = (mag, f)
    f += 1e3
print("LO frequency ~ %.5f MHz (|Z| = %.0f)" % (best[1] / 1e6, best[0]))
iq.set_frequency(best[1])
