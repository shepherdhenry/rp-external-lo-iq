"""
Reference models for the Red Pitaya external-LO IQ demodulator HLS IP.

Signal chain (identical in both models and in the HLS C++):

    adc_in: IN1 = PD signal (14-bit), IN2 = external 20 MHz LO (14-bit)
      |
      +-- NCO: 32-bit phase accumulator, 1024-entry cos/sin LUT (16-bit)
      +-- mix:   Z1 = IN1 * (cos - j sin),  Z2 = IN2 * (cos - j sin)          (30 bit)
      +-- notch: 3-tap FIR with zeros at 40 MHz (the mixing image), optional, shift-add coefficient
      +-- >> MIX_DROP  -> 23-bit filter input
      +-- LPF:   2 cascaded 1st-order IIR per complex channel, coefficient register m per channel
      |          look-ahead form so the feedback loop is DSP -> reg -> add -> reg at 125 MHz:
      |            acc[n] = acc[n-2] - ((acc[n-2] >> 12) * m >> 5) + ((x[n] + x[n-1]) * m >> 6)
      |            y[n]   = acc[n] >> 12            (a^2 = 1 - m / 2^17, a = single-sample pole)
      +-- Z = Z1 * conj(Z2 >> REF_TRUNC)   (24 x 18 bit DSP; RP NCO phase cancels here)
      +-- >> OUT_PRESHIFT, saturate 25 bit, * out_gain (Q12), saturate 18 bit
      +-- + (i_offset, q_offset), saturate 18 bit
      +-- rotate by (rot_cos, rot_sin) in Q14, saturate 18 bit
      +-- CORDIC vectoring -> |Z|, phi
      +-- out_sel mux -> saturate 14 bit -> dac_out (OUT1, OUT2)

Two implementations:
  * fixed_model():  bit-exact integer model (numpy int64), reference for the HLS testbench.
  * float_model():  vectorised float64 model (scipy lfilter), for long-duration checks.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import numpy as np
from scipy import signal

# ---------------------------------------------------------------------------
# Fixed-point architecture constants (exported to hls/iq_params.h)
# ---------------------------------------------------------------------------
FS_NOMINAL = 125e6
PHASE_BITS = 32
LUT_ADDR_BITS = 10
LUT_SIZE = 1 << LUT_ADDR_BITS
LUT_AMP = 32767                      # 16-bit signed LUT amplitude
# First-order phase-truncation correction: cos(phi+eps) ~ cos - eps*sin, sin(phi+eps) ~ sin + eps*cos.
# eps = residual phase bits [31-LUT_ADDR_BITS-1 : 31-LUT_ADDR_BITS-NCO_RES_BITS] in units of 2 pi / 2^(LUT_ADDR_BITS+NCO_RES_BITS)
# eps_q = eps * 2^NCO_EPS_FRAC = (r * NCO_EPS_MUL) >> NCO_EPS_MUL_Q
NCO_RES_BITS = 12
NCO_EPS_FRAC = 20
NCO_EPS_MUL_Q = 14
NCO_EPS_MUL = int(round(2 * np.pi / (1 << (LUT_ADDR_BITS + NCO_RES_BITS)) * (1 << NCO_EPS_FRAC) * (1 << NCO_EPS_MUL_Q)))  # 25736
ADC_BITS = 14
DAC_BITS = 14
DAC_MAX = (1 << (DAC_BITS - 1)) - 1  # 8191
DAC_MIN = -(1 << (DAC_BITS - 1))     # -8192

# Image notch: H(z) = 1 + c z^-1 + z^-2 with zeros at f_img = 2 f_LO, c = -2 cos(2 pi f_img / fs).
# c is the register notch_coef in Q14 (signed 18 bit, |c| <= 2). 20 MHz LO -> 40 MHz notch -> c = 0.85156 -> 13952.
# DC gain of the notch is (2 + c) / 2 (1.43 at 20 MHz, up to 2 for an LO near fs/4).
NOTCH_COEF_Q = 14
NOTCH_POST_SHIFT = 1
MIX_DROP = 7                         # notch output -> saturate to IIR_DATA_BITS-bit filter input

# IIR
IIR_DATA_BITS = 23                   # filter input/output width
IIR_FRAC_BITS = 12                   # fractional bits in accumulator
IIR_COEF_BITS = 17                   # m = round((1 - a^2) * 2^17), 0 < m <= 2^17
IIR_CORR_DROP = 12                   # acc >> 12 before the feedback multiply (24-bit DSP operand)
IIR_CORR_SHIFT = IIR_COEF_BITS - IIR_CORR_DROP          # 5
IIR_FF_SHIFT = IIR_COEF_BITS + 1 - IIR_FRAC_BITS        # 6   ((x+xp) * m / 2^17 / 2 * 2^12)
IIR_STAGES = 2

REF_TRUNC = 5                        # Z2: 23/24 bit -> 18 bit for the 24x18 DSP
OUT_PRESHIFT = 15                    # product (43 bit) >> 15 -> saturate 25 bit
OUT_GAIN_Q = 12                      # out_gain is Q12 (4096 = 1.0), 18-bit signed
POST_BITS = 18                       # width after gain / offset / rotation (CORDIC input)
ROT_Q = 14                           # rotation cos/sin in Q14 (16384 = 1.0)
DBG_SHIFT = 8                        # SEL_CH1/SEL_CH2: filtered channel >> 8 -> 14 bit

CORDIC_ITERS = 16
CORDIC_ANGLE_BITS = 19               # internal angle: pi == 2^17, range (-pi, pi] fits in 19 bits signed
CORDIC_PI_UNITS = 1 << (CORDIC_ANGLE_BITS - 2)
CORDIC_PHASE_SHIFT = (CORDIC_ANGLE_BITS - 2) - (DAC_BITS - 1)  # 2^17 >> 4 = 8192: pi maps to DAC full scale
CORDIC_GAIN_Q = 15
CORDIC_GAIN_INV = int(round((1 << CORDIC_GAIN_Q) / np.prod([np.sqrt(1 + 2.0 ** (-2 * i)) for i in range(CORDIC_ITERS)])))

# out_sel values
SEL_IQ = 0        # OUT1 = I, OUT2 = Q
SEL_MAG_PHASE = 1 # OUT1 = |Z|, OUT2 = phi (8192 == pi)
SEL_CH1 = 2       # OUT1 = I1, OUT2 = Q1 (after LPF, >> DBG_SHIFT)   debug
SEL_CH2 = 3       # OUT1 = I2, OUT2 = Q2                              debug
SEL_ADC = 4       # OUT1 = IN1, OUT2 = IN2 passthrough                debug


def default_ftw(f_lo: float = 20e6) -> int:
    return int(round(f_lo / FS_NOMINAL * (1 << PHASE_BITS))) & 0xFFFFFFFF


def notch_coef(f_lo: float = 20e6, fs: float = FS_NOMINAL) -> int:
    """notch_coef register for an image at 2 f_lo: round(-2 cos(2 pi 2 f_lo / fs) * 2^14)."""
    c = -2.0 * np.cos(2 * np.pi * 2 * f_lo / fs)
    return int(np.clip(round(c * (1 << NOTCH_COEF_Q)), -(1 << 17), (1 << 17) - 1))


# ---------------------------------------------------------------------------
# LPF coefficient helpers
# ---------------------------------------------------------------------------
def lpf_coef(fc_hz: float, fs: float = FS_NOMINAL) -> int:
    """Coefficient register m for a per-stage pole a = exp(-2 pi fc / fs):  m = round((1 - a^2) * 2^17)."""
    a = np.exp(-2 * np.pi * fc_hz / fs)
    m = int(round((1 - a * a) * (1 << IIR_COEF_BITS)))
    return int(np.clip(m, 1, 1 << IIR_COEF_BITS))


def lpf_pole(m: int) -> float:
    """Single-sample pole a from the coefficient register."""
    return float(np.sqrt(max(0.0, 1 - m / (1 << IIR_COEF_BITS))))


def iir_stage_cutoff_hz(m: int, fs: float = FS_NOMINAL) -> float:
    """-3 dB frequency of one stage (approximately a 1st-order IIR with pole a)."""
    a = lpf_pole(m)
    if a <= 0:
        return fs / 4
    c = (4 * a - 1 - a * a) / (2 * a)
    return fs / (2 * np.pi) * np.arccos(np.clip(c, -1, 1))


def iir_total_cutoff_hz(m: int, fs: float = FS_NOMINAL) -> float:
    """-3 dB frequency of the full 2-stage filter (numerical)."""
    f = np.logspace(1, np.log10(fs / 2), 20000)
    h = np.abs(_stage_freqz(m, f, fs)) ** IIR_STAGES
    i = np.argmax(h < 1 / np.sqrt(2))
    return float(f[i]) if i > 0 else fs / 2


def _stage_freqz(m, f, fs=FS_NOMINAL):
    a2 = 1 - m / (1 << IIR_COEF_BITS)
    z = np.exp(1j * 2 * np.pi * f / fs)
    return (1 - a2) / 2 * (1 + 1 / z) / (1 - a2 / z ** 2)


@dataclass
class Regs:
    """AXI-Lite register set of the IP (all stored as 32-bit registers)."""
    ftw: int = default_ftw()
    lpf_m_sig: int = lpf_coef(1.0e6)     # signal channel per-stage pole, default fc ~ 1 MHz
    lpf_m_ref: int = lpf_coef(80e3)      # reference channel. Narrower = less noise, but the phase of
                                         # the ref filter at the RP/LO beat (~400 Hz) then moves more when
                                         # the beat drifts: dphi/dDf ~ 2/fc_ref. 80 kHz: 0.0014 deg/Hz.
    notch_en: int = 1
    notch_coef: int = notch_coef()       # Q14, zeros at 2 f_LO (default 40 MHz)
    out_sel: int = SEL_IQ
    out_gain: int = 128                  # Q12
    i_offset: int = 0                    # 18-bit signed, in output units (before rotation)
    q_offset: int = 0
    rot_cos: int = 1 << ROT_Q
    rot_sin: int = 0

    def as_dict(self):
        return asdict(self)


# ---------------------------------------------------------------------------
# LUT (shared by both models and exported to the HLS header)
# ---------------------------------------------------------------------------
def nco_lut():
    i = np.arange(LUT_SIZE)
    ang = 2 * np.pi * i / LUT_SIZE
    cos_t = np.round(LUT_AMP * np.cos(ang)).astype(np.int64)
    sin_t = np.round(LUT_AMP * np.sin(ang)).astype(np.int64)
    return cos_t, sin_t


def write_params_header(path):
    """Export the fixed-point constants so the HLS cannot diverge from the model."""
    items = [
        ("PHASE_BITS", PHASE_BITS), ("LUT_ADDR_BITS", LUT_ADDR_BITS), ("LUT_SIZE", LUT_SIZE),
        ("NCO_RES_BITS", NCO_RES_BITS), ("NCO_EPS_FRAC", NCO_EPS_FRAC), ("NCO_EPS_MUL_Q", NCO_EPS_MUL_Q), ("NCO_EPS_MUL", NCO_EPS_MUL),
        ("NOTCH_COEF_Q", NOTCH_COEF_Q), ("NOTCH_POST_SHIFT", NOTCH_POST_SHIFT), ("MIX_DROP", MIX_DROP),
        ("IIR_DATA_BITS", IIR_DATA_BITS), ("IIR_FRAC_BITS", IIR_FRAC_BITS), ("IIR_COEF_BITS", IIR_COEF_BITS),
        ("IIR_CORR_DROP", IIR_CORR_DROP), ("IIR_CORR_SHIFT", IIR_CORR_SHIFT), ("IIR_FF_SHIFT", IIR_FF_SHIFT),
        ("REF_TRUNC", REF_TRUNC), ("OUT_PRESHIFT", OUT_PRESHIFT), ("OUT_GAIN_Q", OUT_GAIN_Q),
        ("POST_BITS", POST_BITS), ("ROT_Q", ROT_Q), ("DBG_SHIFT", DBG_SHIFT),
        ("CORDIC_ITERS", CORDIC_ITERS), ("CORDIC_ANGLE_BITS", CORDIC_ANGLE_BITS),
        ("CORDIC_PI_UNITS", CORDIC_PI_UNITS), ("CORDIC_PHASE_SHIFT", CORDIC_PHASE_SHIFT),
        ("CORDIC_GAIN_Q", CORDIC_GAIN_Q), ("CORDIC_GAIN_INV", CORDIC_GAIN_INV),
        ("DAC_BITS", DAC_BITS),
        ("SEL_IQ", SEL_IQ), ("SEL_MAG_PHASE", SEL_MAG_PHASE), ("SEL_CH1", SEL_CH1), ("SEL_CH2", SEL_CH2), ("SEL_ADC", SEL_ADC),
    ]
    atan_tab = [int(round(np.arctan(2.0 ** -j) / np.pi * CORDIC_PI_UNITS)) for j in range(CORDIC_ITERS)]
    with open(path, "w") as f:
        f.write("// Auto-generated by model/iq_model.py -- do not edit\n")
        f.write("#ifndef IQ_PARAMS_H\n#define IQ_PARAMS_H\n")
        for k, v in items:
            f.write(f"#define {k} {v}\n")
        f.write("static const int CORDIC_ATAN[CORDIC_ITERS] = {" + ", ".join(str(v) for v in atan_tab) + "};\n")
        f.write("#endif\n")


def write_lut_header(path):
    cos_t, sin_t = nco_lut()
    with open(path, "w") as f:
        f.write("// Auto-generated by model/iq_model.py -- do not edit\n")
        f.write("#ifndef NCO_LUT_H\n#define NCO_LUT_H\n")
        f.write(f"#define NCO_LUT_SIZE {LUT_SIZE}\n")
        f.write("static const short NCO_COS[NCO_LUT_SIZE] = {\n")
        for k in range(0, LUT_SIZE, 16):
            f.write("  " + ", ".join(str(v) for v in cos_t[k:k + 16]) + ",\n")
        f.write("};\nstatic const short NCO_SIN[NCO_LUT_SIZE] = {\n")
        for k in range(0, LUT_SIZE, 16):
            f.write("  " + ", ".join(str(v) for v in sin_t[k:k + 16]) + ",\n")
        f.write("};\n#endif\n")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def sat(x, bits):
    hi = (1 << (bits - 1)) - 1
    lo = -(1 << (bits - 1))
    return np.clip(x, lo, hi)


def to_signed(x, bits):
    """Interpret the low `bits` of x as a signed integer (register semantics)."""
    x = int(x) & ((1 << bits) - 1)
    return x - (1 << bits) if x & (1 << (bits - 1)) else x


# ---------------------------------------------------------------------------
# Bit-exact fixed-point model
# ---------------------------------------------------------------------------
def _notch_fixed(x: np.ndarray, coef: int) -> np.ndarray:
    x1 = np.concatenate(([0], x[:-1]))
    x2 = np.concatenate(([0, 0], x[:-2]))
    cx1 = (x1 * coef) >> NOTCH_COEF_Q
    return (x + x2 + cx1) >> NOTCH_POST_SHIFT


def _iir_stage_fixed(x: np.ndarray, m: int) -> np.ndarray:
    """
    One look-ahead 1st-order stage, bit-exact with the HLS:
        ff      = ((x[n] + x[n-1]) * m) >> IIR_FF_SHIFT
        acc[n]  = acc[n-2] - corr(acc[n-2]) + ff,   corr(a) = ((a >> IIR_CORR_DROP) * m) >> IIR_CORR_SHIFT
        y[n]    = acc[n] >> IIR_FRAC_BITS
    """
    p1 = 0        # acc[n-1]
    p2 = 0        # acc[n-2]
    corr = 0      # corr(acc[n-2]), computed one iteration earlier from p1
    xp = 0        # x[n-1]
    y = np.empty_like(x)
    xs = x.tolist()
    for n, xv in enumerate(xs):
        ff = ((xv + xp) * m) >> IIR_FF_SHIFT
        acc = p2 - corr + ff
        y[n] = acc >> IIR_FRAC_BITS
        corr = ((p1 >> IIR_CORR_DROP) * m) >> IIR_CORR_SHIFT
        p2 = p1
        p1 = acc
        xp = xv
    return y


def _iir2_fixed(x: np.ndarray, m: int) -> np.ndarray:
    for _ in range(IIR_STAGES):
        x = _iir_stage_fixed(x, m)
    return x


def _cordic_fixed(i: np.ndarray, q: np.ndarray):
    """Vectoring-mode CORDIC. Returns (gain-compensated magnitude, angle in units of pi == CORDIC_PI_UNITS)."""
    x = i.astype(np.int64)
    y = q.astype(np.int64)
    PI_UNITS = CORDIC_PI_UNITS
    neg = x < 0
    x2 = np.where(neg, -x, x)
    y2 = np.where(neg, -y, y)
    z = np.where(neg, np.where(y >= 0, PI_UNITS, -PI_UNITS), 0).astype(np.int64)
    x, y = x2, y2
    atan_tab = [int(round(np.arctan(2.0 ** -j) / np.pi * PI_UNITS)) for j in range(CORDIC_ITERS)]
    for j in range(CORDIC_ITERS):
        d = y < 0
        xs = x >> j
        ys = y >> j
        x_new = np.where(d, x - ys, x + ys)
        y_new = np.where(d, y + xs, y - xs)
        z = np.where(d, z - atan_tab[j], z + atan_tab[j])
        x, y = x_new, y_new
    mag = (x * CORDIC_GAIN_INV) >> CORDIC_GAIN_Q
    return mag, z


def fixed_model(in1: np.ndarray, in2: np.ndarray, regs: Regs, return_internals: bool = False, phase0: int = 0):
    """
    Bit-exact model. in1/in2: int arrays in [-8192, 8191]. Returns (out1, out2) int arrays in DAC range.
    phase0: NCO phase at sample 0 (0 in the HLS testbench; nonzero to study start-phase dependence).
    """
    in1 = np.asarray(in1, dtype=np.int64)
    in2 = np.asarray(in2, dtype=np.int64)
    n = len(in1)
    cos_t, sin_t = nco_lut()

    # NCO
    ftw = int(regs.ftw) & 0xFFFFFFFF
    phase = (np.uint64(phase0 & 0xFFFFFFFF) + np.arange(n, dtype=np.uint64) * np.uint64(ftw)) & np.uint64(0xFFFFFFFF)
    idx = (phase >> np.uint64(PHASE_BITS - LUT_ADDR_BITS)).astype(np.int64)
    res = ((phase >> np.uint64(PHASE_BITS - LUT_ADDR_BITS - NCO_RES_BITS)) & np.uint64((1 << NCO_RES_BITS) - 1)).astype(np.int64)
    c0 = cos_t[idx]
    s0 = sin_t[idx]
    # phase-truncation correction (removes the -60 dBc spur that folds the 40 MHz image onto baseband)
    eps = (res * NCO_EPS_MUL) >> NCO_EPS_MUL_Q
    c = c0 - ((s0 * eps) >> NCO_EPS_FRAC)
    s = s0 + ((c0 * eps) >> NCO_EPS_FRAC)

    # mix with conj(e^{j phi}) = cos - j sin
    i1 = in1 * c
    q1 = -in1 * s
    i2 = in2 * c
    q2 = -in2 * s

    # image notch (2 f_LO), then drop LSBs and saturate into the filter width
    if regs.notch_en:
        nc = to_signed(regs.notch_coef, 18)
        i1, q1, i2, q2 = _notch_fixed(i1, nc), _notch_fixed(q1, nc), _notch_fixed(i2, nc), _notch_fixed(q2, nc)
    i1, q1, i2, q2 = (sat(v >> MIX_DROP, IIR_DATA_BITS) for v in (i1, q1, i2, q2))

    # LPF
    m_sig = int(regs.lpf_m_sig)
    m_ref = int(regs.lpf_m_ref)
    i1f = _iir2_fixed(i1, m_sig)
    q1f = _iir2_fixed(q1, m_sig)
    i2f = _iir2_fixed(i2, m_ref)
    q2f = _iir2_fixed(q2, m_ref)

    # Z = Z1 * conj(Z2)
    b_i = i2f >> REF_TRUNC
    b_q = q2f >> REF_TRUNC
    zi = i1f * b_i + q1f * b_q
    zq = q1f * b_i - i1f * b_q

    g = to_signed(regs.out_gain, 18)
    vi = sat((sat(zi >> OUT_PRESHIFT, 25) * g) >> OUT_GAIN_Q, POST_BITS)
    vq = sat((sat(zq >> OUT_PRESHIFT, 25) * g) >> OUT_GAIN_Q, POST_BITS)
    vi = sat(vi + to_signed(regs.i_offset, POST_BITS), POST_BITS)
    vq = sat(vq + to_signed(regs.q_offset, POST_BITS), POST_BITS)
    rc = to_signed(regs.rot_cos, 16)
    rs = to_signed(regs.rot_sin, 16)
    ri = sat((vi * rc - vq * rs) >> ROT_Q, POST_BITS)
    rq = sat((vq * rc + vi * rs) >> ROT_Q, POST_BITS)

    mag, ang = _cordic_fixed(ri, rq)
    ph_out = ang >> CORDIC_PHASE_SHIFT

    sel = int(regs.out_sel)
    if sel == SEL_IQ:
        o1, o2 = ri, rq
    elif sel == SEL_MAG_PHASE:
        o1, o2 = mag, ph_out
    elif sel == SEL_CH1:
        o1, o2 = i1f >> DBG_SHIFT, q1f >> DBG_SHIFT
    elif sel == SEL_CH2:
        o1, o2 = i2f >> DBG_SHIFT, q2f >> DBG_SHIFT
    else:
        o1, o2 = in1, in2
    out1 = sat(o1, DAC_BITS).astype(np.int64)
    out2 = sat(o2, DAC_BITS).astype(np.int64)

    if return_internals:
        return out1, out2, dict(i1f=i1f, q1f=q1f, i2f=i2f, q2f=q2f, zi=zi, zq=zq, vi=vi, vq=vq,
                                ri=ri, rq=rq, mag=mag, ang=ang)
    return out1, out2


# ---------------------------------------------------------------------------
# Float behavioural model (fast, for long runs)
# ---------------------------------------------------------------------------
def float_model(in1, in2, regs: Regs):
    """
    Same chain in float64 (no truncation/saturation). Returns complex Z scaled like the fixed model's
    product zi + j zq (before OUT_PRESHIFT / gain), plus the filtered channels z1, z2.
    """
    in1 = np.asarray(in1, dtype=np.float64)
    in2 = np.asarray(in2, dtype=np.float64)
    n = len(in1)
    ftw = int(regs.ftw) & 0xFFFFFFFF
    phase = (np.arange(n, dtype=np.uint64) * np.uint64(ftw)) & np.uint64(0xFFFFFFFF)
    ang = phase.astype(np.float64) / 2 ** PHASE_BITS * 2 * np.pi
    lo = LUT_AMP * np.exp(-1j * ang)
    z1 = in1 * lo
    z2 = in2 * lo
    if regs.notch_en:
        b = np.array([1.0, to_signed(regs.notch_coef, 18) / (1 << NOTCH_COEF_Q), 1.0]) / (1 << NOTCH_POST_SHIFT)
        z1 = signal.lfilter(b, [1.0], z1)
        z2 = signal.lfilter(b, [1.0], z2)
    z1 = z1 / 2 ** MIX_DROP
    z2 = z2 / 2 ** MIX_DROP

    def iir2(z, m):
        a2 = 1 - m / (1 << IIR_COEF_BITS)
        b = np.array([1.0, 1.0]) * (1 - a2) / 2
        a = np.array([1.0, 0.0, -a2])
        for _ in range(IIR_STAGES):
            z = signal.lfilter(b, a, z)
        return z

    z1 = iir2(z1, int(regs.lpf_m_sig))
    z2 = iir2(z2, int(regs.lpf_m_ref)) / 2 ** REF_TRUNC
    z = z1 * np.conj(z2)
    return z, z1, z2


# ---------------------------------------------------------------------------
# Test-signal synthesis
# ---------------------------------------------------------------------------
def synth(n: int, delta_ppm: float = 20.0, drift_ppm_per_s: float = 0.0, f_lo: float = 20e6,
          a_ref: float = 0.5, a_sig: float = 0.25, phase_steps=None, am_freq: float = 0.0,
          am_depth: float = 0.0, noise_rms: float = 1e-3, xtalk_db: float = -60.0,
          ref_harm2_db: float = -50.0, seed: int = 0, dc_offset_lsb: float = 0.0):
    """
    Synthesise ADC samples as seen by the FPGA.

    The RP samples at fs_rp = 125 MHz * (1 + delta(t)); the external LO is exactly f_lo in true time.
    Returns int arrays in1, in2 (14-bit range), the complex envelope A(t) of the signal
    (so the output phase can be compared to arg A), and the sample times t.
    """
    rng = np.random.default_rng(seed)
    idx = np.arange(n, dtype=np.float64)
    delta = delta_ppm * 1e-6
    if drift_ppm_per_s:
        t_nom = idx / FS_NOMINAL
        d_t = delta + drift_ppm_per_s * 1e-6 * t_nom
        t = np.cumsum(1.0 / (FS_NOMINAL * (1 + d_t)))
        t -= t[0]
    else:
        t = idx / (FS_NOMINAL * (1 + delta))

    env_phase = np.zeros(n)
    if phase_steps:
        for (t_step, dphi) in phase_steps:
            env_phase[t >= t_step] += dphi
    env_amp = np.full(n, a_sig)
    if am_freq and am_depth:
        env_amp = a_sig * (1 + am_depth * np.sin(2 * np.pi * am_freq * t))
    A = env_amp * np.exp(1j * env_phase)

    ref = a_ref * np.cos(2 * np.pi * f_lo * t)
    if ref_harm2_db is not None and ref_harm2_db > -200:
        ref += a_ref * 10 ** (ref_harm2_db / 20) * np.cos(2 * np.pi * 2 * f_lo * t + 0.7)
    sig = np.real(A * np.exp(1j * 2 * np.pi * f_lo * t))
    sig += 10 ** (xtalk_db / 20) * ref
    sig += noise_rms * rng.standard_normal(n)
    ref += noise_rms * rng.standard_normal(n)

    def quant(x):
        v = np.round(x * (1 << (ADC_BITS - 1)) + dc_offset_lsb)
        return np.clip(v, DAC_MIN, DAC_MAX).astype(np.int64)

    return quant(sig), quant(ref), A, t
