"""
Compare the C and RTL output vectors of a Vitis HLS co-simulation after aligning them.

Why this exists: the IP is ap_ctrl_none (free-running). The cosim harness streams ADC samples from
cycle 0 but needs ~50 cycles to write the AXI-Lite registers, so the RTL processes the first samples
with zeroed registers while the C model has valid registers from sample 0. The built-in post-check
therefore reports mismatches that are a harness artefact. This script finds the lag, skips the
register-settling period and reports the real agreement.

Usage: python compare_cosim.py [project_dir] [settle_samples]
       python compare_cosim.py --files <c_out.dat> <rtl_out.dat> [settle_samples]
"""
import sys
import os
import re
import numpy as np

if len(sys.argv) > 1 and sys.argv[1] == "--files":
    c_file, r_file = sys.argv[2], sys.argv[3]
    settle = int(sys.argv[4]) if len(sys.argv) > 4 else 1000
else:
    proj = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "iq_demod_prj")
    settle = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
    tv = os.path.join(proj, "sol1", "sim", "tv")
    c_file = os.path.join(tv, "cdatafile", "c.iq_demod.autotvout_dac_out_V_data_V.dat")
    r_file = os.path.join(tv, "rtldatafile", "rtl.iq_demod.autotvout_dac_out_V_data_V.dat")


def load(path):
    words = [int(m, 16) for m in re.findall(r"^0x([0-9a-fA-F]+)\s*$", open(path).read(), re.M)]
    w = np.array(words, dtype=np.int64)
    o1 = ((w & 0xFFFF) ^ 0x8000) - 0x8000
    o2 = (((w >> 16) & 0xFFFF) ^ 0x8000) - 0x8000
    return o1, o2


c1, c2 = load(c_file)
r1, r2 = load(r_file)
print(f"C samples: {len(c1)}   RTL samples: {len(r1)}")

def aligned(lag):
    """rtl[k] corresponds to c[k + lag] (lag > 0: the harness dropped leading RTL samples)."""
    if lag >= 0:
        rr1, rr2, cc1, cc2 = r1[:len(c1) - lag], r2[:len(c1) - lag], c1[lag:], c2[lag:]
    else:
        rr1, rr2, cc1, cc2 = r1[-lag:], r2[-lag:], c1[:len(r1) + lag], c2[:len(r1) + lag]
    n = min(len(rr1), len(cc1))
    return rr1[:n], rr2[:n], cc1[:n], cc2[:n]


best = None
for lag in range(-10, 60):
    rr1, rr2, cc1, cc2 = aligned(lag)
    if len(rr1) - settle <= 100:
        continue
    e = np.abs(rr1[settle:] - cc1[settle:]) + np.abs(rr2[settle:] - cc2[settle:])
    score = int(np.sum(e == 0))
    if best is None or score > best[1]:
        best = (lag, score, len(e))
lag, score, m = best
print(f"best lag: rtl[k] == c[k{lag:+d}]; {score}/{m} exact matches after sample {settle}")

rr1, rr2, cc1, cc2 = aligned(lag)
e1 = rr1[settle:] - cc1[settle:]
e2 = rr2[settle:] - cc2[settle:]
err = np.maximum(np.abs(e1), np.abs(e2))
print(f"after settling: max |err| = {err.max()}  mean |err| = {err.mean():.4f}  samples with |err|>1: {int(np.sum(err > 1))}")
full_e = np.maximum(np.abs(rr1 - cc1), np.abs(rr2 - cc2))
bad = np.where(full_e > 1)[0]
print(f"last sample with |err|>1: {bad[-1] if len(bad) else 'none'}  (of {len(full_e)})")
# is the residual a constant complex rotation / gain?  (RTL / C as complex numbers)
zc = cc1[settle:] + 1j * cc2[settle:]
zr = rr1[settle:] + 1j * rr2[settle:]
good = np.abs(zc) > 500
if np.any(good):
    ratio = zr[good] / zc[good]
    print(f"RTL/C ratio: |ratio| mean {np.abs(ratio).mean():.5f} std {np.abs(ratio).std():.5f}, "
          f"angle mean {np.degrees(np.angle(ratio).mean()):+.4f} deg std {np.degrees(np.angle(ratio).std()):.4f} deg")
ok = err.max() <= 1
print("COSIM COMPARE:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
