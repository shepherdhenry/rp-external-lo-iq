"""
Generate bit-exact test vectors for the HLS testbench and the generated headers.

Writes to ../hls/tb_data/<case>_in.txt   : "in1 in2" per line (14-bit signed ADC codes)
          ../hls/tb_data/<case>_out.txt  : "out1 out2" per line (14-bit signed DAC codes)
          ../hls/tb_data/<case>_regs.txt : register values, one "name value" per line
          ../hls/nco_lut.h, ../hls/iq_params.h
"""
import os
import time
import numpy as np

from iq_model import (Regs, synth, fixed_model, write_lut_header, write_params_header, lpf_coef,
                      default_ftw, notch_coef, SEL_IQ, SEL_MAG_PHASE, SEL_CH1, SEL_CH2, SEL_ADC)

HERE = os.path.dirname(os.path.abspath(__file__))
HLS = os.path.normpath(os.path.join(HERE, "..", "hls"))
TB = os.path.join(HLS, "tb_data")
os.makedirs(TB, exist_ok=True)

N = 100_000

CASES = {
    # name: (regs, synth kwargs)
    "iq":        (Regs(out_sel=SEL_IQ),
                  dict(delta_ppm=20, phase_steps=[(0.3e-3, 1.0)], am_freq=50e3, am_depth=0.1, a_sig=0.2, seed=11)),
    "magphase":  (Regs(out_sel=SEL_MAG_PHASE),
                  dict(delta_ppm=-20, phase_steps=[(0.2e-3, 2.5), (0.5e-3, -4.0)], a_sig=0.2, seed=12)),
    "rotoff":    (Regs(out_sel=SEL_IQ, i_offset=-300, q_offset=150,
                       rot_cos=int(round(16384 * np.cos(0.6))), rot_sin=int(round(16384 * np.sin(0.6)))),
                  dict(delta_ppm=20, a_sig=0.2, seed=13)),
    "nonotch":   (Regs(out_sel=SEL_IQ, notch_en=0, lpf_m_sig=lpf_coef(300e3)),
                  dict(delta_ppm=20, a_sig=0.2, seed=14)),
    "wide":      (Regs(out_sel=SEL_IQ, lpf_m_sig=lpf_coef(5e6), lpf_m_ref=lpf_coef(5e6), out_gain=96),
                  dict(delta_ppm=20, a_sig=0.2, seed=19)),
    "narrow":    (Regs(out_sel=SEL_IQ, lpf_m_sig=lpf_coef(2e3), lpf_m_ref=lpf_coef(2e3)),
                  dict(delta_ppm=20, a_sig=0.2, seed=20)),
    "ch1":       (Regs(out_sel=SEL_CH1),
                  dict(delta_ppm=20, a_sig=0.2, seed=15)),
    "ch2":       (Regs(out_sel=SEL_CH2),
                  dict(delta_ppm=20, a_sig=0.2, seed=16)),
    "adc":       (Regs(out_sel=SEL_ADC),
                  dict(delta_ppm=20, a_sig=0.9, a_ref=0.9, seed=17)),
    "sat":       (Regs(out_sel=SEL_IQ, out_gain=4096),   # deliberately overdriven -> saturation paths
                  dict(delta_ppm=20, a_sig=0.9, seed=18)),
    "neggain":   (Regs(out_sel=SEL_MAG_PHASE, out_gain=-128),
                  dict(delta_ppm=20, a_sig=0.2, seed=21)),
    "lo30":      (Regs(out_sel=SEL_IQ, ftw=default_ftw(30e6), notch_coef=notch_coef(30e6), out_gain=66),
                  dict(delta_ppm=20, f_lo=30e6, phase_steps=[(0.4e-3, -1.2)], a_sig=0.2, seed=22)),
    "lo5":       (Regs(out_sel=SEL_IQ, ftw=default_ftw(5e6), notch_coef=notch_coef(5e6), lpf_m_sig=lpf_coef(200e3),
                       out_gain=8600),
                  dict(delta_ppm=20, f_lo=5e6, a_sig=0.2, seed=23)),
}

REG_ORDER = ["ftw", "lpf_m_sig", "lpf_m_ref", "notch_en", "notch_coef", "out_sel", "out_gain",
             "i_offset", "q_offset", "rot_cos", "rot_sin"]


def main():
    write_lut_header(os.path.join(HLS, "nco_lut.h"))
    write_params_header(os.path.join(HLS, "iq_params.h"))
    print("wrote", os.path.join(HLS, "nco_lut.h"), "and iq_params.h")
    for name, (regs, kw) in CASES.items():
        t0 = time.time()
        in1, in2, A, t = synth(N, **kw)
        o1, o2 = fixed_model(in1, in2, regs)
        np.savetxt(os.path.join(TB, f"{name}_in.txt"), np.column_stack([in1, in2]), fmt="%d")
        np.savetxt(os.path.join(TB, f"{name}_out.txt"), np.column_stack([o1, o2]), fmt="%d")
        d = regs.as_dict()
        with open(os.path.join(TB, f"{name}_regs.txt"), "w") as f:
            for k in REG_ORDER:
                f.write(f"{k} {int(d[k]) & 0xFFFFFFFF}\n")
        print(f"{name:10s} N={N} out1 range [{o1.min()}, {o1.max()}] out2 range [{o2.min()}, {o2.max()}]  ({time.time() - t0:.1f}s)")
    with open(os.path.join(TB, "cases.txt"), "w") as f:
        f.write("\n".join(CASES.keys()) + "\n")


if __name__ == "__main__":
    main()
