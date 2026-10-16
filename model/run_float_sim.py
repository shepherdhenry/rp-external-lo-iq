"""
Behavioural verification of the external-LO IQ demodulator (step 1 of the plan).

Checks:
  1. Phase lock: output phase tracks arg A(t) of the signal envelope with a *constant* residual,
     independent of the RP/LO clock offset (static ppm offset and drifting offset).
  2. Step response / latency for the default filter setting.
  3. Image (40 MHz) rejection with and without the notch, for a wide LPF.
  4. Fixed-point model vs float model agreement.

Run:  python run_float_sim.py        (writes plots to model/plots/)
"""
import os
import time
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from iq_model import (Regs, synth, float_model, fixed_model, lpf_coef, iir_stage_cutoff_hz,
                      iir_total_cutoff_hz, FS_NOMINAL, OUT_PRESHIFT, OUT_GAIN_Q, SEL_IQ, SEL_MAG_PHASE)

HERE = os.path.dirname(os.path.abspath(__file__))
PLOTS = os.path.join(HERE, "plots")
os.makedirs(PLOTS, exist_ok=True)


def wrap(p):
    return (p + np.pi) % (2 * np.pi) - np.pi


def phase_lock_check(label, delta_ppm, drift_ppm_per_s, regs, n=2_500_000, dynamic=False):
    """
    dynamic=False: acceptance-test-like. Constant signal (same 20 MHz into IN1 via splitter, with
                   -60 dB crosstalk). Measures the residual phase drift (slope) vs the LO clock offset.
    dynamic=True:  phase steps + AM, measures tracking error per segment.
    """
    t0 = time.time()
    steps = [(5e-3, 1.0), (12e-3, -0.5)] if dynamic else None
    in1, in2, A, t = synth(n, delta_ppm=delta_ppm, drift_ppm_per_s=drift_ppm_per_s,
                           phase_steps=steps, am_freq=50e3 if dynamic else 0, am_depth=0.1 if dynamic else 0,
                           a_sig=0.2, seed=1)
    z, z1, z2 = float_model(in1, in2, regs)
    settle = int(2e-3 * FS_NOMINAL)
    resid = wrap(np.angle(z) - np.angle(A))
    mask = np.ones(n, bool)
    mask[:settle] = False
    if steps:
        for ts, _ in steps:
            i0 = int(ts * FS_NOMINAL)
            mask[i0:i0 + int(20e-6 * FS_NOMINAL)] = False
    r = resid[mask]
    tt = t[mask]
    slope = np.polyfit(tt, np.unwrap(r), 1)[0]  # rad/s
    beat = np.unwrap(np.angle(z2[mask]))
    beat_hz = np.polyfit(tt, beat, 1)[0] / (2 * np.pi)
    print(f"[{label}] delta={delta_ppm} ppm drift={drift_ppm_per_s} ppm/s  beat(ref vs NCO)={beat_hz:+.1f} Hz")
    print(f"          resid mean={np.degrees(r.mean()):+.4f} deg  std={np.degrees(r.std()):.4f} deg  "
          f"slope={np.degrees(slope) * 60:+.4f} deg/min  ({time.time() - t0:.1f}s)")
    if steps:
        edges = [settle] + [int(ts * FS_NOMINAL) for ts, _ in steps] + [n]
        for a, b in zip(edges[:-1], edges[1:]):
            seg = resid[a + int(20e-6 * FS_NOMINAL):b]
            print(f"          segment {a / FS_NOMINAL * 1e3:5.1f}-{b / FS_NOMINAL * 1e3:5.1f} ms: "
                  f"mean {np.degrees(seg.mean()):+.4f} deg  std {np.degrees(seg.std()):.4f} deg")

    fig, ax = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
    ax[0].plot(t * 1e3, np.degrees(np.angle(z)), lw=0.5, label="arg Z (output)")
    ax[0].plot(t * 1e3, np.degrees(np.angle(A)), lw=0.5, ls="--", label="arg A (truth)")
    ax[0].set_ylabel("deg"); ax[0].legend(loc="upper right")
    ax[1].plot(t[mask] * 1e3, np.degrees(resid[mask]), lw=0.5)
    ax[1].set_ylabel("residual (deg)")
    ax[2].plot(t * 1e3, np.abs(z) / np.abs(z[settle:]).mean(), lw=0.5, label="|Z| normalised")
    ax[2].plot(t * 1e3, np.abs(A) / np.abs(A[settle:]).mean(), lw=0.5, ls="--", label="|A| normalised")
    ax[2].set_ylabel("rel. amplitude"); ax[2].set_xlabel("time (ms)"); ax[2].legend(loc="upper right")
    fig.suptitle(f"{label}: delta={delta_ppm} ppm, drift={drift_ppm_per_s} ppm/s")
    fig.tight_layout()
    fig.savefig(os.path.join(PLOTS, f"phase_lock_{label}.png"), dpi=120)
    plt.close(fig)
    return r


def step_response(regs, n=300_000):
    in1, in2, A, t = synth(n, delta_ppm=20, phase_steps=[(1e-3, np.pi / 2)], a_sig=0.2, noise_rms=0)
    z, _, _ = float_model(in1, in2, regs)
    ph = wrap(np.angle(z))
    i0 = int(1e-3 * FS_NOMINAL)
    target = np.pi / 2
    idx90 = i0 + np.argmax(ph[i0:] > 0.9 * target)
    lat = (idx90 - i0) / FS_NOMINAL
    print(f"[step] lpf_m_sig={regs.lpf_m_sig} (stage fc~{iir_stage_cutoff_hz(regs.lpf_m_sig) / 1e3:.0f} kHz, "
          f"2-stage fc~{iir_total_cutoff_hz(regs.lpf_m_sig) / 1e3:.0f} kHz): "
          f"0-90% phase step time = {lat * 1e6:.2f} us")
    fig, ax = plt.subplots(figsize=(8, 4))
    w = slice(i0 - 200, i0 + int(5e-6 * FS_NOMINAL))
    ax.plot((t[w] - t[i0]) * 1e6, np.degrees(ph[w]))
    ax.set_xlabel("us after step"); ax.set_ylabel("arg Z (deg)")
    ax.set_title(f"90 deg phase step, lpf_m_sig={regs.lpf_m_sig}")
    fig.tight_layout(); fig.savefig(os.path.join(PLOTS, "step_response.png"), dpi=120); plt.close(fig)


def image_rejection(n=1_000_000):
    for notch in (0, 1):
        for fc in (5e6, 2.5e6, 1e6):
            m = lpf_coef(fc)
            regs = Regs(lpf_m_sig=m, notch_en=notch)
            in1, in2, A, t = synth(n, delta_ppm=20, a_sig=0.2, noise_rms=0, ref_harm2_db=-200, xtalk_db=-200)
            z, _, _ = float_model(in1, in2, regs)
            zc = z[n // 2:]
            spec = np.fft.fft(zc * np.hanning(len(zc)))
            f = np.fft.fftfreq(len(zc), 1 / FS_NOMINAL)
            p = np.abs(spec) ** 2
            dc = p[np.abs(f) < 50e3].sum()
            img = p[(np.abs(np.abs(f) - 40e6) < 50e3)].sum()
            img2 = p[(np.abs(np.abs(f) - 20e6) < 50e3)].sum()
            print(f"[image] notch={notch} fc_stage={fc / 1e6:.1f} MHz (m={m}, 2-stage fc~{iir_total_cutoff_hz(m) / 1e6:.2f} MHz): "
                  f"40 MHz image = {10 * np.log10(img / dc + 1e-30):.1f} dBc, 20 MHz = {10 * np.log10(img2 / dc + 1e-30):.1f} dBc")


def fixed_vs_float(n=200_000):
    regs = Regs(out_sel=SEL_IQ)
    in1, in2, A, t = synth(n, delta_ppm=20, phase_steps=[(0.5e-3, 1.0)], am_freq=50e3, am_depth=0.1, a_sig=0.2, seed=2)
    t0 = time.time()
    o1, o2, internals = fixed_model(in1, in2, regs, return_internals=True)
    print(f"[fixed] bit-exact model: {time.time() - t0:.1f}s for {n} samples")
    z, _, _ = float_model(in1, in2, regs)
    zf = (internals["ri"] + 1j * internals["rq"])
    zs = z / 2 ** OUT_PRESHIFT * regs.out_gain / 2 ** OUT_GAIN_Q
    settle = int(0.3e-3 * FS_NOMINAL)
    dphi = np.degrees(wrap(np.angle(zf[settle:]) - np.angle(zs[settle:])))
    dmag = (np.abs(zf[settle:]) - np.abs(zs[settle:]))
    print(f"[fixed] I/Q at DAC: |I| max {np.abs(o1).max()}, |Q| max {np.abs(o2).max()} (of 8191)")
    print(f"[fixed] fixed vs float: phase diff mean {dphi.mean():+.4f} deg, std {dphi.std():.4f} deg, "
          f"mag diff rms {dmag.std():.2f} LSB (|Z|~{np.abs(zs[settle:]).mean():.0f})")
    regs2 = Regs(out_sel=SEL_MAG_PHASE)
    m1, m2 = fixed_model(in1, in2, regs2)
    ph_true = np.degrees(np.angle(zf[settle:]))
    ph_out = m2[settle:] / 8192 * 180
    print(f"[fixed] CORDIC phase err rms {np.std(wrap(np.radians(ph_out - ph_true)) * 180 / np.pi):.4f} deg, "
          f"mag err rms {np.std(m1[settle:] - np.abs(zf[settle:])):.2f} LSB")

    fig, ax = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    ax[0].plot(t * 1e3, o1, lw=0.6, label="OUT1 = I"); ax[0].plot(t * 1e3, o2, lw=0.6, label="OUT2 = Q")
    ax[0].set_ylabel("DAC code"); ax[0].legend()
    ax[1].plot(t * 1e3, m1, lw=0.6, label="OUT1 = |Z|"); ax[1].plot(t * 1e3, m2, lw=0.6, label="OUT2 = phi (8192 = pi)")
    ax[1].set_ylabel("DAC code"); ax[1].set_xlabel("time (ms)"); ax[1].legend()
    fig.suptitle("bit-exact model outputs (phase step +1 rad at 0.5 ms, 10% AM at 50 kHz)")
    fig.tight_layout(); fig.savefig(os.path.join(PLOTS, "fixed_outputs.png"), dpi=120); plt.close(fig)


if __name__ == "__main__":
    regs = Regs()
    print(f"ftw=0x{regs.ftw:08x}  lpf_m_sig={regs.lpf_m_sig} (2-stage fc~{iir_total_cutoff_hz(regs.lpf_m_sig) / 1e3:.0f} kHz)  "
          f"lpf_m_ref={regs.lpf_m_ref} (2-stage fc~{iir_total_cutoff_hz(regs.lpf_m_ref) / 1e3:.1f} kHz)")
    phase_lock_check("static_p20ppm", 20.0, 0.0, regs)
    phase_lock_check("static_m20ppm", -20.0, 0.0, regs)
    phase_lock_check("static_0ppm", 0.0, 0.0, regs)
    phase_lock_check("drift_50ppm_in_20ms", 0.0, 2500.0, regs)   # 0 -> 50 ppm over 20 ms (very exaggerated)
    phase_lock_check("dynamic", 20.0, 0.0, regs, dynamic=True)
    phase_lock_check("dynamic_ref_eq_sig", 20.0, 0.0, Regs(lpf_m_ref=regs.lpf_m_sig), dynamic=True)
    step_response(regs)
    image_rejection()
    fixed_vs_float()
